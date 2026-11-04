#!/usr/bin/env bash
# 阶段2 k6 执行垫片：执行逻辑见案例共享套件 k6-suite/run-scenario.sh
# 用法：tests/performance/k6/run-scenario.sh <b0|c1|s1> [k6 额外参数...]
set -euo pipefail
SHIM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SHIM_DIR}/../../../../../testing/k6-suite/run-scenario.sh" 2 "$@"
