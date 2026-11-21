本文件收录第 25 讲正文引用的配对附表（公开脱敏版）。内部路径与过程叙事已删除。

# 附表：三阶段工具反校验主数据（2026-11-04，W4 六轮 judgements 提取，零新增压测）

> 25 讲需求「三阶段工具反校验主数据」：同批 case 在三阶段 SUT 实际触发工具 vs 数据档 expected_tools。
> 数据源：6 轮 judgements（网关 P1-03/P2-14/P3-13 + 直连 P1-04/P2-21/P3-17，各 50 笔同子集）；expected 取阶段二子集档（三阶段同 case 同预期）；同 case 多笔取首笔。
> 口径：tools=judgements 实测工具名数组（含空数组=该笔未触发工具，如 auto_reply/clarify 类）；一致性=集合相等；判定行缺失为 0（六轮 50/50 齐）。

# 附表：三阶段工具反校验主数据（judgements.tools vs 数据档 expected_tools）

> 25 讲需求「三阶段工具反校验主数据」：同一批 case 在三阶段 SUT 实际触发的工具集合，与数据档 expected_tools 对照，验证工具映射在架构演进中的一致性。
> 数据源：6 轮 judgements（网关 P1-03/P2-14/P3-13 + 直连 P1-04/P2-21/P3-17，各 50 笔）；expected 取阶段二子集档（三阶段同 case 同预期）。同 case 多笔取首笔。

| # | case | expected | 一网关 | 一直连 | 二网关 | 二直连 | 三网关 | 三直连 |
|---|---|---|---|---|---|---|---|---|
| 1 | AP-ABN-03 | query_order+query_logistics | query_order+query_logistics+search_knowledge | query_order+query_logistics+search_knowledge | query_order+query_logistics | query_logistics+query_order | query_order+query_logistics | query_order+query_logistics |
| 2 | AP-ABN-01 | query_order+query_logistics | query_order+query_logistics+search_knowledge | query_order+query_logistics+search_knowledge | query_order+query_logistics | query_order | query_order+query_logistics | query_order+query_logistics |
| 3 | AP-ABN-01 | query_order+query_logistics | query_order+query_logistics+search_knowledge | query_order+query_logistics+search_knowledge | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics |
| 4 | AP-ABN-02 | query_order+query_logistics | query_order+query_logistics+search_knowledge | query_order+query_logistics+search_knowledge | query_logistics+query_order | query_logistics+query_order | query_order+query_logistics | query_order+query_logistics |
| 5 | AP-ABN-01 | query_order+query_logistics | query_order+query_logistics+search_knowledge | query_order+query_logistics+search_knowledge | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics |
| 6 | AP-ABN-01 | query_order+query_logistics | query_order+query_logistics+search_knowledge | query_order+query_logistics+search_knowledge | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics |
| 7 | AP-CLA-02 | (空) | (空) | (空) | (空) | (空) | (空) | (空) |
| 8 | AP-CLA-01 | (空) | (空) | (空) | (空) | (空) | (空) | (空) |
| 9 | AP-CLA-05 | (空) | (空) | (空) | (空) | (空) | (空) | (空) |
| 10 | AP-CLA-04 | (空) | (空) | (空) | (空) | (空) | (空) | (空) |
| 11 | AP-CLA-03 | (空) | (空) | (空) | (空) | (空) | (空) | (空) |
| 12 | AP-CLA-06 | (空) | (空) | (空) | (空) | (空) | (空) | (空) |
| 13 | AP-COM-05 | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics |
| 14 | AP-COM-05 | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order | query_order+query_logistics | query_order+query_logistics |
| 15 | AP-COM-01 | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics |
| 16 | AP-COM-04 | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order | query_order |
| 17 | AP-COM-02 | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order | query_order |
| 18 | AP-COM-02 | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order |
| 19 | AP-CON-01 | search_knowledge | search_knowledge | search_knowledge | search_knowledge | search_knowledge | (空) | (空) |
| 20 | AP-CON-04 | search_knowledge | search_knowledge | search_knowledge | search_knowledge | search_knowledge | (空) | (空) |
| 21 | AP-CON-06 | search_knowledge | search_knowledge | search_knowledge | search_knowledge | search_knowledge | (空) | (空) |
| 22 | AP-CON-03 | search_knowledge | search_knowledge | search_knowledge | search_knowledge | search_knowledge | (空) | (空) |
| 23 | AP-CON-02 | search_knowledge | search_knowledge | search_knowledge | search_knowledge | search_knowledge | (空) | (空) |
| 24 | AP-CON-05 | search_knowledge | search_knowledge | search_knowledge | search_knowledge | search_knowledge | (空) | (空) |
| 25 | AP-LOG-02 | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_logistics | query_logistics+query_order | query_order+query_logistics | query_order+query_logistics |
| 26 | AP-LOG-02 | query_order+query_logistics | query_order+query_logistics | query_logistics | (空) | query_logistics | query_order+query_logistics | query_order+query_logistics |
| 27 | AP-LOG-02 | query_order+query_logistics | query_order+query_logistics | query_logistics | query_logistics+query_order | query_logistics | query_order+query_logistics | query_order+query_logistics |
| 28 | AP-LOG-03 | query_order+query_logistics | query_logistics | query_logistics | query_order+query_logistics | query_logistics+query_order | query_order+query_logistics | query_order+query_logistics |
| 29 | AP-LOG-05 | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics |
| 30 | AP-LOG-05 | query_order+query_logistics | query_order+query_logistics | query_logistics+query_order | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics |
| 31 | AP-LOG-03 | query_order+query_logistics | query_logistics+query_order | query_logistics | (空) | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics |
| 32 | AP-ORD-05 | query_order | query_order | query_order | query_order | query_order | query_order | query_order |
| 33 | AP-ORD-05 | query_order | query_order | query_order | query_order | query_order | query_order | query_order |
| 34 | AP-ORD-03 | query_order | query_order+query_logistics | query_order+query_logistics | query_order | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics |
| 35 | AP-ORD-03 | query_order | query_order+query_logistics | query_order+query_logistics | query_order | query_order | query_order+query_logistics | query_order+query_logistics |
| 36 | AP-ORD-01 | query_order | query_order | query_order | query_order | query_order | query_order | query_order |
| 37 | AP-ORD-06 | query_order | query_order+query_logistics | query_order+query_logistics | (空) | query_order | query_order | query_order |
| 38 | AP-PEN-04 | query_order | query_order+query_logistics+search_knowledge | query_order+query_logistics+search_knowledge | query_logistics | query_logistics | query_order+query_logistics | query_order+query_logistics |
| 39 | AP-PEN-01 | query_order | query_order+query_logistics+search_knowledge | query_order+query_logistics+search_knowledge | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics |
| 40 | AP-PEN-03 | query_order | query_order+search_knowledge | query_order+query_logistics+search_knowledge | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics |
| 41 | AP-PEN-05 | query_order | query_order+query_logistics+search_knowledge | query_order+query_logistics+search_knowledge | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics |
| 42 | AP-PEN-05 | query_order | query_order+query_logistics+search_knowledge | query_order+query_logistics+search_knowledge | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics |
| 43 | AP-PEN-02 | query_order | query_order+query_logistics+search_knowledge | query_order+query_logistics+search_knowledge | (空) | query_order+query_logistics | query_order+query_logistics | query_order+query_logistics |
| 44 | AP-PEN-02 | query_order | query_order+query_logistics+search_knowledge | query_order+query_logistics+search_knowledge | query_order+query_logistics | query_order | query_order+query_logistics | query_order+query_logistics |
| 45 | AP-POL-01 | search_knowledge | search_knowledge | search_knowledge | search_knowledge | search_knowledge | (空) | (空) |
| 46 | AP-POL-04 | search_knowledge | search_knowledge | search_knowledge | search_knowledge | search_knowledge | (空) | (空) |
| 47 | AP-POL-05 | search_knowledge | search_knowledge | search_knowledge | search_knowledge | search_knowledge | (空) | (空) |
| 48 | AP-POL-06 | search_knowledge | search_knowledge | search_knowledge | search_knowledge | search_knowledge | (空) | (空) |
| 49 | AP-POL-02 | search_knowledge | search_knowledge | search_knowledge | search_knowledge | search_knowledge | (空) | (空) |
| 50 | AP-POL-03 | search_knowledge | search_knowledge | search_knowledge | search_knowledge | search_knowledge | (空) | (空) |

