# 第 03 讲配套资料

本讲主题：测一次请求还是测完整业务，Agent 的测试对象怎么确定？

## 模板

- [四层测试对象表](templates/test-object-layers.md)
- [口径自查表](templates/latency-report-checklist.md)
- [时间口径填写区](templates/time-boundary-record.md)

## 实验记录

- [案例一实验记录（Single Agent · Mock，seed=42）](experiments/case-1-single-agent.md)：正文「三种时间不能直接做减法」一节数字与时间线的出处

## 来源与使用边界

3 份模板对应课程第 03 讲的“四层测试对象表”“架构检查矩阵”“口径自查表”和时间口径说明，提供参考定义及空白填写区。

实验中的 658、1301 毫秒是工具执行完成时刻，不是各次决策或工具耗时。客户端 20 笔流式第 90 百分位与单笔服务端事件不能拼接；历史字符间隔只作节奏校准，不标为词元平均耗时。实验记录提供历史指纹和统计读数，不包含逐笔原始数据。

跨批次复现条件见[第 05 讲模板](../05-baseline-and-fingerprint/)。

正文、讲解与案例请在专栏阅读。
