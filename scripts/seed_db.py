"""Seed script for the E-Commerce Data Warehouse (data/warehouse.db).

Generates normalized, deterministic mock data for:
- customers
- products
- orders
- order_items

Includes realistic edge cases:
- NULL values in optional fields (phone, marketing_channel, customer_segment, shipped_date, discount_amount)
- Cancelled, returned, and refunded orders
- Transactions spanning multiple quarters (2025 Q1 through 2026 Q4)
"""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_DB_PATH = Path("data/warehouse.db")

SCHEMA_DDL = """
PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    phone TEXT,
    country TEXT NOT NULL,
    city TEXT,
    customer_segment TEXT,
    marketing_channel TEXT,
    signup_date DATE NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE products (
    product_id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL UNIQUE,
    product_name TEXT NOT NULL,
    category TEXT NOT NULL,
    subcategory TEXT,
    unit_price REAL NOT NULL CHECK (unit_price >= 0),
    unit_cost REAL NOT NULL CHECK (unit_cost >= 0),
    stock_quantity INTEGER NOT NULL DEFAULT 0,
    discontinued INTEGER NOT NULL DEFAULT 0,
    launch_date DATE NOT NULL
);

CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    order_date TIMESTAMP NOT NULL,
    shipped_date TIMESTAMP,
    status TEXT NOT NULL CHECK (status IN ('completed', 'shipped', 'processing', 'cancelled', 'returned', 'refunded')),
    shipping_country TEXT NOT NULL,
    shipping_cost REAL NOT NULL DEFAULT 0.0,
    payment_method TEXT,
    total_amount REAL NOT NULL DEFAULT 0.0,
    FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
);

CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price REAL NOT NULL CHECK (unit_price >= 0),
    discount_amount REAL,
    line_total REAL NOT NULL CHECK (line_total >= 0),
    FOREIGN KEY (order_id) REFERENCES orders (order_id),
    FOREIGN KEY (product_id) REFERENCES products (product_id)
);

CREATE INDEX idx_orders_customer_id ON orders(customer_id);
CREATE INDEX idx_orders_order_date ON orders(order_date);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_order_items_order_id ON order_items(order_id);
CREATE INDEX idx_order_items_product_id ON order_items(product_id);
CREATE INDEX idx_products_category ON products(category);
CREATE INDEX idx_customers_country ON customers(country);
"""


FIRST_NAMES = [
    "Alice", "Bob", "Carlos", "Diana", "Elena", "Marcus", "Hannah", "Ibrahim",
    "Julia", "Kevin", "Laura", "Mateo", "Nadia", "Oscar", "Priya", "Quinn",
    "Rafael", "Sofia", "Tariq", "Uma", "Victor", "Wendy", "Xavier", "Yuki", "Zoe",
]

LAST_NAMES = [
    "Smith", "Johnson", "Silva", "Santos", "Chen", "Patel", "Kim", "Garcia",
    "Mueller", "Dubois", "Rossi", "Novak", "Okafor", "Tanaka", "Alvarez",
    "Lindqvist", "Fernandez", "Kowalski", "Costa", "Berg", "Shah", "Moreau",
]

LOCATIONS: list[tuple[str, list[str | None]]] = [
    ("United States", ["New York", "San Francisco", "Austin", "Chicago", "Seattle", None]),
    ("United Kingdom", ["London", "Manchester", "Edinburgh", None]),
    ("Germany", ["Berlin", "Munich", "Hamburg", None]),
    ("Brazil", ["Sao Paulo", "Rio de Janeiro", "Curitiba", None]),
    ("Canada", ["Toronto", "Vancouver", "Montreal", None]),
    ("Japan", ["Tokyo", "Osaka", None]),
    ("Australia", ["Sydney", "Melbourne", None]),
]

SEGMENTS: list[str | None] = ["Enterprise", "Mid-Market", "SMB", "Consumer", None]
CHANNELS: list[str | None] = ["Organic Search", "Paid Ads", "Referral", "Email", "Direct", None]
PAYMENT_METHODS: list[str | None] = ["Credit Card", "Wire Transfer", "PayPal", "Pix", "Invoice", None]

