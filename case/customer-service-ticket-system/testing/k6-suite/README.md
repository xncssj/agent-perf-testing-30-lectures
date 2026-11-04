# k6 压测套件（公开案例包）

本目录是三阶段共用的 k6 实现，包含场景工厂、四级业务判定、看板汇总和纯 Node 自测。云端编排脚本与原始证据未包含在公开包中。

## 目录

```text
k6-suite/
├── run-scenario.sh      # 三阶段统一执行入口
├── selfcheck.mjs        # 判定与看板自测，不依赖 k6 或被测服务
├── scenarios/           # b0 / c1 / s1 / e1 / e2 / calib 场景工厂
├── stages/              # 三阶段请求体、视图与判定适配
└── lib/                 # 配置、请求、判定、分段、看板与摘要
```

## 前置

- k6
- Node.js（仅用于 `selfcheck.mjs`）
- 已启动对应阶段的 Agent 服务

## 自测

```bash
node testing/k6-suite/selfcheck.mjs
```

## 运行

在案例根目录执行，阶段号为 `1`、`2`、`3`：

```bash
bash testing/k6-suite/run-scenario.sh 1 b0 -e ITERS=2 -e WARMUP=0
bash testing/k6-suite/run-scenario.sh 1 c1
bash testing/k6-suite/run-scenario.sh 2 s1
```

常用场景：

| 场景 | 用途 |
|---|---|
| `b0` | 基准轮，工单池轮换，默认 1 VU |
| `c1` | 容量阶梯，默认 1/5/10/20/30/50 VU |
| `s1` | 长稳，默认 20 VU × 2 分钟缩短版 |
| `s1-ramp` | 连续递增长稳 |
| `e1` | 流量突增或骤降 |
| `e2` | 多轮上下文 |
| `calib` | 小样本校准，不参与门禁 |

常用环境变量：

| 变量 | 说明 |
|---|---|
| `BASE_URL` | 被测服务地址，默认按阶段取 `http://127.0.0.1:8000/8001/8002` |
| `TICKETS` | 工单集路径，默认共享 `testing/test-data/tickets.json` |
| `SLO_MS` | E2E 时延门槛，覆盖服务端 `/version` 指纹 |
| `FIRST_ANSWER_SLO_MS` | 首字门槛 |
| `COST_PER_1K` | 每千 token 成本假设 |
| `ITERS` / `WARMUP` | 小样本冒烟与预热轮数 |
| `OUT_PREFIX` | 产物前缀 |

产物写入对应阶段的 `out/`，包括逐笔采样 `.ndjson`、判定表 `-summary.html` 和看板数据 `-summary.json`。`out/` 已在 `.gitignore` 中排除。

## 边界

- 默认面向 Mock 模型，真实模型需要自行配置并重新标定 SLO。
- 同机压测会与被测服务争用 CPU 和内存，结论必须标注“同机执行”。
- 网关链路参数仍保留，但没有云端网关时不必设置 `GATEWAY_URL`。
