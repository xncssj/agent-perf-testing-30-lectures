// 网关认证流（k6 依赖，勿在 selfcheck/node 中导入）：登录取会话 → 每 VU 懒初始化独立会话
// → 业务请求 401/403 强制重登。服务端契约见 app/web-auth-gateway/src/web_auth/main.py：
// 登录 POST /auth/login（Origin 强校验，204 + Set-Cookie），会话查询 GET /auth/session
// （返回 username 与会话绑定 csrf_token）；业务写请求经 Nginx auth_request 校验
// Cookie + X-CSRF-Token + Origin（接口契约 §2/§3）。
// 会话语义（sessions.py）：空闲 30 分钟 / 登录后 8 小时失效——过期体现为业务请求 401，
// 由 api.js 触发 forceRelogin 兜底，不在客户端预判时钟。
// k6 每个 VU 持有独立模块实例，本文件的 state 天然按 VU 隔离（每 VU 一个网关会话）。
// Cookie 统一经 k6 params.cookies 显式覆盖传递（同名覆盖 jar），不与默认 jar 叠加重复头。
import http from 'k6/http';
import { Counter } from 'k6/metrics';
import { GATEWAY_URL, GATEWAY_ORIGIN, GATEWAY_USERNAME, GATEWAY_PASSWORD } from './config.js';
import { sessionTokenFromSetCookie, buildAuthHeaders } from './gateway-headers.js';

// 重登计数（观测项）：正常压测窗口内应保持 0；持续增长说明会话被服务端撤销（账号重置）
// 或空闲过期判定需复核
const gatewayReloginTotal = new Counter('gateway_relogin_total');

// 会话状态（每 VU 独立模块实例，互不可见；口令不入 state）
const state = { token: '', csrf: '', username: '' };

// 网关链路是否开启（GATEWAY_URL 非空即开启）
export function gatewayEnabled() {
  return GATEWAY_URL !== '';
}

// 登录并加载会话绑定 CSRF。任何一步失败即抛错（由 setup 预检放大为开跑前中止）。
function loginAndLoadSession() {
  if (!GATEWAY_URL || !GATEWAY_ORIGIN || !GATEWAY_USERNAME || !GATEWAY_PASSWORD) {
    throw new Error('网关凭据不完整：需要 GATEWAY_URL/GATEWAY_ORIGIN/GATEWAY_USERNAME/GATEWAY_PASSWORD 四项');
  }
  const res = http.post(
    GATEWAY_URL + '/auth/login',
    JSON.stringify({ username: GATEWAY_USERNAME, password: GATEWAY_PASSWORD }),
    {
      headers: { 'Content-Type': 'application/json', Origin: GATEWAY_ORIGIN },
      timeout: '10s',
      tags: { phase: 'gateway-auth' },
    },
  );
  if (res.status !== 204) {
    throw new Error('网关登录失败：HTTP ' + res.status
      + '（401=凭据错误 403=Origin/ACL 拒绝 429=登录限速 000/5xx=网关不可用）');
  }
  const setCookie = res.headers['Set-Cookie'];
  const token = sessionTokenFromSetCookie(Array.isArray(setCookie) ? setCookie.join(',') : setCookie);
  if (!token) throw new Error('网关登录响应未携带会话 Cookie（__Host-cs-session）');
  const sessionRes = http.get(GATEWAY_URL + '/auth/session', {
    cookies: { '__Host-cs-session': token },
    timeout: '10s',
    tags: { phase: 'gateway-auth' },
  });
  if (sessionRes.status !== 200) {
    throw new Error('网关会话查询失败：HTTP ' + sessionRes.status);
  }
  let body = null;
  try { body = sessionRes.json(); } catch (e) { body = null; }
  if (!body || !body.csrf_token) throw new Error('网关会话响应缺少 csrf_token');
  state.token = token;
  state.csrf = body.csrf_token;
  state.username = body.username || '';
}

// 确保有会话：无则登录（每 VU 首笔业务请求前触发一次）
export function ensureGatewaySession() {
  if (!state.token) loginAndLoadSession();
}

// 会话失效兜底：401/403 后强制重登（api.js 调用，重试一次）
export function forceGatewayRelogin() {
  gatewayReloginTotal.add(1);
  state.token = '';
  loginAndLoadSession();
}

// 业务写请求的 k6 params 片段：cookies 覆盖会话令牌，headers 携带 CSRF+Origin。
// 与 api.js 的 Content-Type/timeout/tags 合并后使用。
export function gatewayAuthParams() {
  ensureGatewaySession();
  return {
    cookies: { '__Host-cs-session': state.token },
    headers: buildAuthHeaders(state.csrf, GATEWAY_ORIGIN),
  };
}

// 场景 setup 预检：真实登录 + 会话查询，失败抛错中止整个场景（不带着坏凭据开跑）。
// 返回值随 setup_data 进摘要产物（仅 username 与网关地址，无凭据）。
export function gatewayPrecheck() {
  if (!gatewayEnabled()) return null;
  loginAndLoadSession();
  return { enabled: true, url: GATEWAY_URL, username: state.username };
}
