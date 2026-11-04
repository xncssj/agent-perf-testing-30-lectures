# 客服工单系统核心链路与架构（公开脱敏版）

> 公开脱敏版：由案例内部资料裁剪而来，已移除实例 ID、IP、账号、密钥与内部路径。

> 更新：2026-10-02，按专栏第 09 讲测试范围完成三层标注：图一蓝色圈注标业务终态出口与阶段一规划循环／工具执行，图二、图三按三档策略着色（绿＝真实压测、黄＝替身 Mock、紫＝只监控、灰＝范围外／施压与管理侧）。
> 更新：2026-10-01，按网关与业务调用代码、预压测第三轮原始摘要修正云上入口及阶段 1 Java 工具链路。

本页用四张 Mermaid 图说明三阶段被测应用的实际调用、阿里云部署和压测取证路径。图中的「阶段一／二／三」表示同一工单案例的三种实现；业务分支按工单内容选择，并非每笔工单都会查询订单、物流和知识库。

## 一、被测应用核心链路图

```mermaid
sequenceDiagram
    autonumber
    actor User as 用户
    participant Browser as 用户浏览器
    participant Gateway as 云上统一网关
    participant Front as 阶段前端 Nginx
    participant API as FastAPI
    participant Core as 阶段编排
    participant Worker as 子 Agent
    participant Model as Mock 或 OpenAI Provider
    participant Tool as 业务工具与 RAG
    participant Java as Java 订单物流服务
    participant MySQL as MySQL order_logistics
    participant Data as 本阶段数据快照

    User->>Browser: 提交客服问题
    Browser->>Gateway: POST /阶段前缀/api/v1/chat/complete 或 /stream
    Gateway->>Front: 鉴权通过，去掉阶段前缀后反代
    Front->>API: 代理 /api/ 请求
    API->>Core: 传入工单身份与问题
    alt 阶段一：Single Agent
        Note over Core,Tool: 执行路径标注：规划循环与工具执行（预算：≤10 轮决策 / ≤5 次工具 / 30 秒超时）
        rect rgb(219, 234, 254)
            Core->>Model: 决策下一步
            Model-->>Core: 工具调用或结束决策
            opt 需要订单、物流或知识信息
                Core->>Tool: 执行所选工具
                alt 订单或物流且 Java 开关开启
                    Tool->>Java: HTTP 查询订单或物流
                    Java->>MySQL: 查询 order_logistics
                    MySQL-->>Java: 返回数据
                    Java-->>Tool: 返回查询结果
                else 知识查询或 Java 开关关闭
                    Tool->>Data: 查询 Agent 数据后端
                    Data-->>Tool: 返回查询结果
                end
                Tool-->>Core: 返回工具观察
            end
        end
    else 阶段二：Multi-Agent
        Core->>Model: 规划子 Agent 任务
        Model-->>Core: 返回任务计划
        Core->>Worker: 分派所需订单、物流或知识任务
        Worker->>Tool: 执行所需业务工具
        Tool->>Data: 查询对应快照
        Data-->>Tool: 返回查询结果
        Tool-->>Worker: 返回工具结果
        Worker-->>Core: 汇总子任务结果
        Core->>Model: 校验并生成答复
    else 阶段三：Workflow / RAG
        Core->>Model: 意图识别
        Model-->>Core: 返回意图与分支
        alt 订单或物流分支
            Core->>Tool: 执行订单或物流查询节点
            Tool->>Data: 查询对应快照
            Data-->>Tool: 返回查询结果
            Tool-->>Core: 返回节点结果
        else 知识问答分支
            Core->>Tool: 执行 RAG 检索节点
            Tool->>Data: 检索本地知识语料
            Data-->>Tool: 返回命中文档
            Tool-->>Core: 返回检索结果与来源
        end
        Core->>Model: 生成答复
    end
    Core-->>API: 答复、运行终态与事件
    alt 完整响应
        API-->>Front: JSON 结果
    else 流式响应
        API-->>Front: SSE 事件与增量答复
    end
    Front-->>Gateway: 前端代理转发答复
    Gateway-->>Browser: 网关将答复返回浏览器
    Note over Browser,User: 业务路径标注：业务终态出口（合格答复 / 反问澄清 / 建议联系人工）
    rect rgb(219, 234, 254)
        Browser-->>User: 展示答复或澄清、人工处理建议
    end
```