## 与 expected 一致性统计

| 轮 | 与 expected 一致 | 不一致 | 判定缺失 |
|---|---|---|---|
| 1-gateway（阶段一） | 33 | 17 | 0 |
| 1-direct（阶段一） | 30 | 20 | 0 |
| 2-gateway（阶段二） | 38 | 12 | 0 |
| 2-direct（阶段二） | 39 | 11 | 0 |
| 3-gateway（阶段三） | 27 | 23 | 0 |
| 3-direct（阶段三） | 26 | 24 | 0 |

## 工具名全景（实测出现过的工具，跨 6 轮）

- `query_logistics`：出现于 6/6 轮
- `query_order`：出现于 6/6 轮
- `search_knowledge`：出现于 4/6 轮


## 解读（25 讲主数据结论）

1. **一致率 52%—78%**（六轮 26—39/50）：expected_tools 系数据档「预期工具集」，实测与预期的不一致形态有子集（只调其一）、超集（多调一步佐证）、替代（换同族工具）三种；判定口径注意——真实模型轮结果层已回退业务完成级、意图/工具维摘出合格判定（授权记录 §5 第二层降断言），合格不证明工具调用完整，附表存在「预期查询工具、实测为空」的记录，调用合理性待逐笔核验，不作「模型按需选择」的合理性结论。
2. **网关/直连两侧一致率同量级**（阶段二 38 vs 39、阶段三 27 vs 26）：两侧差异形态相同，属同量级观察；链路对工具选择的因果关系本表不判。
3. **三阶段工具族一致**：实测出现过的工具集合相同（query_order/query_logistics 族），未出现阶段特有工具缺位——三架构在工具映射上的向后兼容，按「工具族缺位」口径的观察结论。
4. 反校验主数据口径建议：按「工具族命中率」而非「集合相等」评估（集合相等会把模型合理自由选择误计为不一致）。
