-- 实验库管理员表与初始账号（MySQL 8）。
-- 口令不以明文入库。摘要与 sql/admin_seed.sql 相同：PBKDF2-HMAC-SHA256，迭代 200000。
-- 用法：
--   mysql -u root -p order_logistics < sql/admin_seed.mysql.sql

CREATE TABLE IF NOT EXISTS admin_users (
    id BIGINT NOT NULL AUTO_INCREMENT,
    username VARCHAR(64) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    display_name VARCHAR(64) NOT NULL DEFAULT '',
    email VARCHAR(128) NOT NULL DEFAULT '',
    phone VARCHAR(32) NOT NULL DEFAULT '',
    title VARCHAR(64) NOT NULL DEFAULT '',
    created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_admin_users_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS admin_sessions (
    token VARCHAR(128) NOT NULL,
    user_id BIGINT NOT NULL,
    expires_at VARCHAR(40) NOT NULL,
    PRIMARY KEY (token),
    CONSTRAINT fk_admin_sessions_user FOREIGN KEY (user_id) REFERENCES admin_users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

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
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM admin_users WHERE username = 'admin');
