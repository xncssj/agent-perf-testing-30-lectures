-- Docker 初始化：为 Python Agent 单独建库，避免与 Java 的 order_logistics 抢 orders / logistics 表。
CREATE DATABASE IF NOT EXISTS cs_agent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
