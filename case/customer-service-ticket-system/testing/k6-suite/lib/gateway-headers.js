// 网关链路纯函数（无 k6 依赖，selfcheck.mjs 直接断言）：URL 拼接 / 会话 Cookie 解析 / 认证头构造。
// 链路口径（2026-09-25 起）：云端发压业务流量经公网网关「内网回环」——k6 与网关同机（pt-control），
// 请求打 https://<控制机私网IP>:443，网关 TLS 终结 + auth_request 鉴权后反代阶段前端，前端再反代
// Agent；不经 EIP、不耗公网带宽。控制面（探活/指纹/指标采样）仍直连 Agent。
// 证书 SAN 是公网 IP 而 k6 连内网地址，TLS 主机名校验以 --insecure-skip-tls-verify 跳过
// （仅实验室内网闭环）；Origin 头仍按认证服务强校验填 https://<公网IP>。

// 阶段号 → 网关公共前缀（统一网关配置的 location）
export const STAGE_GATEWAY_PREFIX = { '1': 'single', '2': 'multi', '3': 'workflow' };

// 业务请求 URL：网关地址 + 阶段前缀 + 阶段 endpoint（endpoint 以 / 开头，rewrite 后原样到达前端）
export function gatewayBusinessUrl(gatewayUrl, stageId, endpoint) {
  const prefix = STAGE_GATEWAY_PREFIX[String(stageId)];
  if (!prefix) throw new Error('阶段 ' + stageId + ' 无网关前缀映射（仅支持 1/2/3）');
  const base = String(gatewayUrl || '').replace(/\/+$/, '');
  if (!base) throw new Error('GATEWAY_URL 为空');
  const path = String(endpoint || '');
  if (!path.startsWith('/')) throw new Error('endpoint 须以 / 开头：' + path);
  return base + '/' + prefix + path;
}

// 从 Set-Cookie 值提取会话令牌（认证服务只下发 __Host-cs-session 一个 Cookie）。
// 入参兼容单头字符串与 k6 多值数组（join 后）的形态。
export function sessionTokenFromSetCookie(setCookieValue) {
  if (!setCookieValue) return '';
  const m = /__Host-cs-session=([^;,\s]+)/.exec(String(setCookieValue));
  return m ? m[1] : '';
}

// 业务写请求认证头（Cookie 经 k6 params.cookies 显式覆盖传递，避免与 jar 同名重复）：
// 写请求经 auth_request 校验 会话绑定 CSRF + Origin（接口契约 §3）。
export function buildAuthHeaders(csrfToken, origin) {
  if (!csrfToken || !origin) {
    throw new Error('网关认证头不完整（csrf_token/origin 缺一不可）');
  }
  return { 'X-CSRF-Token': csrfToken, Origin: origin };
}
