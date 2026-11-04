# Multi-Agent Python 服务 Dockerfile
# 构建上下文：客服工单系统根目录（经 docker-compose --profile multi 指定）

FROM python:3.12-slim

# 容器内复刻仓库目录层级（case/app/multi-agent），使代码中的相对深度假设
# （PROJECT_ROOT=parents[2]、_CASE_ROOT=parents[4]）与仓库布局一致
WORKDIR /opt/case/app/multi-agent

RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    && sed -i 's|deb.debian.org|mirrors.cloud.aliyuncs.com|g; s|security.debian.org|mirrors.cloud.aliyuncs.com|g' /etc/apt/sources.list /etc/apt/sources.list.d/*.sources 2>/dev/null || true \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY app/multi-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制共享 harness 包与基准测试数据集
COPY app/harness/ ./harness/
COPY testing/test-data/ /opt/case/test-data/

# 复制源码
COPY app/multi-agent/src/ ./src/

# 创建数据目录（SQLite 持久化）
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]