图中「人工处理建议」是当前可证明的出口，不表示系统已经创建人工工单或完成转交。阶段 1 当前云上订单、物流查询经 Java 服务读取 MySQL `order_logistics`，知识查询仍由 Agent 数据后端提供，云上为 MySQL `cs_agent`。本地直接启动的默认配置关闭 Java、读取 SQLite；本地根 Compose 同样开启 Java 并使用 MySQL。阶段 2 读取 SQLite 快照，阶段 3 从共享测试数据装载进程内数据并执行本地 RAG 检索。本地运行可直接进入前端代理；云上 k6 业务请求经统一网关与前端 Nginx，不运行页面渲染，路径见第 4 图。图内蓝色圈注为第 09 讲测试范围标注：一处在阶段一规划循环与工具执行（执行路径），一处在业务终态出口（业务路径）。

## 二、被测应用架构图

```mermaid
flowchart LR
    User["云上用户浏览器"] --> Gateway["统一网关<br/>会话鉴权 + 阶段前缀路由"]
    CloudK6["云上 k6 业务请求<br/>默认网关内网回环"] --> Gateway
    Gateway --> Front["三阶段前端<br/>Vue 3 + Nginx API 代理"]
    Client["本地或显式直连 API 客户端"] --> API1
    Client --> API2
    Client --> API3
    Front --> API1
    Front --> API2
    Front --> API3

    subgraph App["三阶段独立应用进程"]
        API1["阶段一 FastAPI<br/>Single Agent"] --> Tool1["订单 / 物流 / 知识工具"]
        API2["阶段二 FastAPI<br/>Supervisor + Workers"] --> Tool2["订单 / 物流 / 知识工具"]
        API3["阶段三 FastAPI<br/>Workflow DAG + RAG"] --> Tool3["订单 / 物流工具 + 本地 RAG"]
    end

    subgraph Shared["公共执行能力"]
        Harness["共享 Harness<br/>身份、预算、工具执行、事件与终态"]
        Provider["Mock 默认 / OpenAI 兼容 Provider"]
    end

    subgraph Stores["各阶段数据边界"]
        Store1["阶段一：本地直接启动默认 SQLite<br/>知识与 Java 关闭时的订单物流"]
        CloudDB["云上 MySQL<br/>cs_agent / order_logistics"]
        Store2["阶段二：SQLite 快照"]
        Store3["阶段三：进程内数据与 RAG 索引<br/>检查点默认内存，可选 SQLite"]
    end

    Tool1 -->|"本地直读"| Store1
    Tool1 -->|"云上知识；Java 关闭时订单物流直读 cs_agent"| CloudDB
    Tool1 -->|"阶段 1 Java 开关开启：订单与物流 HTTP"| Java
    Tool2 --> Store2
    Tool3 --> Store3
    API1 -.-> Harness
    API2 -.-> Harness
    API3 -.-> Harness
    API1 --> Provider
    API2 --> Provider
    API3 --> Provider

    subgraph Observability["观测服务"]
        Prom["Prometheus：/metrics"]
        Lang["Langfuse：可选 Trace 上报"]
    end
    Prom -.->|"抓取 /metrics"| API1
    Prom -.-> API2
    Prom -.-> API3
    API1 -.-> Lang
    API2 -.-> Lang
    API3 -.-> Lang

    subgraph Separate["单独部署的依赖服务"]
        Java["Java 订单物流服务"]
    end
    Java -->|"查询 order_logistics"| CloudDB

    %% 三档策略着色（第 09 讲测试范围口径）：绿＝真实压测 黄＝替身（Mock） 紫＝只监控 灰＝范围外／施压入口
    classDef scopeReal fill:#F0FDF4,stroke:#16A34A
    classDef scopeStub fill:#FEF3C7,stroke:#F97316
    classDef scopeObs fill:#F3F4F6,stroke:#6B46C1
    classDef scopeOut fill:#F8FAFC,stroke:#64748B
    class Gateway,Front,API1,API2,API3,Tool1,Tool2,Tool3,Harness,Store1,Store2,Store3,CloudDB,Java scopeReal
    class Provider scopeStub
    class Prom,Lang scopeObs
    class User,Client,CloudK6 scopeOut
```

