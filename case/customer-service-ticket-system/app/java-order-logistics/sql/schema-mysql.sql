-- MySQL 建表脚本

-- 订单表
CREATE TABLE IF NOT EXISTS orders (
    order_id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(64),
    status VARCHAR(32),
    items TEXT,  -- JSON 字符串
    total_amount DECIMAL(10,2),
    shipping_address TEXT,  -- JSON 字符串
    tracking_no VARCHAR(64),
    carrier VARCHAR(32),
    create_time VARCHAR(32),
    update_time VARCHAR(32)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 订单商品项表
CREATE TABLE IF NOT EXISTS order_items (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id VARCHAR(64),
    product_id VARCHAR(64),
    product_name VARCHAR(128),
    quantity INT,
    price DECIMAL(10,2),
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 物流表
CREATE TABLE IF NOT EXISTS logistics (
    tracking_no VARCHAR(64) PRIMARY KEY,
    order_id VARCHAR(64),
    carrier VARCHAR(32),
    status VARCHAR(32),
    current_location VARCHAR(128),
    origin VARCHAR(128),
    destination VARCHAR(128),
    trajectory TEXT,  -- JSON 字符串
    estimated_delivery VARCHAR(64)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 物流轨迹表
CREATE TABLE IF NOT EXISTS logistics_trajectory (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    tracking_no VARCHAR(64),
    time VARCHAR(32),
    location VARCHAR(128),
    status VARCHAR(32),
    FOREIGN KEY (tracking_no) REFERENCES logistics(tracking_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 创建索引
CREATE INDEX idx_orders_user_id ON orders(user_id);
CREATE INDEX idx_orders_tracking_no ON orders(tracking_no);
CREATE INDEX idx_order_items_order_id ON order_items(order_id);
CREATE INDEX idx_logistics_order_id ON logistics(order_id);
CREATE INDEX idx_logistics_trajectory_tracking_no ON logistics_trajectory(tracking_no);
