# 客服工单系统公开案例包

本目录是《Agent 性能测试实战30讲》主线案例的公开可运行子集。案例通过三次架构迭代覆盖三种 Agent 形态：

| 阶段 | 架构 | 服务端口 | 重点 |
|---|---|---|---|
| 案例一 | Single Agent | 8000 | 单 Agent 工具选择、流式输出、四级业务判定 |
| 案例二 | Multi-Agent | 8001 | Agent 扇出、级联故障、降级与恢复 |
| 案例三 | Workflow/Flow + RAG | 8002 | 分支、关键路径、检索增强与检查点 |

## 目录

| 路径 | 说明 |
|---|---|
| `app/single-agent/` | 案例一源码、测试与本地启动脚本 |
| `app/multi-agent/` | 案例二源码、测试与本地启动脚本 |
| `app/workflow-flow-rag/` | 案例三源码、测试与本地启动脚本 |
| `app/harness/` | 三阶段共享的模型接入、工具执行、观测与护栏 |
| `app/frontend/` | 三阶段共享 Vue 3 前端 |
| `app/java-order-logistics/` | 订单与物流 Java 微服务 |
| `testing/k6-suite/` | 三阶段共用 k6 场景、判定、看板与自测 |
| `testing/test-data/` | 245 条合成工单、订单/物流快照与知识库 |
| `docker-compose.yml` | MySQL、Java 服务、三阶段 Agent 与前端编排 |

## Docker Compose 快速开始

前置：Docker 与 Docker Compose。

```bash
cp .env.example .env
docker compose up -d --build
```

启动后访问：

- 工作台：`http://127.0.0.1/`
- 案例一接口：`http://127.0.0.1:8000`
- 案例二接口：`http://127.0.0.1:8001`
- 案例三接口：`http://127.0.0.1:8002`

工作台初始账号为 `admin`，初始口令为 `AgentPerf@2026`。该口令仅用于本地演示，部署到任何共享或公网环境前必须修改。

切换案例：

```bash
docker compose --profile multi up -d --build
docker compose --profile workflow up -d --build
```

停止并清理：

```bash
docker compose down
```

默认只把端口绑定到 `127.0.0.1`。如需局域网访问，显式设置 `MYSQL_BIND_IP`、`JAVA_BIND_IP`、`AGENT_BIND_IP` 或 `FRONTEND_BIND_IP`，并自行评估暴露风险。

## 本机运行单阶段

以案例一为例，默认使用 Mock 模型，不需要 API Key。本机运行需要 Python 3.11+，Docker 镜像使用 Python 3.12：

```bash
cd app/single-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python ../../testing/test-data/generate.py
python scripts/init_db.py
LLM_PROVIDER=mock uvicorn src.main:app --host 127.0.0.1 --port 8000
```

案例二、案例三的启动方式相同，分别使用 `app/multi-agent/` 和 `app/workflow-flow-rag/`，端口为 8001、8002。案例三首次启动前可执行 `python scripts/init_db.py` 初始化检索索引。

接入真实模型时，复制对应阶段的 `.env.example` 为 `.env`，设置 `LLM_PROVIDER=openai`、`LLM_BASE_URL`、`LLM_MODEL` 与 `LLM_API_KEY`。真实模型读数与 Mock 读数不可直接比较。

## k6 压测

安装 k6 后，在案例根目录执行：

```bash
node testing/k6-suite/selfcheck.mjs
bash testing/k6-suite/run-scenario.sh 1 b0 -e ITERS=2 -e WARMUP=0
```

完整场景、环境变量与产物说明见 [testing/k6-suite/README.md](testing/k6-suite/README.md)。

## 数据与口径

- 测试数据为合成数据，生成器使用 `SEED=42`，重复生成结果一致。
- 共享基准包含 245 条工单、173 条订单、158 条物流快照与 10 篇知识文档，详见 [testing/test-data/MANIFEST.md](testing/test-data/MANIFEST.md)。
- Docker Compose 中的 MySQL 默认口令为 `change-me`，仅用于本地实验；共享或公网部署前必须替换。
- 默认 `LLM_PROVIDER=mock`，不会调用外部模型。启用真实模型后产生的费用、限流与数据外发风险由使用者自行承担。

## 边界

本公开包不包含云上部署脚本、Terraform、运维台账、原始压测证据、账单截图、内部规格包和逐轮实验记录。案例结论、分析底稿与证据边界以专栏正文和各讲配套资料为准。