连线表示当前实现中的调用或观测关系，虚线表示共享能力或观测链路。根 Compose 对阶段 1 注入 `JAVA_SERVICE_ENABLED=true`，`AgentService` 据此注册 Java 订单、物流工具；2026-10-01 核实的 5 个窗口指纹也为 `java_service_enabled=true`。Java 使用 `order_logistics`，Agent 知识工具使用其数据后端，云上为 `cs_agent`，两者是不同数据库。阶段 2、3 的工具尚未接入该 Java 分支，不能把阶段 1 的取数链路推广到全部阶段。阶段 3 Workflow 在节点边界接入共享 Harness，不使用阶段 1 的规划循环。节点着色为第 09 讲三档策略：绿＝真实压测（接口、工具、共享执行、数据快照、链路内 Java）、黄＝替身（Mock Provider）、紫＝只监控（观测服务）、灰＝范围外／施压入口。

## 三、阿里云部署架构图

```mermaid
flowchart TB
    Admin["管理网段用户"] -->|"HTTPS 443"| ControlEIP["控制机固定 EIP"]
    Admin -->|"3000 / 3001"| ObsEIP["观测机运行期 EIP"]
    ACME["证书验证服务"] -->|"80：仅 ACME 挑战"| ControlEIP

    subgraph VPC["阿里云杭州：同可用区 VPC / vSwitch"]
        subgraph PT["pt-control · 控制机"]
            Gateway["Nginx 统一网关"] --> Auth["Web 会话鉴权服务"]
            K6["k6 与运行编排"]
            Uploader["ossutil + RAM 角色"]
        end
        subgraph SUT["sut-agent · 被测机"]
            Frontends["三阶段前端"] --> Agents["三阶段 FastAPI<br/>压测时仅目标阶段运行"]
        end
        subgraph DEP["dep-services · 依赖机"]
            Java["Java 订单物流服务"] --> Mysql["MySQL"]
        end
        subgraph OBS["obs-stack · 观测机"]
            Grafana["Grafana"] -->|"查询指标"| Prom["Prometheus"]
            Langfuse["Langfuse"]
        end
        Gateway -->|"私网反代"| Frontends
        K6 -->|"业务：同机 HTTPS 回环 <IP>:443"| Gateway
        K6 -.->|"控制面：探活、指纹、采样 8000–8002"| Agents
        Agents -->|"阶段 1：Java 开关开启时订单物流 HTTP"| Java
        Agents -->|"阶段 1：知识读取 cs_agent；Java 关闭时直读"| Mysql
        Agents -.->|"可选 Trace"| Langfuse
        Prom -.->|"抓取 /metrics"| Agents
        Prom -.->|"抓取 JVM / MySQL 指标"| DEP
        Prom -.->|"抓取控制机主机指标"| PT
        K6 -.->|"可选 remote write"| Prom
    end

    ControlEIP --> Gateway
    ObsEIP --> Grafana
    ObsEIP --> Langfuse
    Uploader -->|"证据包与回读校验"| OSS["私有 OSS Bucket"]

    %% 三档策略着色（第 09 讲测试范围口径）：绿＝真实压测 紫＝只监控 灰＝范围外／施压与管理侧；替身 Mock 在应用进程内、见图二
    classDef scopeReal fill:#F0FDF4,stroke:#16A34A
    classDef scopeObs fill:#F3F4F6,stroke:#6B46C1
    classDef scopeOut fill:#F8FAFC,stroke:#64748B
    class Gateway,Auth,Frontends,Agents,Java,Mysql scopeReal
    class Grafana,Prom,Langfuse scopeObs
    class K6,Uploader,Admin,ACME,ControlEIP,ObsEIP,OSS scopeOut
```

