// 运行环境指纹抓取（k6 依赖）：场景 setup() 调用一次，结果经 data.setup_data 进入摘要产物。
// 依据：性能测试方案 §4.1 启动准则（判定自测、数据与代码指纹固定、口径确认、观测链路就位）
//       与压测前检查清单 B1/B3/B5。
// 探活请求统一带 phase=fingerprint 标签，与业务计数（business_total）隔离。
import http from 'k6/http';

const PROBE_PARAMS = {
  headers: { 'Content-Type': 'application/json' },
  timeout: '10s',
  tags: { phase: 'fingerprint' },
};

export function probeFingerprint(baseUrl) {
  const out = {
    probed_at: new Date().toISOString(),
    base_url: baseUrl,
    version: null,
    health: null,
    error: '',
    caliber: { slo_e2e_ms: null, slo_first_answer_ms: null, token_price_per_1k: null },
  };

  let res = null;
  try {
    res = http.get(baseUrl + '/version', PROBE_PARAMS);
  } catch (e) {
    res = null;
  }
  if (res && res.status === 200) {
    try {
      out.version = res.json();
      const v = out.version || {};
      // 阶段三把 SLO 与计价写入指纹；阶段一/二无这些字段（保持 null）
      if (v.slo_e2e_ms !== undefined) out.caliber.slo_e2e_ms = Number(v.slo_e2e_ms);
      if (v.slo_first_answer_ms !== undefined) out.caliber.slo_first_answer_ms = Number(v.slo_first_answer_ms);
      if (v.token_price_per_1k !== undefined) out.caliber.token_price_per_1k = Number(v.token_price_per_1k);
    } catch (e) {
      out.error += '/version 响应不是合法 JSON;';
    }
  } else {
    out.error += '/version 不可用（HTTP ' + (res ? res.status : '连接失败') + '）;';
  }

  try {
    res = http.get(baseUrl + '/health', PROBE_PARAMS);
  } catch (e) {
    res = null;
  }
  if (res && res.status === 200) {
    try {
      out.health = res.json();
    } catch (e) {
      out.health = { status: 'unparsed', body: String(res.body).slice(0, 120) };
    }
  } else {
    out.error += '/health 不可用（HTTP ' + (res ? res.status : '连接失败') + '）;';
  }

  if (out.error) {
    // 探活失败不阻断压测：指纹缺失在摘要页显式标注，由当窗记录决定是否继续
    console.warn('环境指纹探活存在异常：' + out.error);
  }
  return out;
}
