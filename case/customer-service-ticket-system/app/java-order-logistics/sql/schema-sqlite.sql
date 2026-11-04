-- SQLite 建表脚本

-- 订单表
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    user_id TEXT,
    status TEXT,
    items TEXT,  -- JSON 字符串
    total_amount REAL,
    shipping_address TEXT,  -- JSON 字符串
    tracking_no TEXT,
    carrier TEXT,
    create_time TEXT,
    update_time TEXT
);

-- 订单商品项表
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT,
    product_id TEXT,
    product_name TEXT,
    quantity INTEGER,
    price REAL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
);

-- 物流表
CREATE TABLE IF NOT EXISTS logistics (
    tracking_no TEXT PRIMARY KEY,
    order_id TEXT,
    carrier TEXT,
    status TEXT,
    current_location TEXT,
    origin TEXT,
    destination TEXT,
    trajectory TEXT,  -- JSON 字符串
    estimated_delivery TEXT
);

-- 物流轨迹表
CREATE TABLE IF NOT EXISTS logistics_trajectory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking_no TEXT,
    time TEXT,
    location TEXT,
    status TEXT,
    FOREIGN KEY (tracking_no) REFERENCES logistics(tracking_no)
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_tracking_no ON orders(tracking_no);
CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_logistics_order_id ON logistics(order_id);
CREATE INDEX IF NOT EXISTS idx_logistics_trajectory_tracking_no ON logistics_trajectory(tracking_no);
