本文件收录第 25 讲正文引用的观测环境说明（公开脱敏版，源：ops/deploy/langfuse/README.md）。

# Langfuse 自托管观测环境

本目录是课程配套的 LLM 观测平台环境：使用 Docker 自托管 [Langfuse](https://github.com/langfuse/langfuse)，为客服工单系统三个阶段提供 Trace 关联与质量评估数据的本地采集与查看能力，数据全部留在本机。

整套环境由 6 个容器组成：

| 容器 | 职责 |
|------|------|
| langfuse-web | Web 界面与 API，查询 Trace、看板和项目设置的入口 |
| langfuse-worker | 后台任务消费者：事件入库、评估、导出 |
| postgres | 关系库：项目、用户、API Key 等业务元数据 |
| clickhouse | 列式存储：Trace/Observation 事件明细的查询引擎 |
| redis | 队列：worker 消费的中间件 |
| minio | 对象存储：原始事件与媒体文件的持久化缓冲 |

6 个容器听着重，实际都由 `docker-compose.yml` 编排好，`docker compose up -d` 一行命令启动。本机实测（macOS，Docker 29.2.0 + Compose v5.0.2，2026-09-13）：镜像已在本地时，从 `up -d` 到四个基础容器 healthy 约 20 秒，到 Web 健康检查返回 200 约 1 分半；首次安装需额外拉取约 8GB 镜像，实测约 6 分钟，耗时视网络情况而定。本文件改编自 Langfuse 官方仓库的 `docker-compose.yml`（2026-09-13 拉取，实测镜像版本 4.35.0），只做了端口参数化、密钥收敛和中文注释三类适配。

## 前置条件

- Docker 与 Docker Compose v2（`docker compose` 子命令可用）。
- 端口：langfuse-web 默认占用 `3000`，minio 控制台占用 `9091`；postgres（5432）、clickhouse（8123/9000）、redis（6379）、worker（3030）只绑定 `127.0.0.1`。
- 注意：客服工单案例的前端默认也使用 `3000`（见各阶段 README），两套环境同时启动时需要调整 `LANGFUSE_PORT`，见下文[端口冲突](#端口冲突)。

## 快速开始

以下命令默认在本目录（`case/customer-service-ticket-system/ops/deploy/langfuse/`）执行。

### 1. 环境准备

```bash
cp .env.example .env
```

预期结果：当前目录生成 `.env` 文件（已在 `.gitignore` 中排除，不会被提交）。

编辑 `.env`，把带「必改」标记的占位符替换为自己生成的密钥：

```bash
openssl rand -hex 32
```

预期结果：每次输出一串 64 位十六进制字符，填入 `NEXTAUTH_SECRET`、`SALT`、`ENCRYPTION_KEY` 及各组件密码。

### 2. 启动服务

```bash
docker compose up -d
```

预期结果：六个容器依次创建并进入 healthy 状态。用以下命令确认：

```bash
docker compose ps
```

预期结果：`postgres`、`clickhouse`、`redis`、`minio` 四个基础服务状态为 `Up (healthy)`；`langfuse-web`、`langfuse-worker` 显示 `Up`（官方未给这两个容器定义健康检查），Web 是否就绪以下方健康检查为准。worker 首次启动要等 clickhouse 建表和迁移，多等一会儿再验证。

### 3. 访问与验证

| 服务 | 地址 |
|------|------|
| Langfuse Web | http://localhost:3000 |
| MinIO 控制台 | http://localhost:9091 |

命令行健康检查：

```bash
curl -s http://localhost:3000/api/public/health
```

预期结果：返回 HTTP 200，响应体类似 `{"status":"OK","version":"4.35.0"}`（版本号随镜像更新变化）。

浏览器打开 http://localhost:3000，注册本机管理员账号（邮箱与密码仅存本机数据库）。进入项目后，在 **Settings → API Keys** 生成一对密钥：Public Key（`pk-lf-` 开头）用于上报识别，Secret Key（`sk-lf-` 开头）用于签名，SDK 接入时使用。

三个阶段的服务端均已内置 Harness 观测导出器（三阶段共享 `harness/langfuse_exporter.py`）：在服务端配置 `LANGFUSE_ENABLED=true` 与上述密钥后，每次客服业务运行会以一条 Trace 上报（模型调用为 Generation、工具尝试为 Span，终态后才批量上报，失败不影响业务），所有 Trace 统一带 `stage:<阶段>` 标签（s01/s02/s03），UI 中可按标签筛选阶段。阶段差异：阶段二每次子 Agent 调度与聚合校验各映射为一个 Span；阶段三每个 Workflow 节点执行边界映射为一个 Span。启用步骤与 Trace 内容见 [../../../app/single-agent/README.md](../../../app/single-agent/README.md) 的「Langfuse 观测」一节，各阶段差异见对应阶段 README 的「Langfuse 观测」。

## 端口冲突

客服工单案例前端默认占用 `3000`。两套环境同时启动时，在 `.env` 中把 `LANGFUSE_PORT` 改为其他端口（如 `3001`），并把 `NEXTAUTH_URL` 的端口同步改为相同值，再执行 `docker compose up -d` 重建 web 容器。

## 常见问题

- **登录提示 Invalid credentials**：本机实例没有预置账号，首次使用要在登录页切换到「注册（Sign up）」标签创建账号；该报错通常是在「登录」表单里填了尚不存在的邮箱和密码。
- **个别容器一直不是 healthy**：先看日志定位，`docker compose logs -f langfuse-worker`；首次启动的建表与迁移需要时间，等待几分钟后再确认。
- **端口被占用**：`lsof -i :3000` 找到占用进程，或改 `LANGFUSE_PORT` 与 `NEXTAUTH_URL`。
- **镜像拉取慢或失败**：镜像自 2026-09-27 起直引北京 2 ACR（`registry.cn-beijing.aliyuncs.com/csagent-pt`，钉版 tag），需先 `docker login` ACR；内网机经控制机代理出网。
- **客户端上报返回 207「Event type not accepted」**：v4 默认 `LANGFUSE_MIGRATION_V4_WRITE_MODE=events_only`，只接受 score/log 事件。配套的导出器已改用 v4 信封格式；若使用旧版 SDK/集成，可在 `.env` 追加 `LANGFUSE_MIGRATION_V4_WRITE_MODE=dual`（本目录 `docker-compose.yml` 已支持该变量传递），再 `docker compose up -d --force-recreate langfuse-web langfuse-worker` 重建生效。
- **提交成功但 Traces 页面看不到数据**：摄取是异步管道（约数秒延迟）；若长期为空，按顺序检查 `docker compose logs langfuse-worker` 是否有队列消费错误（如 Redis Socket timeout，可尝试 `docker compose restart langfuse-worker`，无效时整栈 `docker compose down && docker compose up -d` 重建，数据在卷中不会丢失）。
- **停止与清理**：`docker compose down` 停止并删除容器，数据保留在 named volume 中；`docker compose down -v` 会连同全部本地数据一起删除，执行前先确认不再需要。