PRODUCTS_CATALOG: list[tuple[str, str, str, str | None, float, float, int, int]] = [
    ("SKU-EL-001", "ProBook 16 Ultralight Laptop", "Electronics", "Laptops", 1499.99, 920.00, 85, 0),
    ("SKU-EL-002", "4K UltraSharp 32 Monitor", "Electronics", "Monitors", 649.50, 380.00, 120, 0),
    ("SKU-EL-003", "NoiseGuard ANC Wireless Headphones", "Electronics", "Audio", 299.00, 115.00, 240, 0),
    ("SKU-EL-004", "Thunderbolt 4 Docking Station", "Electronics", "Peripherals", 219.99, 95.00, 160, 0),
    ("SKU-EL-005", "Legacy 1080p Webcam", "Electronics", "Peripherals", 59.99, 22.00, 0, 1),
    ("SKU-SW-001", "CloudSync Enterprise License (Annual)", "Software", "SaaS", 1200.00, 120.00, 999, 0),
    ("SKU-SW-002", "DataPulse BI Connector Pro", "Software", "Analytics", 499.00, 45.00, 999, 0),
    ("SKU-SW-003", "SecureVault Endpoint Protection", "Software", "Security", 189.00, 25.00, 999, 0),
    ("SKU-SW-004", "DevOps Pipeline Optimizer", "Software", "Developer Tools", 799.00, 80.00, 999, 0),
    ("SKU-HO-001", "ErgoMesh Executive Chair", "Home & Office", "Furniture", 549.00, 260.00, 45, 0),
    ("SKU-HO-002", "Motorized Standing Desk 60in", "Home & Office", "Furniture", 699.00, 340.00, 30, 0),
    ("SKU-HO-003", "LED Architect Desk Lamp", "Home & Office", "Lighting", 79.50, 28.00, 190, 0),
    ("SKU-HO-004", "Acoustic Studio Wall Panels (6-Pack)", "Home & Office", "Acoustics", 129.00, 48.00, 75, 0),
    ("SKU-AC-001", "Mechanical Keycap Custom Set", "Accessories", "Keyboards", 149.00, 52.00, 150, 0),
    ("SKU-AC-002", "Ergonomic Vertical Wireless Mouse", "Accessories", "Mice", 89.99, 31.00, 310, 0),
    ("SKU-AC-003", "Braided USB-C 240W Cable (2m)", "Accessories", "Cables", 24.99, 5.50, 600, 0),
    ("SKU-AC-004", "Waterproof Laptop Sleeve 16in", "Accessories", "Bags", 45.00, 14.00, 210, 0),
    ("SKU-AC-005", "Portable NVMe SSD 2TB", "Accessories", "Storage", 179.99, 98.00, 110, 0),
    ("SKU-AP-001", "TechFleece Company Hoodie", "Apparel", "Outerwear", 65.00, 22.00, 180, 0),
    ("SKU-AP-002", "Merino Commuter Backpack", "Apparel", "Gear", 159.00, 64.00, 90, 0),
]


