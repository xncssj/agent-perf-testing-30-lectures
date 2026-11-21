本文件收录第 25 讲正文引用的共享数据库连接腐坏轮证据导航（公开脱敏版）。

# 证据导航（run_id: 0520-20261103-v13-real23-p2-11-05）

| 文件 | 证明什么 | 与其他文件的解析关系 |
|---|---|---|
| manifest.json | 运行身份、参数、阈值、必需证据清单 | run_id 与 run.json/样本一致；thresholds 与 aggregation.md 一致 |
| samples-head.ndjson | 逐笔原始样本（前 200 行） | 运行归属经 manifest + run.json + hashes.sha256 链证明 |
| run.json | 三态门禁判定、实际窗口、证据报告 | 依据 manifest + 事件证据计算；verdict 与 failure-sample.md 一致 |
| hashes.sha256 | 运行目录全部产物哈希 | 原始产物以哈希唯一定位；本包文件不在其列（包级校验见 verify-evidence.py） |
| aggregation.md | 工具版本、入口、聚合口径 | 复现步骤；契约版本与 run.json 一致 |
| failure-sample.md | 失败/未验证证据或夹具指引 | 引用 run.json 判定与 selfcheck 夹具 |

