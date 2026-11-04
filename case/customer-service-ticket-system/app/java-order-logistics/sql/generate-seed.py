"""从共享基准数据集生成 Java 微服务侧种子 SQL（seed-mysql.sql）。

数据单一事实源是 case 根下 testing/test-data/（见其 MANIFEST.md）：
本脚本只做「JSON 快照 → MySQL INSERT」的形态转换，不引入第二份数据。
test-data 重新生成后须重跑本脚本并复核 seed-mysql.sql 已随数据更新。

用法（在 app/java-order-logistics/ 目录下）：
    python3 sql/generate-seed.py

产物随 docker compose 的 mysql initdb 挂载（docker-entrypoint-initdb.d/02-seed.sql），
仅在新数据卷首次初始化时执行；已有数据卷须手工执行一次：
    docker exec -i cs-agent-mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" order_logistics < app/java-order-logistics/sql/seed-mysql.sql

INSERT 语法保持 MySQL 与 SQLite 双兼容（单引号 '' 转义、无方言函数），
便于本地 SQLite 模式的 Java 服务复用同一份种子。例外：文件头部的
SET NAMES utf8mb4 是 MySQL 专用——initdb 的 mysql 客户端默认按 latin1
解读 SQL 文件（2026-09-25 实测：不加时中文被二次编码入库，Java 读出乱码），
SQLite 手工导入时跳过该行即可。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# sql/ → java-order-logistics → app → customer-service-ticket-system（case 根）
CASE_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = CASE_ROOT / "testing" / "test-data"
OUT_PATH = Path(__file__).resolve().parent / "seed-mysql.sql"

# 列序与 java-order-logistics/sql/schema-mysql.sql 及 testing/test-data 的 JSON 键一致
ORDERS_COLUMNS = (
    "order_id", "user_id", "status", "items", "total_amount",
    "shipping_address", "tracking_no", "carrier", "create_time", "update_time",
)
LOGISTICS_COLUMNS = (
    "tracking_no", "order_id", "carrier", "status", "current_location",
    "origin", "destination", "trajectory", "estimated_delivery",
)
# JSON 文本列：Python 侧对象（list/dict）序列化为紧凑 JSON 字符串入库
JSON_COLUMNS = {"items", "shipping_address", "trajectory"}


def sql_literal(column: str, value) -> str:
    if value is None:
        return "NULL"
    if column in JSON_COLUMNS or not isinstance(value, (int, float)):
        if column in JSON_COLUMNS:
            value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        # 单引号按 SQL 标准用两个单引号转义（MySQL/SQLite 双兼容）
        return "'" + str(value).replace("'", "''") + "'"
    return str(value)


def insert_statements(table: str, columns: tuple[str, ...], rows: list[dict]) -> list[str]:
    stmts = []
    for row in rows:
        values = ", ".join(sql_literal(col, row.get(col)) for col in columns)
        stmts.append(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({values});")
    return stmts


def generate(data_dir: Path = DATA_DIR, out_path: Path = OUT_PATH) -> tuple[int, int]:
    """从指定快照生成 SQL；不修改共享基准快照。"""
    orders = json.loads((data_dir / "orders.json").read_text(encoding="utf-8"))
    logistics = json.loads((data_dir / "logistics.json").read_text(encoding="utf-8"))
    source_line = (
        "-- 数据来源：testing/test-data/（单一事实源，见其 MANIFEST.md）"
        if data_dir.resolve() == DATA_DIR.resolve()
        else "-- 数据来源：所选数据档的 orders.json / logistics.json；核对其 manifest.json"
    )

    lines = [
        "-- Java 订单物流微服务种子数据（生成产物，勿手工编辑）",
        "-- 生成命令：python3 app/java-order-logistics/sql/generate-seed.py",
        source_line,
        "-- 目标库：order_logistics（initdb 默认库）；仅新数据卷首次初始化自动执行，",
        "--         已有数据卷须手工重放一次（命令见本目录 generate-seed.py 头注释）。",
        "-- 本文件为 UTF-8；SET NAMES 必须在前：initdb 的 mysql 客户端默认按 latin1",
        "-- 解读 SQL 文件，缺此行中文会被二次编码入库（SQLite 导入时跳过本行）。",
        "SET NAMES utf8mb4;",
        "START TRANSACTION;",
        "",
    ]
    if data_dir.resolve() == DATA_DIR.resolve():
        # 默认输出必须与既有 seed-mysql.sql 完全一致。
        lines += ["DELETE FROM orders;", "DELETE FROM logistics;", ""]
    else:
        # 独立数据库重复装载时先清除可能由应用写入的子表，避免外键阻断。
        lines += ["DELETE FROM logistics_trajectory;", "DELETE FROM order_items;",
                  "DELETE FROM logistics;", "DELETE FROM orders;", ""]
    lines += insert_statements("orders", ORDERS_COLUMNS, orders)
    lines.append("")
    lines += insert_statements("logistics", LOGISTICS_COLUMNS, logistics)
    lines += ["", "COMMIT;", ""]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return len(orders), len(logistics)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args()
    counts = generate(args.data_dir, args.out)
    print(f"已生成 {args.out}（orders {counts[0]} 条、logistics {counts[1]} 条）")


if __name__ == "__main__":
    main()
