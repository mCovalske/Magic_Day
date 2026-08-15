import os
from typing import Optional
import psycopg2
from cryptography.fernet import Fernet, InvalidToken

DATABASE_URL = os.getenv("DATABASE_URL")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")
if not ENCRYPTION_KEY:
    raise RuntimeError("ENCRYPTION_KEY is not set")
try:
    FERNET = Fernet(ENCRYPTION_KEY.encode())
except Exception as exc:
    raise RuntimeError("ENCRYPTION_KEY is invalid") from exc


def get_conn():
    return psycopg2.connect(DATABASE_URL, connect_timeout=10)


def encrypt(value: Optional[str]) -> str:
    return FERNET.encrypt((value or "").encode()).decode()


def decrypt(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return FERNET.decrypt(value.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        return ""


def init_db():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id BIGINT PRIMARY KEY,
                    name_enc TEXT NOT NULL DEFAULT '',
                    gender_enc TEXT NOT NULL DEFAULT '',
                    birthdate_enc TEXT NOT NULL DEFAULT '',
                    phone_enc TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL,
                    price_rub INTEGER NOT NULL CHECK (price_rub >= 0),
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id BIGSERIAL PRIMARY KEY,
                    telegram_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
                    product_name_snapshot TEXT NOT NULL,
                    customer_name_enc TEXT NOT NULL,
                    customer_phone_enc TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new'
                        CHECK (status IN ('new','confirmed','rejected','completed')),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    telegram_id BIGINT PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
                    time_local TEXT NOT NULL,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(telegram_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_subs_time ON subscriptions(active, time_local)")
            seed_products(cur)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def seed_products(cur):
    products = [
        ("Дзи 9 глаз", "Символ удачи, защиты и внутренней силы.", 5000, 1),
        ("Дзи 3 глаза", "Символ благополучия, энергии и движения вперёд.", 4500, 2),
        ("Дзи 2 глаза", "Символ гармонии и партнёрства.", 4000, 3),
        ("Дзи 1 глаз", "Символ ясности, концентрации и уверенного выбора.", 3500, 4),
    ]
    for name, description, price, sort_order in products:
        cur.execute("""
            INSERT INTO products (name, description, price_rub, sort_order)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (name) DO NOTHING
        """, (name, description, price, sort_order))


def ensure_user(telegram_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (telegram_id) VALUES (%s) ON CONFLICT DO NOTHING", (telegram_id,))
        conn.commit()
    finally:
        conn.close()


def get_user(telegram_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT name_enc, gender_enc, birthdate_enc, phone_enc, created_at
                FROM users WHERE telegram_id = %s
            """, (telegram_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "telegram_id": telegram_id,
        "name": decrypt(row[0]),
        "gender": decrypt(row[1]),
        "birthdate": decrypt(row[2]),
        "phone": decrypt(row[3]) if row[3] else "",
        "created_at": row[4],
    }


def update_user_field(telegram_id: int, field: str, value: str):
    columns = {"name": "name_enc", "gender": "gender_enc", "birthdate": "birthdate_enc", "phone": "phone_enc"}
    if field not in columns:
        raise ValueError("Unsupported user field")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE users SET {columns[field]} = %s, updated_at = CURRENT_TIMESTAMP WHERE telegram_id = %s", (encrypt(value), telegram_id))
        conn.commit()
    finally:
        conn.close()


def get_all_users(limit=1000):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id, name_enc, gender_enc, birthdate_enc, phone_enc, created_at FROM users ORDER BY created_at DESC LIMIT %s", (limit,))
            rows = cur.fetchall()
    finally:
        conn.close()
    return [{
        "telegram_id": r[0], "name": decrypt(r[1]), "gender": decrypt(r[2]),
        "birthdate": decrypt(r[3]), "phone": decrypt(r[4]) if r[4] else "", "created_at": r[5]
    } for r in rows]


def get_products(active_only=True):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            sql = "SELECT id, name, description, price_rub, is_active, sort_order FROM products"
            if active_only:
                sql += " WHERE is_active = TRUE"
            sql += " ORDER BY sort_order, id"
            cur.execute(sql)
            rows = cur.fetchall()
    finally:
        conn.close()
    return [{"id": r[0], "name": r[1], "description": r[2], "price_rub": r[3], "is_active": bool(r[4]), "sort_order": r[5]} for r in rows]


def get_product(product_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, description, price_rub, is_active, sort_order FROM products WHERE id = %s", (product_id,))
            r = cur.fetchone()
    finally:
        conn.close()
    return None if not r else {"id": r[0], "name": r[1], "description": r[2], "price_rub": r[3], "is_active": bool(r[4]), "sort_order": r[5]}


def get_product_by_name(name: str):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, description, price_rub, is_active, sort_order FROM products WHERE name = %s", (name,))
            r = cur.fetchone()
    finally:
        conn.close()
    return None if not r else {"id": r[0], "name": r[1], "description": r[2], "price_rub": r[3], "is_active": bool(r[4]), "sort_order": r[5]}


def add_product(name: str, description: str, price_rub: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM products")
            order = cur.fetchone()[0]
            cur.execute("INSERT INTO products (name, description, price_rub, sort_order) VALUES (%s,%s,%s,%s) RETURNING id", (name, description, price_rub, order))
            pid = cur.fetchone()[0]
        conn.commit()
        return pid
    finally:
        conn.close()


def update_product(product_id: int, field: str, value):
    if field not in {"name", "description", "price_rub"}:
        raise ValueError("Unsupported product field")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE products SET {field} = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s", (value, product_id))
        conn.commit()
    finally:
        conn.close()


def set_product_active(product_id: int, active: bool):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE products SET is_active = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s", (active, product_id))
        conn.commit()
    finally:
        conn.close()


def create_order(telegram_id: int, product_id: int, customer_name: str, customer_phone: str):
    product = get_product(product_id)
    if not product or not product["is_active"]:
        raise ValueError("Product unavailable")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO orders (telegram_id, product_id, product_name_snapshot, customer_name_enc, customer_phone_enc)
                VALUES (%s,%s,%s,%s,%s) RETURNING id
            """, (telegram_id, product_id, product["name"], encrypt(customer_name), encrypt(customer_phone)))
            oid = cur.fetchone()[0]
        conn.commit()
        return oid
    finally:
        conn.close()


def get_user_orders(telegram_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, product_name_snapshot, status, created_at
                FROM orders WHERE telegram_id = %s ORDER BY created_at DESC
            """, (telegram_id,))
            rows = cur.fetchall()
    finally:
        conn.close()
    return [{"id": r[0], "product_name": r[1], "status": r[2], "created_at": r[3]} for r in rows]


def get_order(order_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, telegram_id, product_id, product_name_snapshot,
                       customer_name_enc, customer_phone_enc, status, created_at
                FROM orders WHERE id = %s
            """, (order_id,))
            r = cur.fetchone()
    finally:
        conn.close()
    if not r:
        return None
    return {
        "id": r[0], "telegram_id": r[1], "product_id": r[2], "product_name": r[3],
        "customer_name": decrypt(r[4]), "customer_phone": decrypt(r[5]),
        "status": r[6], "created_at": r[7]
    }


def get_recent_orders(limit=30):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, telegram_id, product_name_snapshot, customer_name_enc,
                       customer_phone_enc, status, created_at
                FROM orders ORDER BY created_at DESC LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
    finally:
        conn.close()
    return [{
        "id": r[0], "telegram_id": r[1], "product_name": r[2],
        "customer_name": decrypt(r[3]), "customer_phone": decrypt(r[4]),
        "status": r[5], "created_at": r[6]
    } for r in rows]


def set_order_status(order_id: int, status: str):
    if status not in {"new", "confirmed", "rejected", "completed"}:
        raise ValueError("Invalid status")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE orders SET status=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s", (status, order_id))
        conn.commit()
    finally:
        conn.close()


def set_subscription(telegram_id: int, time_local: str, active=True):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO subscriptions (telegram_id, time_local, active)
                VALUES (%s,%s,%s)
                ON CONFLICT (telegram_id) DO UPDATE SET
                    time_local=EXCLUDED.time_local, active=EXCLUDED.active, updated_at=CURRENT_TIMESTAMP
            """, (telegram_id, time_local, active))
        conn.commit()
    finally:
        conn.close()


def get_subscription(telegram_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT time_local, active FROM subscriptions WHERE telegram_id=%s", (telegram_id,))
            r = cur.fetchone()
    finally:
        conn.close()
    return None if not r else {"time": r[0], "active": bool(r[1])}


def get_active_subscriptions(time_local: Optional[str] = None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if time_local:
                cur.execute("SELECT telegram_id, time_local FROM subscriptions WHERE active=TRUE AND time_local=%s", (time_local,))
            else:
                cur.execute("SELECT telegram_id, time_local FROM subscriptions WHERE active=TRUE")
            rows = cur.fetchall()
    finally:
        conn.close()
    return [{"telegram_id": r[0], "time": r[1]} for r in rows]


def get_stats():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users"); users = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM subscriptions WHERE active=TRUE"); subs = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM products WHERE is_active=TRUE"); products = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM orders"); orders = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM orders WHERE status='new'"); new_orders = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM orders WHERE status='confirmed'"); confirmed = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM orders WHERE status='rejected'"); rejected = cur.fetchone()[0]
    finally:
        conn.close()
    return {"users": users, "subscriptions": subs, "products": products, "orders": orders, "new_orders": new_orders, "confirmed": confirmed, "rejected": rejected}


def acquire_bot_lock():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT pg_try_advisory_lock(8142026081501)")
    if not cur.fetchone()[0]:
        cur.close(); conn.close()
        raise RuntimeError("Another bot instance is already running with this database.")
    return conn, cur