平时三阶段的 3 个 Agent 与 3 个前端可并行运行；标准编排测量窗暂停其他 2 个阶段，网关仅对非目标阶段前缀返回 503，目标阶段保持业务可测，窗口结束后恢复。替代运行器的隔离状态须按各窗口证据核对，不能由脚本设计推断实际已隔离。观测机 EIP 仅向管理网段开放 Langfuse 与 Grafana；被测机无公网业务入口。图中不包含尚未启用的压测平台、第三方故障桩或自研观测服务。节点着色为第 09 讲三档策略：绿＝真实压测（网关、前端代理、被测 Agent、链路内 Java 与 MySQL）、紫＝只监控（观测机三件套）、灰＝范围外／施压与管理侧。

## 四、压测架构图

```mermaid
flowchart TB
    Tickets["共享测试工单集"] --> K6

    subgraph PT["pt-control · 发压与取证"]
        Lab["lab.sh：预检、阶段隔离、运行、收集"] --> Runner["run-isolated：run_id / manifest / 真实 PID"]
        Runner --> K6["k6：B0 / C1 / S1 / E1 / E2<br/>非流式负载"]
        Runner --> Env["环境监控"]
        Runner --> Biz["业务监控"]
        Env --> Fuse["熔断控制器"]
        Biz --> Fuse
        Fuse --> Watch["熔断看护：停止真实 k6 进程"]
        Watch --> K6
        K6 --> Raw["客户端样本与退出记录"]
        Gateway["统一 Nginx 网关<br/>TLS + 会话鉴权 + 阶段路由"]
        Env --> Events["环境与业务事件"]
        Biz --> Events
        Fuse --> Events
        Runner --> Verify["verify-run：逐档汇总与三态门禁"]
        Raw --> Verify
        Events --> Verify
        Verify --> Evidence["运行摘要、哈希与证据包"]
        SSE["独立 Python stream_benchmark.py<br/>SSE TTFT / TPOT"]
    end

    subgraph SUT["sut-agent · 被测应用"]
        Front["目标阶段前端 Nginx<br/>API 代理，不执行页面渲染"]
        API["目标阶段 FastAPI"]
    end

    subgraph OBS["obs-stack · 观测"]
        Grafana["Grafana 看板"] -->|"查询指标"| Prom["Prometheus"]
        Lang["Langfuse Trace"]
    end

    K6 -->|"默认业务：<IP>:443 内网回环"| Gateway
    Gateway -->|"固定私网上游 80 / 8081 / 8082"| Front
    Front -->|"容器内 Agent :8000"| API
    SSE -.->|"独立 SSE 采集：按该脚本的目标地址"| API
    Env -.->|"/health"| API
    Biz -.->|"/metrics 终态与在途量"| API
    Prom -.->|"抓取 /metrics"| API
    API -.->|"启用时上报 Trace"| Lang
    K6 -.->|"配置后 remote write"| Prom
    Evidence -->|"collect：上传并回读哈希"| OSS["私有 OSS Bucket"]
```

k6 的云上默认业务路径为统一网关与阶段前端代理，客户端墙钟 E2E 包含这 2 跳；`BASE_URL` 仍用于控制面直连，不能凭 `manifest.target.base_url` 判断业务绕过网关。业务 URL、网关指纹与请求选择代码必须交叉核对；`GATEWAY=0` 显式直连对照须单独标注。k6 与网关同驻 `pt-control`，分机发压只证明 k6 与 Agent 进程分离，不能排除控制机资源竞争对网关的影响。

k6 记录完整响应、业务合格样本和运行级证据；环境监控还在发压机本地采样负载。`verify-run` 根据完整证据给出 `PASS`、`FAIL` 或 `NOT_VERIFIED`。Python 脚本单独测量 SSE 的 TTFT/TPOT，不从 k6 完整响应推算流式指标，也不表示它由 `run-isolated` 自动启动；服务端 `stream_mode=real` 配置标签同样不证明本轮 k6 发出了流式请求。

## 事实来源

- 三阶段共享 Harness 结构及各阶段 `src/` 实现。
- 阿里云部署方案、`<内部路径>` 与 `<内部路径>`。
- k6 共享套件及 `orchestrator/run-isolated.sh`。
- 业务请求选择、阶段路径拼接、统一网关配置、前端代理模板与阶段 1 工具注册。
- 统一网关链路核实与资料勘误：2026-10-01 原始摘要和业务 URL 的核对范围。