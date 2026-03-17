#!/usr/bin/env python3
"""
Seed script for OrderGuard ERP MySQL database.

Creates a realistic mid-size e-commerce ERP schema in `orderguard_erp`
and populates it with ~3000 sales orders, ~50 SKUs, inventory, purchases,
returns, and daily summaries — including embedded anomalies for AI detection.

Usage:
    uv run python scripts/seed_mysql_erp.py

Connects as: mysql -u root (no password) to orderguard_erp
"""

from __future__ import annotations

import random
import datetime
from decimal import Decimal

import mysql.connector

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_CONFIG = {
    "host": "127.0.0.1",
    "user": "root",
    "password": "",
    "database": "orderguard_erp",
    "charset": "utf8mb4",
}

TODAY = datetime.date(2026, 3, 8)
random.seed(42)

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
-- -------------------------------------------------------
-- 1. categories 产品类目
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS categories (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    parent_id   INT NULL,
    level       TINYINT NOT NULL DEFAULT 1,
    status      ENUM('active','inactive') NOT NULL DEFAULT 'active',
    FOREIGN KEY (parent_id) REFERENCES categories(id) ON DELETE SET NULL,
    INDEX idx_parent (parent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='产品类目（三级层级）';

-- -------------------------------------------------------
-- 2. suppliers 供应商
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS suppliers (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    contact         VARCHAR(100),
    phone           VARCHAR(30),
    email           VARCHAR(150),
    address         VARCHAR(300),
    rating          DECIMAL(2,1) DEFAULT 4.0,
    payment_terms   VARCHAR(100),
    status          ENUM('active','inactive','blacklisted') NOT NULL DEFAULT 'active',
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='供应商信息';

-- -------------------------------------------------------
-- 3. products 商品
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS products (
    sku         VARCHAR(30) PRIMARY KEY,
    name        VARCHAR(300) NOT NULL,
    category_id INT,
    supplier_id INT,
    unit_cost   DECIMAL(10,2) NOT NULL,
    unit_price  DECIMAL(10,2) NOT NULL,
    weight_kg   DECIMAL(6,3) DEFAULT 0.5,
    status      ENUM('active','discontinued','draft') NOT NULL DEFAULT 'active',
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (category_id) REFERENCES categories(id),
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
    INDEX idx_category (category_id),
    INDEX idx_supplier (supplier_id),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商品主数据';

-- -------------------------------------------------------
-- 4. warehouses 仓库
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS warehouses (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    city        VARCHAR(60),
    province    VARCHAR(60),
    capacity    INT NOT NULL DEFAULT 10000,
    type        ENUM('自营','第三方') NOT NULL DEFAULT '自营'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='仓库信息';

-- -------------------------------------------------------
-- 5. inventory 实时库存
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS inventory (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    sku                 VARCHAR(30) NOT NULL,
    warehouse_id        INT NOT NULL,
    quantity            INT NOT NULL DEFAULT 0,
    reserved_qty        INT NOT NULL DEFAULT 0,
    reorder_point       INT NOT NULL DEFAULT 50,
    safety_stock        INT NOT NULL DEFAULT 20,
    lead_time_days      INT NOT NULL DEFAULT 7,
    last_replenish_date DATE,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (sku) REFERENCES products(sku),
    FOREIGN KEY (warehouse_id) REFERENCES warehouses(id),
    UNIQUE KEY uk_sku_wh (sku, warehouse_id),
    INDEX idx_sku (sku),
    INDEX idx_warehouse (warehouse_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='实时库存';

-- -------------------------------------------------------
-- 6. purchase_orders 采购单
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS purchase_orders (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    po_number       VARCHAR(30) NOT NULL UNIQUE,
    supplier_id     INT NOT NULL,
    status          ENUM('draft','confirmed','shipped','received','cancelled') NOT NULL DEFAULT 'draft',
    total_amount    DECIMAL(12,2) NOT NULL DEFAULT 0,
    order_date      DATE NOT NULL,
    expected_date   DATE,
    received_date   DATE,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
    INDEX idx_supplier (supplier_id),
    INDEX idx_status (status),
    INDEX idx_order_date (order_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='采购单';

-- -------------------------------------------------------
-- 7. purchase_order_items 采购明细
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS purchase_order_items (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    po_id       INT NOT NULL,
    sku         VARCHAR(30) NOT NULL,
    quantity    INT NOT NULL,
    unit_cost   DECIMAL(10,2) NOT NULL,
    received_qty INT NOT NULL DEFAULT 0,
    FOREIGN KEY (po_id) REFERENCES purchase_orders(id) ON DELETE CASCADE,
    FOREIGN KEY (sku) REFERENCES products(sku),
    INDEX idx_po (po_id),
    INDEX idx_sku (sku)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='采购明细';

-- -------------------------------------------------------
-- 8. sales_orders 销售订单
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS sales_orders (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    order_number    VARCHAR(30) NOT NULL UNIQUE,
    channel         ENUM('天猫','京东','抖音','自营') NOT NULL,
    customer_name   VARCHAR(100),
    status          ENUM('pending','confirmed','shipped','delivered','returned','cancelled') NOT NULL DEFAULT 'pending',
    total_amount    DECIMAL(12,2) NOT NULL DEFAULT 0,
    order_date      DATE NOT NULL,
    ship_date       DATE,
    delivery_date   DATE,
    INDEX idx_channel (channel),
    INDEX idx_status (status),
    INDEX idx_order_date (order_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='销售订单';

-- -------------------------------------------------------
-- 9. sales_order_items 销售明细
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS sales_order_items (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    order_id        INT NOT NULL,
    sku             VARCHAR(30) NOT NULL,
    quantity        INT NOT NULL DEFAULT 1,
    unit_price      DECIMAL(10,2) NOT NULL,
    discount_rate   DECIMAL(4,2) NOT NULL DEFAULT 0.00,
    FOREIGN KEY (order_id) REFERENCES sales_orders(id) ON DELETE CASCADE,
    FOREIGN KEY (sku) REFERENCES products(sku),
    INDEX idx_order (order_id),
    INDEX idx_sku (sku)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='销售明细';

-- -------------------------------------------------------
-- 10. daily_sales_summary 每日销售汇总
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_sales_summary (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    sku             VARCHAR(30) NOT NULL,
    sale_date       DATE NOT NULL,
    channel         ENUM('天猫','京东','抖音','自营') NOT NULL,
    quantity_sold   INT NOT NULL DEFAULT 0,
    revenue         DECIMAL(12,2) NOT NULL DEFAULT 0,
    cost            DECIMAL(12,2) NOT NULL DEFAULT 0,
    profit          DECIMAL(12,2) NOT NULL DEFAULT 0,
    return_qty      INT NOT NULL DEFAULT 0,
    FOREIGN KEY (sku) REFERENCES products(sku),
    UNIQUE KEY uk_sku_date_ch (sku, sale_date, channel),
    INDEX idx_date (sale_date),
    INDEX idx_sku (sku)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='每日销售汇总';

-- -------------------------------------------------------
-- 11. inventory_movements 库存流水
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS inventory_movements (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    sku             VARCHAR(30) NOT NULL,
    warehouse_id    INT NOT NULL,
    movement_type   ENUM('inbound','outbound','transfer','adjustment') NOT NULL,
    quantity         INT NOT NULL,
    reference_no    VARCHAR(50),
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (sku) REFERENCES products(sku),
    FOREIGN KEY (warehouse_id) REFERENCES warehouses(id),
    INDEX idx_sku (sku),
    INDEX idx_wh (warehouse_id),
    INDEX idx_type (movement_type),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='库存流水';

-- -------------------------------------------------------
-- 12. returns 退货记录
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS returns (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    order_id    INT NOT NULL,
    sku         VARCHAR(30) NOT NULL,
    quantity    INT NOT NULL DEFAULT 1,
    reason      ENUM('质量问题','尺寸不合','不喜欢','物流损坏','其他') NOT NULL,
    status      ENUM('pending','approved','refunded') NOT NULL DEFAULT 'pending',
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES sales_orders(id),
    FOREIGN KEY (sku) REFERENCES products(sku),
    INDEX idx_order (order_id),
    INDEX idx_sku (sku),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='退货记录';
"""

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
# Categories: (name, parent_name_or_None, level)
CATEGORIES_TREE = [
    # Level 1
    ("电子产品", None, 1),
    ("穿戴设备", None, 1),
    ("家居生活", None, 1),
    ("运动户外", None, 1),
    ("食品饮料", None, 1),
    # Level 2
    ("手机配件", "电子产品", 2),
    ("电脑配件", "电子产品", 2),
    ("智能手表", "穿戴设备", 2),
    ("智能手环", "穿戴设备", 2),
    ("厨房用品", "家居生活", 2),
    ("收纳整理", "家居生活", 2),
    ("健身器材", "运动户外", 2),
    ("休闲零食", "食品饮料", 2),
    # Level 3
    ("手机壳", "手机配件", 3),
    ("充电线材", "手机配件", 3),
]

SUPPLIERS = [
    ("深圳华强电子科技有限公司", "张伟", "13800138001", "zhangwei@huaqiang.cn", "广东省深圳市福田区华强北路1001号", 4.5, "月结30天"),
    ("东莞市品优塑胶制品厂", "李娜", "13900139002", "lina@pinyou.com", "广东省东莞市长安镇上角工业区", 4.2, "月结45天"),
    ("杭州智联物联网有限公司", "王磊", "15800158003", "wanglei@zhilian-iot.com", "浙江省杭州市滨江区网商路599号", 4.8, "预付50%+到货付清"),
    ("义乌市恒达五金制品厂", "刘芳", "13700137004", "liufang@hengda.cn", "浙江省义乌市北苑街道春晗路180号", 3.9, "月结30天"),
    ("上海鼎盛贸易有限公司", "陈强", "18600186005", "chenqiang@dingsheng-sh.com", "上海市闵行区莘庄工业区春东路508号", 4.3, "月结60天"),
    ("佛山市顺德厨具制造有限公司", "赵敏", "13600136006", "zhaomin@sdkitchen.com", "广东省佛山市顺德区容桂镇华丰路", 4.1, "月结30天"),
    ("厦门优品健康科技有限公司", "周杰", "15900159007", "zhoujie@youpin-health.com", "福建省厦门市湖里区高新技术园", 4.6, "预付30%+月结"),
    ("成都蜀味食品有限公司", "孙丽", "13500135008", "sunli@shuwei-food.com", "四川省成都市郫都区安德镇食品园区", 4.0, "月结15天"),
    ("宁波海纳电气有限公司", "吴涛", "18800188009", "wutao@haina-elec.com", "浙江省宁波市北仑区新碶街道", 4.4, "月结30天"),
    ("广州锦程包装材料有限公司", "黄静", "13300133010", "huangjing@jincheng-bz.com", "广东省广州市白云区钟落潭镇", 3.8, "月结30天"),
]

WAREHOUSES = [
    ("华东仓", "上海", "上海市", 20000, "自营"),
    ("华南仓", "广州", "广东省", 15000, "自营"),
    ("华北仓", "北京", "北京市", 18000, "第三方"),
    ("西南仓", "成都", "四川省", 10000, "第三方"),
    ("华中仓", "武汉", "湖北省", 12000, "自营"),
]

# (sku, name, category_name, supplier_idx(0-based), unit_cost, unit_price, weight_kg, status)
PRODUCTS = [
    ("SKU-ERP-001", "iPhone 15 透明防摔手机壳", "手机壳", 0, 8.50, 39.90, 0.05, "active"),
    ("SKU-ERP-002", "Type-C 快充数据线 1米", "充电线材", 0, 3.20, 19.90, 0.03, "active"),
    ("SKU-ERP-003", "MacBook Pro 散热支架", "电脑配件", 3, 35.00, 129.00, 0.85, "active"),
    ("SKU-ERP-004", "无线蓝牙机械键盘", "电脑配件", 0, 89.00, 299.00, 0.65, "active"),
    ("SKU-ERP-005", "华为 GT4 智能手表运动版", "智能手表", 2, 580.00, 1488.00, 0.08, "active"),
    ("SKU-ERP-006", "小米手环 8 NFC版", "智能手环", 2, 95.00, 249.00, 0.03, "active"),
    ("SKU-ERP-007", "多功能硅胶厨房铲套装", "厨房用品", 5, 12.00, 49.90, 0.35, "active"),
    ("SKU-ERP-008", "不锈钢真空保温杯 500ml", "厨房用品", 5, 18.00, 79.90, 0.32, "active"),
    ("SKU-ERP-009", "桌面收纳盒三件套", "收纳整理", 3, 15.00, 59.90, 0.60, "active"),
    ("SKU-ERP-010", "可折叠瑜伽垫 6mm", "健身器材", 6, 22.00, 89.00, 1.20, "active"),
    ("SKU-ERP-011", "弹力阻力带五件套", "健身器材", 6, 8.00, 35.90, 0.25, "active"),
    ("SKU-ERP-012", "每日坚果混合装 750g", "休闲零食", 7, 28.00, 69.90, 0.80, "active"),
    ("SKU-ERP-013", "Samsung 手机壳 磨砂款", "手机壳", 1, 6.00, 29.90, 0.04, "active"),
    ("SKU-ERP-014", "65W GaN 氮化镓充电器", "充电线材", 0, 42.00, 149.00, 0.12, "active"),
    ("SKU-ERP-015", "AirPods Pro 保护套", "手机配件", 1, 5.00, 25.90, 0.02, "active"),
    ("SKU-ERP-016", "电竞鼠标垫 超大号", "电脑配件", 3, 18.00, 69.00, 0.45, "active"),
    ("SKU-ERP-017", "智能体脂秤 WiFi版", "穿戴设备", 2, 65.00, 199.00, 1.50, "active"),
    ("SKU-ERP-018", "运动水壶 750ml", "健身器材", 5, 10.00, 39.90, 0.18, "active"),
    ("SKU-ERP-019", "家用哑铃套装 20kg", "健身器材", 6, 85.00, 259.00, 20.50, "active"),
    ("SKU-ERP-020", "抹茶味蛋白棒 12支装", "休闲零食", 7, 35.00, 99.00, 0.60, "active"),
    ("SKU-ERP-021", "MagSafe 磁吸充电宝", "手机配件", 0, 55.00, 199.00, 0.20, "active"),
    ("SKU-ERP-022", "降噪蓝牙耳机", "电子产品", 0, 120.00, 399.00, 0.25, "active"),
    ("SKU-ERP-023", "复古机械闹钟", "家居生活", 3, 25.00, 89.00, 0.40, "active"),
    ("SKU-ERP-024", "USB-C 扩展坞 7合1", "电脑配件", 8, 78.00, 259.00, 0.18, "active"),
    ("SKU-ERP-025", "儿童智能手表 4G版", "智能手表", 2, 180.00, 499.00, 0.06, "active"),
    ("SKU-ERP-026", "竹纤维收纳篮 3件套", "收纳整理", 3, 20.00, 79.90, 0.90, "active"),
    ("SKU-ERP-027", "厨房电子秤", "厨房用品", 5, 15.00, 59.00, 0.30, "active"),
    ("SKU-ERP-028", "跳绳 专业计数款", "健身器材", 6, 6.00, 29.90, 0.15, "active"),
    ("SKU-ERP-029", "蜂蜜柚子茶 500g*2", "休闲零食", 7, 18.00, 49.90, 1.10, "active"),
    ("SKU-ERP-030", "笔记本电脑内胆包 14寸", "电脑配件", 9, 22.00, 79.00, 0.25, "active"),
    ("SKU-ERP-031", "蓝牙音箱 便携防水", "电子产品", 0, 45.00, 159.00, 0.35, "active"),
    ("SKU-ERP-032", "硅胶厨房手套 加厚款", "厨房用品", 5, 8.00, 32.00, 0.15, "active"),
    ("SKU-ERP-033", "LED 护眼台灯", "家居生活", 8, 55.00, 189.00, 1.20, "active"),
    ("SKU-ERP-034", "旅行收纳袋 6件套", "收纳整理", 9, 12.00, 45.90, 0.30, "active"),
    ("SKU-ERP-035", "无线车载充电支架", "手机配件", 0, 38.00, 139.00, 0.22, "active"),
    ("SKU-ERP-036", "智能手环 血氧版", "智能手环", 2, 75.00, 199.00, 0.03, "active"),
    ("SKU-ERP-037", "泡沫轴 肌肉放松", "健身器材", 6, 18.00, 69.00, 0.55, "active"),
    ("SKU-ERP-038", "芒果干 500g", "休闲零食", 7, 15.00, 39.90, 0.55, "active"),
    ("SKU-ERP-039", "Type-C 转 HDMI 转接头", "电脑配件", 8, 20.00, 69.00, 0.05, "active"),
    ("SKU-ERP-040", "家用空气炸锅 4.5L", "厨房用品", 5, 120.00, 399.00, 4.50, "active"),
    ("SKU-ERP-041", "户外折叠椅", "运动户外", 3, 45.00, 149.00, 3.20, "active"),
    ("SKU-ERP-042", "手机支架 桌面可调节", "手机配件", 1, 5.50, 25.90, 0.12, "active"),
    ("SKU-ERP-043", "速干运动T恤", "运动户外", 9, 22.00, 79.00, 0.18, "active"),
    ("SKU-ERP-044", "充电宝 20000mAh", "电子产品", 0, 58.00, 199.00, 0.45, "active"),
    ("SKU-ERP-045", "智能门锁 指纹密码", "家居生活", 8, 320.00, 899.00, 2.80, "active"),
    ("SKU-ERP-046", "宠物自动喂食器", "家居生活", 2, 95.00, 299.00, 1.80, "active"),
    ("SKU-ERP-047", "车载香薰 高级款", "家居生活", 9, 18.00, 69.00, 0.10, "active"),
    ("SKU-ERP-048", "无线充电板 15W", "手机配件", 0, 25.00, 89.00, 0.15, "active"),
    ("SKU-ERP-049", "牛肉干 内蒙风味 250g", "休闲零食", 7, 30.00, 79.90, 0.28, "active"),
    ("SKU-ERP-050", "多功能螺丝刀套装 24合1", "家居生活", 3, 15.00, 49.90, 0.35, "active"),
]

CHANNELS = ["天猫", "京东", "抖音", "自营"]

CUSTOMER_SURNAMES = ["张", "王", "李", "赵", "刘", "陈", "杨", "黄", "周", "吴",
                     "徐", "孙", "马", "胡", "郭", "林", "何", "高", "罗", "郑"]
CUSTOMER_NAMES_PART = ["伟", "芳", "娜", "强", "磊", "洋", "勇", "艳", "杰", "丽",
                       "静", "涛", "明", "辉", "霞", "鑫", "慧", "军", "敏", "婷"]

RETURN_REASONS = ["质量问题", "尺寸不合", "不喜欢", "物流损坏", "其他"]


def random_customer():
    return random.choice(CUSTOMER_SURNAMES) + random.choice(CUSTOMER_NAMES_PART) + random.choice(CUSTOMER_NAMES_PART)


def connect():
    return mysql.connector.connect(**DB_CONFIG)


def create_schema(conn):
    cur = conn.cursor()
    for stmt in SCHEMA_SQL.split(";"):
        stmt = stmt.strip()
        if stmt:
            cur.execute(stmt)
    conn.commit()
    cur.close()


def seed_categories(conn):
    cur = conn.cursor()
    # Insert level by level so parent_id can resolve
    name_to_id: dict[str, int] = {}
    for name, parent_name, level in CATEGORIES_TREE:
        parent_id = name_to_id.get(parent_name)
        cur.execute(
            "INSERT IGNORE INTO categories (name, parent_id, level, status) VALUES (%s, %s, %s, 'active')",
            (name, parent_id, level),
        )
        conn.commit()
        # Fetch the id (may already exist)
        cur.execute("SELECT id FROM categories WHERE name = %s", (name,))
        row = cur.fetchone()
        if row:
            name_to_id[name] = row[0]
    cur.close()
    return name_to_id


def seed_suppliers(conn):
    cur = conn.cursor()
    ids = []
    for s in SUPPLIERS:
        cur.execute(
            """INSERT IGNORE INTO suppliers
               (name, contact, phone, email, address, rating, payment_terms, status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,'active')""",
            s,
        )
        conn.commit()
        cur.execute("SELECT id FROM suppliers WHERE name = %s", (s[0],))
        ids.append(cur.fetchone()[0])
    cur.close()
    return ids


def seed_warehouses(conn):
    cur = conn.cursor()
    ids = []
    for w in WAREHOUSES:
        cur.execute(
            "INSERT IGNORE INTO warehouses (name, city, province, capacity, type) VALUES (%s,%s,%s,%s,%s)",
            w,
        )
        conn.commit()
        cur.execute("SELECT id FROM warehouses WHERE name = %s", (w[0],))
        ids.append(cur.fetchone()[0])
    cur.close()
    return ids


def seed_products(conn, cat_name_to_id, supplier_ids):
    cur = conn.cursor()
    for p in PRODUCTS:
        sku, name, cat_name, sup_idx, cost, price, weight, status = p
        cat_id = cat_name_to_id.get(cat_name)
        sup_id = supplier_ids[sup_idx]
        cur.execute(
            """REPLACE INTO products
               (sku, name, category_id, supplier_id, unit_cost, unit_price, weight_kg, status, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (sku, name, cat_id, sup_id, cost, price, weight, status,
             TODAY - datetime.timedelta(days=random.randint(60, 365))),
        )
    conn.commit()
    cur.close()


def seed_inventory(conn, warehouse_ids):
    """Each SKU in 2-3 warehouses with embedded anomalies."""
    cur = conn.cursor()

    # Default inventory generation
    for p in PRODUCTS:
        sku = p[0]
        # Pick 2-3 warehouses
        n_wh = random.choice([2, 2, 3])
        wh_ids = random.sample(warehouse_ids, n_wh)
        for wh_id in wh_ids:
            qty = random.randint(50, 500)
            reserved = random.randint(0, min(30, qty))
            reorder = random.randint(30, 80)
            safety = random.randint(10, 30)
            lead_days = random.randint(3, 14)
            last_replenish = TODAY - datetime.timedelta(days=random.randint(1, 30))
            cur.execute(
                """REPLACE INTO inventory
                   (sku, warehouse_id, quantity, reserved_qty, reorder_point, safety_stock,
                    lead_time_days, last_replenish_date)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (sku, wh_id, qty, reserved, reorder, safety, lead_days, last_replenish),
            )
    conn.commit()

    # --- Anomaly: SKU-ERP-015 critically low in 华东仓 (warehouse_ids[0]) ---
    cur.execute(
        "UPDATE inventory SET quantity=3, reserved_qty=0, reorder_point=100, safety_stock=50 "
        "WHERE sku='SKU-ERP-015' AND warehouse_id=%s", (warehouse_ids[0],))
    # Also ensure it exists there
    if cur.rowcount == 0:
        cur.execute(
            "REPLACE INTO inventory (sku, warehouse_id, quantity, reserved_qty, reorder_point, safety_stock, lead_time_days, last_replenish_date) "
            "VALUES ('SKU-ERP-015', %s, 3, 0, 100, 50, 7, %s)",
            (warehouse_ids[0], TODAY - datetime.timedelta(days=20)))

    # --- Anomaly: SKU-ERP-023 overstocked ---
    for wh_id in warehouse_ids[:3]:
        cur.execute(
            "REPLACE INTO inventory (sku, warehouse_id, quantity, reserved_qty, reorder_point, safety_stock, lead_time_days, last_replenish_date) "
            "VALUES ('SKU-ERP-023', %s, %s, 0, 30, 10, 7, %s)",
            (wh_id, random.choice([2500, 2800, 2700]), TODAY - datetime.timedelta(days=60)))

    # --- Anomaly: 华南仓 (warehouse_ids[1]) overstocked for slow movers ---
    # Boost inventory for many SKUs in 华南仓 to create high turnover days
    cur.execute("UPDATE inventory SET quantity = quantity + 1500 WHERE warehouse_id = %s", (warehouse_ids[1],))

    conn.commit()
    cur.close()


def seed_purchase_orders(conn, supplier_ids):
    """~100 POs over last 6 months."""
    cur = conn.cursor()
    skus = [p[0] for p in PRODUCTS]
    sku_cost = {p[0]: p[4] for p in PRODUCTS}
    sku_sup = {p[0]: supplier_ids[p[3]] for p in PRODUCTS}

    po_ids = []
    for i in range(1, 101):
        po_number = f"PO-2026-{i:04d}"
        days_ago = random.randint(1, 180)
        order_date = TODAY - datetime.timedelta(days=days_ago)
        sup_id = random.choice(supplier_ids)
        lead = random.randint(5, 20)
        expected_date = order_date + datetime.timedelta(days=lead)

        if days_ago > lead + 5:
            status = random.choice(["received", "received", "received", "cancelled"])
            received_date = expected_date + datetime.timedelta(days=random.randint(-2, 5)) if status == "received" else None
        elif days_ago > lead:
            status = random.choice(["received", "shipped"])
            received_date = expected_date if status == "received" else None
        else:
            status = random.choice(["draft", "confirmed", "shipped"])
            received_date = None

        cur.execute(
            """INSERT IGNORE INTO purchase_orders
               (po_number, supplier_id, status, total_amount, order_date, expected_date, received_date)
               VALUES (%s,%s,%s,0,%s,%s,%s)""",
            (po_number, sup_id, status, order_date, expected_date, received_date),
        )
        conn.commit()
        cur.execute("SELECT id FROM purchase_orders WHERE po_number=%s", (po_number,))
        row = cur.fetchone()
        if row:
            po_ids.append(row[0])

        # 1-5 line items per PO
        n_items = random.randint(1, 5)
        chosen_skus = random.sample(skus, min(n_items, len(skus)))
        total = Decimal("0")
        for sku in chosen_skus:
            qty = random.choice([50, 100, 200, 300, 500])
            cost = Decimal(str(sku_cost[sku]))

            # Anomaly: SKU-ERP-008 cost spike in recent POs
            if sku == "SKU-ERP-008" and days_ago < 30:
                cost = round(cost * Decimal("1.4"), 2)

            recv_qty = qty if status == "received" else (random.randint(0, qty) if status == "shipped" else 0)
            line_total = cost * qty
            total += line_total
            cur.execute(
                "INSERT IGNORE INTO purchase_order_items (po_id, sku, quantity, unit_cost, received_qty) VALUES (%s,%s,%s,%s,%s)",
                (row[0], sku, qty, float(cost), recv_qty),
            )

        cur.execute("UPDATE purchase_orders SET total_amount=%s WHERE id=%s", (float(total), row[0]))

    conn.commit()
    cur.close()
    return po_ids


def seed_sales_orders(conn, warehouse_ids):
    """~3000 sales orders over last 3 months."""
    cur = conn.cursor()
    skus = [p[0] for p in PRODUCTS]
    sku_price = {p[0]: p[5] for p in PRODUCTS}
    sku_cost = {p[0]: p[4] for p in PRODUCTS}

    # Weighted SKU popularity
    popular_skus = ["SKU-ERP-001", "SKU-ERP-002", "SKU-ERP-006", "SKU-ERP-014",
                    "SKU-ERP-015", "SKU-ERP-022", "SKU-ERP-044"]
    sku_weights = []
    for s in skus:
        if s in popular_skus:
            sku_weights.append(5)
        else:
            sku_weights.append(1)

    order_ids_by_sku: dict[str, list[int]] = {s: [] for s in skus}

    for i in range(1, 3001):
        order_number = f"SO-2026-{i:06d}"
        days_ago = random.randint(0, 90)
        order_date = TODAY - datetime.timedelta(days=days_ago)
        channel = random.choice(CHANNELS)
        customer = random_customer()

        # Status based on age
        if days_ago > 7:
            status = random.choices(
                ["delivered", "delivered", "delivered", "returned", "cancelled"],
                weights=[60, 20, 10, 5, 5],
            )[0]
        elif days_ago > 2:
            status = random.choices(
                ["shipped", "delivered", "confirmed"],
                weights=[50, 30, 20],
            )[0]
        else:
            status = random.choices(["pending", "confirmed"], weights=[40, 60])[0]

        ship_date = (order_date + datetime.timedelta(days=random.randint(1, 3))) if status in ("shipped", "delivered") else None
        delivery_date = (ship_date + datetime.timedelta(days=random.randint(1, 5))) if status == "delivered" and ship_date else None

        cur.execute(
            """INSERT IGNORE INTO sales_orders
               (order_number, channel, customer_name, status, total_amount, order_date, ship_date, delivery_date)
               VALUES (%s,%s,%s,%s,0,%s,%s,%s)""",
            (order_number, channel, customer, status, order_date, ship_date, delivery_date),
        )
        conn.commit()
        cur.execute("SELECT id FROM sales_orders WHERE order_number=%s", (order_number,))
        row = cur.fetchone()
        if not row:
            continue
        oid = row[0]

        # 1-3 items per order
        n_items = random.choices([1, 2, 3], weights=[60, 30, 10])[0]
        chosen = random.choices(skus, weights=sku_weights, k=n_items)
        chosen = list(set(chosen))  # dedupe
        total = Decimal("0")

        for sku in chosen:
            qty = random.choices([1, 1, 2, 3], weights=[50, 20, 20, 10])[0]
            price = Decimal(str(sku_price[sku]))
            discount = Decimal(str(random.choice([0, 0, 0, 0.05, 0.10, 0.15, 0.20])))
            line_total = price * qty * (1 - discount)
            total += line_total

            # Anomaly: SKU-ERP-042 sales spike in last 7 days
            if sku == "SKU-ERP-042" and days_ago <= 7:
                qty = random.randint(5, 15)
                line_total = price * qty * (1 - discount)
                total += line_total - price * 1  # adjust

            cur.execute(
                "INSERT IGNORE INTO sales_order_items (order_id, sku, quantity, unit_price, discount_rate) VALUES (%s,%s,%s,%s,%s)",
                (oid, sku, qty, float(price), float(discount)),
            )
            order_ids_by_sku[sku].append(oid)

        cur.execute("UPDATE sales_orders SET total_amount=%s WHERE id=%s", (float(total), oid))

    conn.commit()
    cur.close()
    return order_ids_by_sku


def seed_daily_sales_summary(conn):
    """~5000 rows: 90 days x sampled SKUs x channels."""
    cur = conn.cursor()
    sku_price = {p[0]: p[5] for p in PRODUCTS}
    sku_cost = {p[0]: p[4] for p in PRODUCTS}
    skus = [p[0] for p in PRODUCTS]

    rows_inserted = 0
    for day_offset in range(90):
        sale_date = TODAY - datetime.timedelta(days=day_offset)
        # Sample ~15 SKUs per day per channel (not all)
        for ch in CHANNELS:
            sampled_skus = random.sample(skus, random.randint(12, 18))
            for sku in sampled_skus:
                base_qty = random.randint(1, 15)
                price = sku_price[sku]
                cost = sku_cost[sku]

                # Anomaly: SKU-ERP-042 sales spike in last 7 days
                if sku == "SKU-ERP-042" and day_offset <= 7:
                    base_qty = random.randint(40, 80)

                # Anomaly: SKU-ERP-023 very low sales
                if sku == "SKU-ERP-023":
                    base_qty = random.choice([0, 0, 0, 1])

                # Anomaly: SKU-ERP-015 high normal sales (to show stockout risk)
                if sku == "SKU-ERP-015":
                    base_qty = random.randint(20, 30)

                revenue = round(base_qty * price, 2)
                total_cost = round(base_qty * cost, 2)

                # Anomaly: SKU-ERP-008 cost spike recently
                if sku == "SKU-ERP-008" and day_offset < 30:
                    total_cost = round(base_qty * cost * 1.4, 2)

                profit = round(revenue - total_cost, 2)
                return_qty = 0

                # Anomaly: SKU-ERP-031 high return rate
                if sku == "SKU-ERP-031" and base_qty > 0:
                    return_qty = max(1, int(base_qty * random.uniform(0.18, 0.28)))
                elif base_qty > 3 and random.random() < 0.08:
                    return_qty = random.randint(1, max(1, base_qty // 5))

                cur.execute(
                    """INSERT IGNORE INTO daily_sales_summary
                       (sku, sale_date, channel, quantity_sold, revenue, cost, profit, return_qty)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (sku, sale_date, ch, base_qty, revenue, total_cost, profit, return_qty),
                )
                rows_inserted += 1

    conn.commit()
    cur.close()
    return rows_inserted


def seed_inventory_movements(conn, warehouse_ids):
    """~2000 movement records."""
    cur = conn.cursor()
    skus = [p[0] for p in PRODUCTS]
    movement_types = ["inbound", "outbound", "transfer", "adjustment"]
    type_weights = [30, 45, 15, 10]

    for i in range(2000):
        sku = random.choice(skus)
        wh_id = random.choice(warehouse_ids)
        mtype = random.choices(movement_types, weights=type_weights)[0]
        qty = random.randint(1, 200) if mtype != "adjustment" else random.randint(-20, 20)
        if mtype == "outbound":
            qty = -abs(qty)
        days_ago = random.randint(0, 90)
        created = datetime.datetime.combine(
            TODAY - datetime.timedelta(days=days_ago),
            datetime.time(random.randint(6, 22), random.randint(0, 59)),
        )
        ref = f"{'PO' if mtype == 'inbound' else 'SO' if mtype == 'outbound' else 'TR' if mtype == 'transfer' else 'ADJ'}-{random.randint(1000, 9999)}"

        cur.execute(
            """INSERT INTO inventory_movements
               (sku, warehouse_id, movement_type, quantity, reference_no, created_at)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (sku, wh_id, mtype, qty, ref, created),
        )

    conn.commit()
    cur.close()


def seed_returns(conn, order_ids_by_sku):
    """~200 returns with anomaly on SKU-ERP-031."""
    cur = conn.cursor()
    returns_count = 0

    # Normal returns for random SKUs
    all_skus = [p[0] for p in PRODUCTS]
    for _ in range(150):
        sku = random.choice(all_skus)
        if sku == "SKU-ERP-031":
            continue  # handled separately
        oids = order_ids_by_sku.get(sku, [])
        if not oids:
            continue
        oid = random.choice(oids)
        qty = random.randint(1, 2)
        reason = random.choices(
            RETURN_REASONS, weights=[15, 20, 40, 15, 10]
        )[0]
        status = random.choice(["pending", "approved", "refunded"])
        days_ago = random.randint(0, 60)
        created = datetime.datetime.combine(
            TODAY - datetime.timedelta(days=days_ago),
            datetime.time(random.randint(8, 20), random.randint(0, 59)),
        )
        cur.execute(
            "INSERT INTO returns (order_id, sku, quantity, reason, status, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
            (oid, sku, qty, reason, status, created),
        )
        returns_count += 1

    # Anomaly: SKU-ERP-031 high return rate — 50+ returns (质量问题 dominant)
    oids_031 = order_ids_by_sku.get("SKU-ERP-031", [])
    for _ in range(55):
        if not oids_031:
            break
        oid = random.choice(oids_031)
        qty = random.randint(1, 2)
        reason = random.choices(
            RETURN_REASONS, weights=[60, 5, 10, 20, 5]
        )[0]
        status = random.choice(["pending", "approved", "refunded"])
        days_ago = random.randint(0, 45)
        created = datetime.datetime.combine(
            TODAY - datetime.timedelta(days=days_ago),
            datetime.time(random.randint(8, 20), random.randint(0, 59)),
        )
        cur.execute(
            "INSERT INTO returns (order_id, sku, quantity, reason, status, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
            (oid, "SKU-ERP-031", qty, reason, status, created),
        )
        returns_count += 1

    conn.commit()
    cur.close()
    return returns_count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Connecting to MySQL orderguard_erp ...")
    conn = connect()

    print("Creating schema (12 tables) ...")
    create_schema(conn)

    print("Seeding categories ...")
    cat_map = seed_categories(conn)
    print(f"  -> {len(cat_map)} categories")

    print("Seeding suppliers ...")
    sup_ids = seed_suppliers(conn)
    print(f"  -> {len(sup_ids)} suppliers")

    print("Seeding warehouses ...")
    wh_ids = seed_warehouses(conn)
    print(f"  -> {len(wh_ids)} warehouses")

    print("Seeding products ...")
    seed_products(conn, cat_map, sup_ids)
    print(f"  -> {len(PRODUCTS)} products")

    print("Seeding inventory ...")
    seed_inventory(conn, wh_ids)
    print("  -> inventory with anomalies embedded")

    print("Seeding purchase orders ...")
    po_ids = seed_purchase_orders(conn, sup_ids)
    print(f"  -> {len(po_ids)} purchase orders")

    print("Seeding sales orders (~3000) ...")
    order_ids_by_sku = seed_sales_orders(conn, wh_ids)
    total_orders = sum(len(v) for v in order_ids_by_sku.values())
    print(f"  -> sales order-item links: {total_orders}")

    print("Seeding daily sales summary ...")
    dss_count = seed_daily_sales_summary(conn)
    print(f"  -> {dss_count} summary rows")

    print("Seeding inventory movements ...")
    seed_inventory_movements(conn, wh_ids)
    print("  -> 2000 movements")

    print("Seeding returns ...")
    ret_count = seed_returns(conn, order_ids_by_sku)
    print(f"  -> {ret_count} returns")

    # Print summary
    print("\n=== Seed Complete ===")
    cur = conn.cursor()
    tables = [
        "categories", "suppliers", "products", "warehouses", "inventory",
        "purchase_orders", "purchase_order_items", "sales_orders",
        "sales_order_items", "daily_sales_summary", "inventory_movements", "returns",
    ]
    for t in tables:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        count = cur.fetchone()[0]
        print(f"  {t:30s} {count:>6d} rows")
    cur.close()

    # Print anomaly verification
    print("\n=== Anomaly Verification ===")
    cur = conn.cursor()

    cur.execute("SELECT quantity FROM inventory WHERE sku='SKU-ERP-015' AND warehouse_id=(SELECT id FROM warehouses WHERE name='华东仓')")
    r = cur.fetchone()
    print(f"  SKU-ERP-015 华东仓 库存: {r[0] if r else 'N/A'} (should be ~3)")

    cur.execute("SELECT SUM(quantity) FROM inventory WHERE sku='SKU-ERP-023'")
    r = cur.fetchone()
    print(f"  SKU-ERP-023 总库存: {r[0] if r else 'N/A'} (should be ~8000+)")

    cur.execute("SELECT COUNT(*) FROM returns WHERE sku='SKU-ERP-031'")
    r = cur.fetchone()
    print(f"  SKU-ERP-031 退货数: {r[0] if r else 'N/A'} (should be ~55)")

    cur.execute("""
        SELECT SUM(quantity_sold) FROM daily_sales_summary
        WHERE sku='SKU-ERP-042' AND sale_date >= %s
    """, (TODAY - datetime.timedelta(days=7),))
    r = cur.fetchone()
    print(f"  SKU-ERP-042 近7天总销量: {r[0] if r else 'N/A'} (should be very high)")

    cur.close()
    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
