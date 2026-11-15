# 第 23 讲性能数据集说明（公开脱敏版）

本文件收录第 23 讲正文引用的阶段三数据口径：契约工单构成、用例池派生方式、三阶段黄金用例与静态预检结论。逐笔原始产物、云上部署记录与内部规格不在本仓库发布。

## 1 数据集版本

| 项 | 值 |
|---|---|
| 数据版本 | `performance-v1.1`，档 `reference-balanced-uniform` |
| 源数据 | 共享基准 v2.1.0：245 条工单、173 条订单、158 条物流、10 篇知识文档 |
| 随机种子 | 42 |
| 规则版本 | `agent-performance-rule-v1.1` |

v1.1 在 v1 用例基础上只做两件事：规则版本升级；Multi-Agent 四个故障场景参数差异化（物流超时、物流持续错误自愈、订单子 Agent 不可用、共享模型超时），并为每个场景补答复边界判据。

## 2 契约构成（245 条工单）

| 走法 | 工单数 | 占比 |
|---|---:|---:|
| 业务数据主路径 | 183 | 74.7% |
| 知识检索主路径 | 42 | 17.1% |
| 澄清 | 20 | 8.2% |

业务数据主路径里，物流查询一个意图占 161 条。这组比例是数据契约自带的比例，覆盖率的分母按它算。

## 3 用例池派生（每阶段 2,000 条）

用例池不是照契约意图构成复制，而是按用例类别均衡轮换派生，每个类别的用例数量大体拉平。

| 走法 | 用例数 | 占比 |
|---|---:|---:|
| 业务数据主路径 | 1,158 | 57.9% |
| 知识检索主路径 | 566 | 28.3% |
| 澄清 | 276 | 13.8% |

两组占比不能互替：契约占比是覆盖率分母；用例池占比是这一轮加压的流量构成，不是实际请求分布。业务数据主路径从契约的 74.7% 降到 57.9%，知识检索从 17.1% 升到 28.3%，澄清从 8.2% 升到 13.8%。

## 4 黄金用例与静态预检

64 条黄金用例按 v1.1 规则派生，三阶段适用 48 / 56 / 56 条。静态可达性预检把每条用例归入一类，只把「已知不可能路径」判为不可达。

| 阶段 | 黄金用例 | 静态通过 | 检索相关 | 仅行为判定 | 故障窗口 |
|---|---:|---:|---:|---:|---:|
| 阶段一 | 48 | 30 | 12 | 6 | — |
| 阶段二 | 56 | 34 | 12 | 6 | 4 |
| 阶段三 | 56 | 30 | 16 | 10 | — |

三阶段 `unreachable_total = 0`。

## 5 生成与校验命令

以下命令在案例根目录 `customer-service-ticket-system/` 执行，要求 Python 3.10+：

```bash
python3 testing/test-data/performance-v1.1/validate.py
python3 testing/test-data/performance-v1.1/generate.py \
  --profile reference --mix balanced --access uniform --pool-size 2000 --seed 42 \
  --out testing/test-data/performance-v1.1/out/reference-balanced-uniform
python3 testing/test-data/performance-v1.1/validate.py \
  --data-dir testing/test-data/performance-v1.1/out/reference-balanced-uniform
python3 testing/test-data/performance-v1.1/precheck.py
python3 testing/test-data/performance-v1.1/derive_subset.py --stage 3 --force
```

`generate.py` 生成前先跑静态可达性预检，存在不可达黄金用例即拒绝生成；生成产物写入 `out/`，不入库。

## 6 边界

- 静态预检只判断「已知不可能路径」，不代替服务逐笔回放；数据可用性以装载后回放为准。
- 文件条数不等于数据库或检索索引已装载；切档后仍需核对版本指纹与清单。
- 2,000 条请求记录不等于 2,000 个互不重复的业务问题。
- 用例池占比按用例类别均衡派生，不能当成真实请求分布。