def seed_database(db_path: str | Path = DEFAULT_DB_PATH, seed: int = 42) -> dict[str, int]:
    """Create and populate the SQLite e-commerce warehouse with deterministic data.

    Args:
        db_path: Destination SQLite database file path.
        seed: Random seed for reproducible data generation.

    Returns:
        Dictionary mapping table names to row counts inserted.
    """
    rng = random.Random(seed)
    target_path = Path(db_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(target_path) as conn:
        conn.executescript(SCHEMA_DDL)
        cursor = conn.cursor()

        # 1. Seed Customers (120 customers across 2024-2026)
        customers_data: list[tuple[object, ...]] = []
        base_signup = datetime(2024, 6, 1)
        for idx in range(1, 121):
            first = rng.choice(FIRST_NAMES)
            last = rng.choice(LAST_NAMES)
            email = f"{first.lower()}.{last.lower()}{idx}@example.com"
            phone = (
                f"+1-555-{rng.randint(100, 999)}-{rng.randint(1000, 9999)}"
                if rng.random() > 0.18
                else None
            )
            country, cities = rng.choice(LOCATIONS)
            city = rng.choice(cities)
            segment = rng.choice(SEGMENTS)
            channel = rng.choice(CHANNELS)
            signup_dt = (base_signup + timedelta(days=rng.randint(0, 650))).strftime("%Y-%m-%d")
            is_active = 0 if rng.random() < 0.08 else 1
            customers_data.append(
                (first, last, email, phone, country, city, segment, channel, signup_dt, is_active)
            )

        cursor.executemany(
            """
            INSERT INTO customers (
                first_name, last_name, email, phone, country, city,
                customer_segment, marketing_channel, signup_date, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            customers_data,
        )

        # 2. Seed Products (20 curated products with realistic margins and edge cases)
        products_data: list[tuple[object, ...]] = []
        base_launch = datetime(2024, 1, 15)
        for sku, name, cat, subcat, price, cost, stock, disc in PRODUCTS_CATALOG:
            launch_dt = (base_launch + timedelta(days=rng.randint(0, 450))).strftime("%Y-%m-%d")
            # Introduce a couple of NULL subcategories for edge-case testing
            effective_subcat = None if sku in ("SKU-AC-003", "SKU-AP-001") else subcat
            products_data.append(
                (sku, name, cat, effective_subcat, price, cost, stock, disc, launch_dt)
            )

        cursor.executemany(
            """
            INSERT INTO products (
                sku, product_name, category, subcategory, unit_price,
                unit_cost, stock_quantity, discontinued, launch_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            products_data,
        )

        # 3. Seed Orders & Order Items across 8 quarters (2025 Q1 through 2026 Q4)
        # Quarter date ranges:
        quarters = [
            (datetime(2025, 1, 1), datetime(2025, 3, 31), 55),
            (datetime(2025, 4, 1), datetime(2025, 6, 30), 65),
            (datetime(2025, 7, 1), datetime(2025, 9, 30), 70),
            (datetime(2025, 10, 1), datetime(2025, 12, 31), 95),
            (datetime(2026, 1, 1), datetime(2026, 3, 31), 75),
            (datetime(2026, 4, 1), datetime(2026, 6, 30), 85),
            (datetime(2026, 7, 1), datetime(2026, 9, 30), 90),
            (datetime(2026, 10, 1), datetime(2026, 10, 29), 45),
        ]

        statuses = ["completed", "shipped", "processing", "cancelled", "returned", "refunded"]
        status_weights = [0.64, 0.12, 0.06, 0.10, 0.05, 0.03]

        order_count = 0
        order_item_count = 0

        for q_start, q_end, num_orders in quarters:
            span_seconds = int((q_end - q_start).total_seconds())
            for _ in range(num_orders):
                order_count += 1
                customer_id = rng.randint(1, len(customers_data))
                customer_country = str(customers_data[customer_id - 1][4])

                order_dt = q_start + timedelta(seconds=rng.randint(0, max(1, span_seconds)))
                status = rng.choices(statuses, weights=status_weights, k=1)[0]

                if status in ("completed", "shipped", "returned", "refunded"):
                    shipped_dt: str | None = (
                        order_dt + timedelta(days=rng.randint(1, 6), hours=rng.randint(1, 18))
                    ).strftime("%Y-%m-%d %H:%M:%S")
                else:
                    # processing or cancelled orders have NULL shipped_date
                    shipped_dt = None

                shipping_cost = round(rng.choice([0.0, 9.99, 14.99, 24.99, 39.99]), 2)
                payment_method = rng.choice(PAYMENT_METHODS)

                # Generate 1 to 4 distinct line items per order
                num_items = rng.randint(1, 4)
                chosen_product_indices = rng.sample(range(1, len(PRODUCTS_CATALOG) + 1), k=num_items)

                order_subtotal = 0.0
                items_to_insert: list[tuple[int, int, float, float | None, float]] = []

                for prod_id in chosen_product_indices:
                    prod_price = float(PRODUCTS_CATALOG[prod_id - 1][4])
                    qty = rng.randint(1, 5)
                    gross = round(prod_price * qty, 2)

                    # Edge case: NULL discount vs explicit 0.0 vs percentage discount
                    disc_roll = rng.random()
                    if disc_roll < 0.45:
                        discount_amt: float | None = None
                        line_total = gross
                    elif disc_roll < 0.70:
                        discount_amt = 0.0
                        line_total = gross
                    else:
                        pct = rng.choice([0.05, 0.10, 0.15, 0.20])
                        discount_amt = round(gross * pct, 2)
                        line_total = round(gross - discount_amt, 2)

                    order_subtotal = round(order_subtotal + line_total, 2)
                    items_to_insert.append((prod_id, qty, prod_price, discount_amt, line_total))

                total_amount = round(order_subtotal + shipping_cost, 2)

                cursor.execute(
                    """
                    INSERT INTO orders (
                        customer_id, order_date, shipped_date, status,
                        shipping_country, shipping_cost, payment_method, total_amount
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        customer_id,
                        order_dt.strftime("%Y-%m-%d %H:%M:%S"),
                        shipped_dt,
                        status,
                        customer_country,
                        shipping_cost,
                        payment_method,
                        total_amount,
                    ),
                )
                order_id = cursor.lastrowid
                assert order_id is not None

                for prod_id, qty, unit_price, discount_amt, line_total in items_to_insert:
                    order_item_count += 1
                    cursor.execute(
                        """
                        INSERT INTO order_items (
                            order_id, product_id, quantity, unit_price, discount_amount, line_total
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (order_id, prod_id, qty, unit_price, discount_amt, line_total),
                    )

        conn.commit()

    return {
        "customers": len(customers_data),
        "products": len(products_data),
        "orders": order_count,
        "order_items": order_item_count,
    }


if __name__ == "__main__":
    counts = seed_database()
    print(f"Database seeded successfully at '{DEFAULT_DB_PATH}': {counts}")
