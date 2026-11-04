# 共享基准数据集 MANIFEST

三阶段（Single Agent / Multi-Agent / Workflow+RAG）共用的业务基准数据单一事实源。
跨阶段对比结论必须基于同一版本数据（课程 课程跨阶段 同任务集、同验收口径要求）。

## 版本信息

| 项 | 值 |
|---|---|
| Schema 版本 | v2.1.0 |
| 生成命令 | `python test-data/generate.py`（在 `customer-service-ticket-system/` 目录下执行） |
| 随机种子 | SEED=42（幂等，重复生成结果一致） |
| 生成日期 | 2026-09-25 |

## 数据构成

| 文件 | 规模 | 说明 |
|---|---|---|
| `tickets.json` | 245 条 | 标准 100（T）/ 复杂 40（C）/ 异常 30（E）/ 模糊 20（M）/ 并发 10（V）/ 扩展 45（X） |
| `orders.json` | 173 条 | 订单快照（工单引用的全部订单） |
| `logistics.json` | 158 条 | 物流快照（已发货订单的物流轨迹） |
| `knowledge-base/` | 10 条 | FAQ 6 / products 2 / policies 2 |
| `generate.py` | — | 幂等生成器，修改数据需求时改此文件后重新生成 |

## 工单字段字典（v2）

| 字段 | 消费阶段 | 说明 |
|---|---|---|
| `ticket_id` / `user_id` / `user_query` | 全部 | 工单标识与用户输入 |
| `order_id` | 全部 | 关联订单号；无关联订单时为空字符串 |
| `expected_intent` / `expected_tools` / `difficulty` | 阶段一 | 意图、期望工具序列、难度 |
| `expected_answer_keywords` | 全部 | **答复必须全部命中**的业务要点词（AND 语义，v1 为任一命中） |
| `expected_facts` | 全部 | 结构化事实（`order_id`/`status`/`current_location`/`estimated_delivery`/`total_amount`），判定要求每项取值出现在答复中 |
| `expected_behavior` | 全部 | 行为特征文本（如模糊工单澄清答复须含"提供订单号"），判定要求该文本出现在答复中 |
| `expected_agents` | 阶段二 | Multi-Agent 期望参与的 Agent 集合（supervisor / order_agent / logistics_agent / knowledge_agent / validation_agent） |
| `expected_path` | 阶段三 | Workflow 期望节点序列（语义节点名，实现时对齐 `Workflow 设计说明` 节点定义） |
| `expected_branch` | 阶段三 | 期望分支结果：`auto_reply` / `human_transfer` / `clarification` |

判定优先级：`expected_facts` + `expected_answer_keywords` 全部命中为"结果合格"；
两者均空的模糊工单回退 `expected_behavior` 判定（见阶段一 `tests/performance/common.py`）。

## 各阶段消费方式

- **阶段一（Single Agent）**：压测脚本 `--data` 默认指向 `test-data/tickets.json`；
  服务端种子数据由 `scripts/init_db.py` 读取本目录 orders/logistics/knowledge-base。
- **阶段二（Multi-Agent）**：适配层按 `expected_agents` 校验参与 Agent 与扇出路径。
- **阶段三（Workflow + RAG）**：适配层按 `expected_path`/`expected_branch` 校验
  分支覆盖与关键路径。

## 变更记录

- **v2.1.0（2026-09-25）**：扩展段新增 40 条知识库咨询工单（X006-X045）：
  return_policy 15 / exchange_policy 15 / contact_info 10，知识意图占比
  2/205 → 42/245，让 RAG 检索链路（阶段三 `knowledge_retrieval` 节点）在
  压测流量中获得足量覆盖。意图仅用三阶段检测器已支持的三个知识意图，
  服务端行为零改动；措辞避开 `refund_status`/`complaint` 关键词，关键词
  取自知识库语料原文（三阶段知识答复均引用命中语料原文）。**基线影响**：
  历史压测证据基于 v2.0.0（205 条），跨版本数值不可直接对比；三阶段基线
  需以 v2.1.0 重跑后更新讲次正文数值。
- **v2.0.0（2026-09-08）**：升级为三阶段共享集。新增 `order_id`、`expected_facts`、
  `expected_agents`、`expected_path`、`expected_branch` 字段；关键词语义收紧为全部命中；
  并入原阶段二 `tests/test-data/` 的 5 条扩展业务域工单（金额核对/退款咨询/退货/综合投诉，
  原文件已删除）；异常工单关键词对齐实际降级答复。
- **v1（2026-09-07 前）**：阶段一独立数据集（200 条工单），位于
  `app/single-agent/tests/test_data/`，已迁移至本目录。
