-- 实验库管理员表与初始账号。
-- 口令不以明文入库。摘要算法：PBKDF2-HMAC-SHA256，迭代 200000。
-- 初始账号用户名 admin；初始口令仅在 README「管理员账号」说明，便于本地登录实验。
-- 用法：sqlite3 data/agent.db < sql/admin_seed.sql

CREATE TABLE IF NOT EXISTS admin_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES admin_users(id)
);

INSERT INTO admin_users (
    username, password_hash, display_name, email, phone, title, created_at, updated_at
)
SELECT
    'admin',
    'pbkdf2_sha256$200000$4f3c9a1e7b2d8056c1e4aa90d7f31208$c842d1cc4abc705d3a7599c698e35b8775549e0da2d805d8b999cefcfceeb719',
    '系统管理员',
    'admin@example.com',
    '',
    '系统管理员',
    '2026-09-20T00:00:00+00:00',
    '2026-09-20T00:00:00+00:00'
WHERE NOT EXISTS (SELECT 1 FROM admin_users WHERE username = 'admin');
