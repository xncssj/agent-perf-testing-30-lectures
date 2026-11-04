import { defineStore } from 'pinia'
import { reactive, ref } from 'vue'
import { completeChat, streamChat } from '@/api/chat'
import { STAGE } from '@/api/gatewayAuth'
import { fmtArgs, fmtMs, nowTime } from '@/api/http'
import { TOOL_URI } from '@/demo/demo'
import { applySnapshot, emptyPanel, evidenceKindText, stopReasonText, usageSourceText } from './panel'
import type {
  ChatMessage,
  CustomerPanelModel,
  RunResult,
  SideMetrics,
  StreamEvent,
  TimelineItem,
} from '@/types'
import { useSessionsStore } from './sessions'

const STATUS_META: Record<string, { t: string; cls: string }> = {
  completed: { t: '业务完成', cls: 'done' },
  error: { t: '运行异常', cls: 'fail' },
}

export const useChatStore = defineStore('chat', () => {
  const messages = ref<ChatMessage[]>([])
  const running = ref(false)
  const draft = ref('')
  const steps = reactive<Record<string, 'pending' | 'active' | 'done'>>({
    '1': 'active',
    '2': 'pending',
    '3': 'pending',
    '4': 'pending',
    '5': 'pending',
  })
  const sideCustomer = reactive<CustomerPanelModel>(emptyPanel())
  const sideMetrics = reactive<SideMetrics>({
    status: '—',
    stop: '—',
    e2e: '—',
    first: '—',
    token: '—',
    usageSrc: '—',
    evidence: '—',
  })

  function resetSteps() {
    steps['1'] = 'active'
    steps['2'] = 'pending'
    steps['3'] = 'pending'
    steps['4'] = 'pending'
    steps['5'] = 'pending'
  }

  function setStep(k: string, state: 'pending' | 'active' | 'done') {
    steps[k] = state
  }

  function clearMessages() {
    messages.value = []
    resetSteps()
  }

  function createAgentMsg(): ChatMessage {
    const msg: ChatMessage = {
      id: 'a-' + Date.now(),
      role: 'agent',
      time: nowTime(),
      running: true,
      statusLabel: '运行中…',
      timeline: [],
      timelineOpen: false,
      answer: '',
      streaming: false,
      tail: [],
    }
    messages.value.push(msg)
    // 返回数组内的响应式代理：改动原始对象不会触发消息区重渲染
    return messages.value[messages.value.length - 1]
  }

  function pushTl(msg: ChatMessage, item: TimelineItem) {
    msg.timeline.push(item)
    msg.timelineOpen = true
  }

  function handleAgentEvents(msg: ChatMessage, _q: string, ctx: { runId: string; ts: string }) {
    let intent: string | null = null
    let toolCalls = 0

    const onIntent = (v: string) => {
      intent = v
      msg.intent = v
      setStep('1', 'done')
      setStep('2', 'done')
      setStep('3', v === 'refund_policy' || v === 'knowledge_query' ? 'pending' : 'active')
      setStep('4', 'pending')
      setStep('5', 'pending')
    }

    const onThinking = (txt?: string) => {
      pushTl(msg, { kind: 'thinking', title: 'LLM 思考', sub: txt || '' })
    }

    const onToolCall = (tool: string, args?: Record<string, unknown>) => {
      toolCalls += 1
      const uri = TOOL_URI[tool] ? TOOL_URI[tool](args || {}) : '—'
      pushTl(msg, {
        kind: 'tool_call',
        title: `工具调用 · ${tool}`,
        uri,
        sub: 'args: ' + fmtArgs(args || {}),
      })
      if (tool === 'query_order' || tool === 'query_logistics') setStep('3', 'active')
      if (tool === 'search_knowledge') setStep('4', 'active')
    }

    const onToolResult = (
      tool: string,
      data: unknown,
      duration?: number,
      found?: boolean,
      error?: string,
    ) => {
      // stream 模式的 tool_result 事件不携带顶层 found，
      // 工具返回 data 内始终有 found 字段，故以其兜底判断命中
      const dataFound =
        data && typeof data === 'object'
          ? (data as Record<string, unknown>).found === true
          : false
      const ok = (found === undefined ? dataFound : !!found) && !error
      const icon = ok ? '✅' : error ? '⚠️' : '❌'
      const sub: string[] = []
      if (duration != null) sub.push('耗时 ' + fmtMs(duration))
      if (error) sub.push('error: ' + error)
      if (ok && tool === 'search_knowledge' && data && typeof data === 'object') {
        const d = data as Record<string, unknown>
        const docs = (d.hits || d.docs || d.results || []) as unknown[]
        sub.push('命中 ' + docs.length + ' 条')
      }
      pushTl(msg, {
        kind: 'tool_result',
        title: `${icon} 工具结果 · ${tool}`,
        sub: sub.join(' · '),
        data: fmtArgs(data),
      })
      if (ok && tool === 'query_order') {
        setStep('3', 'done')
        Object.assign(sideCustomer, applySnapshot(sideCustomer, 'order', data, ctx))
      } else if (ok && tool === 'query_logistics') {
        setStep('3', 'done')
        Object.assign(sideCustomer, applySnapshot(sideCustomer, 'logistics', data, ctx))
      } else if (ok && tool === 'search_knowledge') {
        setStep('4', 'done')
        setStep('5', 'active')
      } else if (!ok) {
        Object.assign(sideCustomer, applySnapshot(sideCustomer, 'miss', data, ctx))
      }
    }

    const onAnswer = (txt: string) => {
      msg.streaming = true
      msg.answer = (msg.answer || '') + txt
    }

    const onDone = (run: RunResult) => {
      msg.running = false
      msg.streaming = false
      const st = run.status || 'unknown'
      const meta = STATUS_META[st]
      msg.statusLabel = meta ? meta.t : st
      msg.statusCls = meta ? meta.cls : 'fail'
      const intentVal = intent || run.intent || run.trace?.intent
      if (intentVal) msg.intent = intentVal

      const m = run.metrics || {}
      msg.tail = [
        { label: '终态', value: STATUS_META[st] ? STATUS_META[st].t : stopReasonText(st) },
        { label: '停止原因', value: stopReasonText(run.stop_reason) },
        { label: 'E2E', value: fmtMs(m.e2e_ms) },
      ]
      if (m.first_answer_ms != null) {
        msg.tail.push({ label: '首片段', value: fmtMs(m.first_answer_ms) })
      }
      msg.tail.push(
        { label: 'Token', value: String(m.total_tokens ?? m.token_count ?? '—') },
        {
          label: '工具调用',
          value: String(run.trace?.tool_calls?.length ?? toolCalls),
        },
        { label: '用量来源', value: usageSourceText(String(m.usage_source || '—')) },
        { label: '证据类型', value: evidenceKindText(String(m.evidence_kind || '—')) },
      )

      sideMetrics.status = STATUS_META[st] ? STATUS_META[st].t : stopReasonText(st)
      sideMetrics.stop = stopReasonText(run.stop_reason)
      sideMetrics.e2e = fmtMs(m.e2e_ms)
      sideMetrics.first =
        m.first_answer_ms != null
          ? fmtMs(m.first_answer_ms)
          : m.ttft_ms != null
            ? '首 Token ' + fmtMs(m.ttft_ms)
            : '—'
      sideMetrics.token = String(m.total_tokens ?? m.token_count ?? '—')
      sideMetrics.usageSrc = usageSourceText(String(m.usage_source || '—'))
      sideMetrics.evidence = evidenceKindText(String(m.evidence_kind || '—'))

      setStep('2', 'done')
      setStep('3', 'done')
      setStep('4', 'done')
      setStep('5', 'done')
    }

    const feedMessage = (d: StreamEvent) => {
      if (d.type === 'thinking') onThinking(d.content as string | undefined)
      else if (d.type === 'tool_call')
        onToolCall((d.tool as string) || 'tool', d.args as Record<string, unknown>)
      else if (d.type === 'tool_result') {
        onToolResult(
          (d.tool as string) || 'tool',
          d.data,
          d.duration_ms as number | undefined,
          d.found as boolean | undefined,
          d.error as string | undefined,
        )
      } else if (d.type === 'answer') onAnswer((d.content || d.text || '') as string)
    }

    const handleComplete = (data: RunResult) => {
      const iv = data.intent || data.trace?.intent
      if (iv) onIntent(iv)
      for (const tc of data.trace?.tool_calls || []) {
        onToolCall(tc.tool || 'tool', tc.args)
        onToolResult(tc.tool || 'tool', tc.data, tc.duration_ms, tc.found, tc.error)
      }
      const answers = data.response || data.answer || ''
      if (answers && !msg.answer) onAnswer(answers)
      onDone(data)
    }

    return { onIntent, feedMessage, handleComplete, onDone }
  }

  async function sendQuery(q: string, mode: 'stream' | 'complete') {
    const text = q.trim()
    if (!text || running.value) return
    running.value = true
    resetSteps()
    messages.value.push({
      id: 'u-' + Date.now(),
      role: 'user',
      text,
      time: nowTime(),
      timeline: [],
    })
    draft.value = ''
    const agent = createAgentMsg()
    const sessions = useSessionsStore()
    const runId = sessions.current?.id?.startsWith('new-')
      ? 'fe-' + Date.now()
      : sessions.current?.id || 'fe-' + Date.now()
    const ctx = { runId, ts: nowTime() }
    const handlers = handleAgentEvents(agent, text, ctx)
    // 案例三请求模型适配（接口契约 §4）：界面输入映射为后端 query，
    // 会话关联映射为 execution_id；案例一/二仍发送 user_query
    const sessionKey = sessions.current?.id || 'web-' + Date.now()
    const taskKey = sessions.current?.id?.startsWith('new-')
      ? 'fe-' + Date.now()
      : sessions.current?.id || 'fe-' + Date.now()
    const payload =
      STAGE === 'workflow'
        ? {
            query: text,
            execution_id: sessionKey,
            task_id: taskKey,
            batch_id: 'web-' + Date.now(),
            stream: mode === 'stream',
          }
        : {
            conversation_id: sessionKey,
            task_id: taskKey,
            user_query: text,
            stream: mode === 'stream',
          }

    try {
      if (mode === 'stream') {
        await streamChat(payload, (raw) => {
          if (raw && 'type' in raw && raw.type) {
            handlers.feedMessage(raw as StreamEvent)
          } else if (raw && ('status' in raw || 'stop_reason' in raw)) {
            const data = raw as RunResult
            const iv = data.intent || data.trace?.intent
            if (iv) handlers.onIntent(iv)
            handlers.onDone(data)
          }
        })
      } else {
        const data = await completeChat(payload)
        handlers.handleComplete(data)
      }
    } catch (e) {
      handlers.onDone({
        status: 'error',
        stop_reason: e instanceof Error ? e.message : String(e),
        metrics: {},
      })
    } finally {
      running.value = false
      void useSessionsStore().refresh()
    }
  }

  return {
    messages,
    running,
    draft,
    steps,
    sideCustomer,
    sideMetrics,
    clearMessages,
    resetSteps,
    sendQuery,
  }
})
