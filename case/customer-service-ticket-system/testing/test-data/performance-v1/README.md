# Agent 性能测试数据集 v1

本目录存放独立于共享 v2.1.0 基准的合成性能数据资产。原有 245 条工单、173 条订单、158 条物流和 10 篇知识文档保持原样。这里的规模、类别比例和访问模式均为**实验设定**，没有生产流量代表性结论。生成产物写入忽略的 `out/`，不覆盖共享基准。

## 资产与判定

| 文件 | 用途 |
|---|---|
| `cases.json` | 64 条人工编写的黄金用例：八类共有用例各 6 条，Multi-Agent 专项 8 条，Workflow+RAG 专项 8 条。 |
| `source-manifest.json` | v1 源版本清单：旧基准与黄金用例的 SHA-256、默认种子、规则版本和档位倍数。 |
| `generate.py` | 从已校验用例和共享快照生成六种物理数据档、三阶段输入池及清单。 |
| `validate.py` | 核对 ID、订单与运单关系、知识引用、参考事实、适用阶段、条数和哈希。 |
| `out/<档名>/manifest.json` | 每次生成的种子、规模、类别与意图实际分布、来源及产物 SHA-256。 |

## 数据量总览

共享 v2.1.0 基准包含 245 条工单、173 条订单、158 条物流和 10 篇知识文档；本版不改写这些文件。新增的 `cases.json` 有 **64 条不同的黄金用例**：48 条三阶段共有，另有阶段二、三各自适用的专项用例 8 条。因此，生成的 `golden-stage1/2/3.json` 分别有 48/56/56 条；三个阶段的条数相加不是 160 条不同用例。

每次按一组 `profile + mix + access + seed` 生成数据时，默认得到下列参数化输入；`--pool-size` 可改变每阶段的负载池与固定对照池条数。

| 输入文件 | 每阶段默认条数 | 三阶段文件条数合计 | 用途 |
|---|---:|---:|---|
| `load-stageN.json` | 2,000 | 6,000 | 从当前物理档选取访问键，测量不同访问模式。 |
| `fixed-stageN.json` | 2,000 | 6,000 | 使用共享基准键，固定请求集以比较背景数据规模。 |

两种输入池是不同的实验对照，不能相加称为 4,000 条独立用例。它们从适用阶段的黄金用例结构生成并替换关联数据；**2,000 条请求记录不等于 2,000 种互不重复的业务问题**。不同物理档或访问模式生成的文件也不合并为一个更大的评测集。黄金用例、负载输入、固定对照输入分别记录分母。

六档物理铺底数据量如下；订单与物流是数据库记录数，知识文档单独计数。`reference` 即共享基准规模，其余档位均保留原始记录及 ID。

| 物理档 | 订单 | 物流 | 订单与物流合计 | 知识文档 |
|---|---:|---:|---:|---:|
| `reference` | 173 | 158 | 331 | 10 |
| `db10` | 1,730 | 1,580 | 3,310 | 10 |
| `kb10` | 173 | 158 | 331 | 100 |
| `db100` | 17,300 | 15,800 | 33,100 | 10 |
| `kb100` | 173 | 158 | 331 | 1,000 |
| `combined10` | 1,730 | 1,580 | 3,310 | 100 |

这些规模与访问比例均为合成实验设定，不代表生产数据或流量。运行时以对应 `out/<档名>/manifest.json` 的 `counts` 和 `per_stage` 核对实际生成条数；文件条数不能证明数据库或 Workflow 索引已装载，应按课程配套的实施方案检查运行时实况。本次仅补充数据量说明，数据资产版本仍为 v1。

逐条保留旧判定器消费的 `ticket_id`、`user_query`、`expected_intent`、`expected_tools`、`expected_answer_keywords`、`expected_facts`、`expected_agents`、`expected_path`、`expected_branch` 等字段；新增 `case_id`、`applicable_stages`、`reference`、`perf_tags` 和 `rule_version`。`reference.reference_answer` 供逐笔复核，不能逐字比对；结构化事实和明确行为仍按现有规则判定。黄金用例与负载输入分开统计，阶段一有 48 条黄金用例，阶段二、三各 56 条。

`AP-MUL-05` 至 `AP-MUL-08` 的 `fault_profile` 是**单独故障窗口的场景标记**，不会由工单输入自动触发。逐条 `reference.fault_scenario` 写有阶段二服务启动所需的故障注入环境变量、预期降级轨迹和答复边界。普通无故障运行按原 `expected_facts` 判定；故障窗口另按 `fault_scenario` 核对，受影响任务不得借正常事实预期误计为合格 Goodput。

黄金问题的输入长度档按字符数标记：短于 50 为 `short`，50—120 为 `medium`，121 及以上为 `long`；当前分别有 52/8/4 条。长度变化采用同一业务语义的补充背景，不添加无法核对的预期事实。物理档另外独立改变商品项、物流轨迹和知识文档长度。

## 生成

以下命令均在案例根目录 `customer-service-ticket-system/` 执行，要求 Python 3.10+：

```bash
python3 testing/test-data/performance-v1/validate.py
python3 testing/test-data/performance-v1/generate.py \
  --profile combined10 --mix balanced --access uniform --pool-size 2000 --seed 42 \
  --out testing/test-data/performance-v1/out/combined10-balanced-uniform
python3 testing/test-data/performance-v1/validate.py \
  --data-dir testing/test-data/performance-v1/out/combined10-balanced-uniform
```

`--profile` 可选 `reference`、`db10`、`kb10`、`db100`、`kb100`、`combined10`。订单、物流、知识文档目标条数依次为：`173/158/10`、`1730/1580/10`、`173/158/100`、`17300/15800/10`、`173/158/1000`、`1730/1580/100`。原始记录及 ID 均保留。新增订单改变商品项数；新增物流改变轨迹长度；新增知识文档改变篇幅并加入相似内容。负载池默认每阶段 2,000 条，`--pool-size` 可改。

`--mix balanced` 在当前阶段可用的用例类别间均衡轮换；`current_shape` 按旧 245 条的**意图构成**分配整数配额，实际配额写在清单中。它不是业务时段或真实用户占比。`--access uniform` 在同数据类订单内均匀抽样；`hotspot_80_20` 在每个数据类内以 80% 的抽取机会选前 20% 的键；`broad` 在每个数据类内轮换。清单记录热键实际抽取比例、不同订单数及访问量前十的键。没有订单号的知识与澄清请求不计入热键比例。

每个阶段输出三种可通过现有 `TICKETS` 读取的数组：`golden-stageN.json` 用于逐笔复核，`fixed-stageN.json` 使用共享基准键且同种子跨物理档内容相同，`load-stageN.json` 使用当前物理档选键。比较表规模时用 `fixed`；固定表规模比较冷热与广覆盖时用 `load`。生成器禁止把输出写到共享数据目录；同一来源、参数和种子应得到同样的文件哈希。

完整装载、复位、测量顺序和记录模板见课程配套的实施方案。
