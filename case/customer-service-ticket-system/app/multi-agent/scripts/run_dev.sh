#!/usr/bin/env bash
# 一键初始化并启动开发服务
# 运行前提：当前目录为项目根目录；已安装 python3 与 venv
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# 加载本地 .env（存在时），供 LLM_*、LANGFUSE_* 等配置生效
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
pip install -q -r requirements.txt

# 生成测试数据（幂等，写入共享基准集 ../../testing/test-data）并初始化数据库
python ../../testing/test-data/generate.py
python scripts/init_db.py

export LLM_PROVIDER="${LLM_PROVIDER:-mock}"
echo "服务启动：http://127.0.0.1:${PORT:-8000}  (docs: /docs, metrics: /metrics)"
uvicorn src.main:app --host 0.0.0.0 --port "${PORT:-8000}"