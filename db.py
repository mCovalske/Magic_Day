# BUILD: PREDBOT-2026-08-18-ADMIN-STAGE1-01
import os
from typing import Optional
import psycopg2
from cryptography.fernet import Fernet, InvalidToken

CONSENT_VERSION = "2026-08-18"

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



def reset_all_user_data_once():
    """
    One-time destructive reset for development.
    Removes all user profiles and dependent user data, but keeps the catalog.
    """
    enabled = os.getenv("RESET_USER_DATA_ON_START", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if not enabled:
        return False

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS system_migrations (
                    migration_key TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                SELECT 1
                FROM system_migrations
                WHERE migration_key = 'RESET_ALL_USER_DATA_2026_08_18'
            """)
            if cur.fetchone():
                conn.commit()
                return False

            cur.execute("""
                TRUNCATE TABLE
                    reviews,
                    magic8_usage,
                    subscriptions,
                    orders,
                    users
                RESTART IDENTITY CASCADE
            """)

            cur.execute("""
                INSERT INTO system_migrations (migration_key)
                VALUES ('RESET_ALL_USER_DATA_2026_08_18')
            """)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
                    username_enc TEXT,
                    consent_given BOOLEAN NOT NULL DEFAULT FALSE,
                    consent_version TEXT,
                    consent_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                ALTER TABLE users ADD COLUMN IF NOT EXISTS consent_given BOOLEAN NOT NULL DEFAULT FALSE
            """)
            cur.execute("""
                ALTER TABLE users ADD COLUMN IF NOT EXISTS consent_version TEXT
            """)
            cur.execute("""
                ALTER TABLE users ADD COLUMN IF NOT EXISTS consent_at TIMESTAMPTZ
            """)
            cur.execute("""
                ALTER TABLE users ADD COLUMN IF NOT EXISTS username_enc TEXT
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL,
                    price_rub INTEGER NOT NULL CHECK (price_rub >= 0),
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    image_file TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                ALTER TABLE products ADD COLUMN IF NOT EXISTS image_file TEXT
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
                        CHECK (status IN ('new','confirmed','assembling','ready','shipping','completed','rejected')),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check")
            cur.execute("""
                ALTER TABLE orders ADD CONSTRAINT orders_status_check
                CHECK (status IN ('new','confirmed','assembling','ready','shipping','completed','rejected'))
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
            cur.execute("""CREATE TABLE IF NOT EXISTS magic8_usage (telegram_id BIGINT REFERENCES users(telegram_id) ON DELETE CASCADE, usage_date DATE, question_count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (telegram_id, usage_date))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS reviews (id BIGSERIAL PRIMARY KEY, order_id BIGINT UNIQUE REFERENCES orders(id) ON DELETE CASCADE, telegram_id BIGINT REFERENCES users(telegram_id) ON DELETE CASCADE, rating INTEGER CHECK (rating BETWEEN 1 AND 5), review_text TEXT, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP)""")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS admin_audit (
                    id BIGSERIAL PRIMARY KEY, admin_id BIGINT NOT NULL, action TEXT NOT NULL,
                    entity_type TEXT, entity_id TEXT, details TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS admin_settings (
                    id INTEGER PRIMARY KEY CHECK (id=1),
                    notify_new_order BOOLEAN NOT NULL DEFAULT TRUE,
                    notify_status_change BOOLEAN NOT NULL DEFAULT TRUE,
                    notify_new_user BOOLEAN NOT NULL DEFAULT FALSE,
                    notify_security BOOLEAN NOT NULL DEFAULT TRUE,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS daily_predictions (
                    id BIGSERIAL PRIMARY KEY,
                    text TEXT NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS magic8_answers (
                    id BIGSERIAL PRIMARY KEY,
                    text TEXT NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS legal_documents (
                    id BIGSERIAL PRIMARY KEY,
                    doc_key TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    version TEXT NOT NULL DEFAULT '1.0',
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS backup_log (
                    id BIGSERIAL PRIMARY KEY,
                    admin_id BIGINT NOT NULL,
                    backup_type TEXT NOT NULL,
                    file_name TEXT,
                    status TEXT NOT NULL,
                    details TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            seed_stage2_content(cur)
            cur.execute("INSERT INTO admin_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
            seed_products(cur)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def seed_products(cur):
    products = [
        ("Дзи 9 глаз", "Символ удачи, защиты и внутренней силы.", 5000, "dzi_9.png", 1),
        ("Дзи 3 глаза", "Символ благополучия, энергии и движения вперёд.", 4500, "dzi_3.png", 2),
        ("Дзи 2 глаза", "Символ гармонии и партнёрства.", 4000, "dzi_2.png", 3),
        ("Дзи 1 глаз", "Символ ясности, концентрации и уверенного выбора.", 3500, "dzi_1.png", 4),
        ("Дзи 6 глаз", "Символ спокойствия, мудрости и внутреннего равновесия.", 4300, "dzi_6.png", 5),
        ("Дзи 12 глаз", "Символ интуиции, процветания и новых возможностей.", 5200, "dzi_12.png", 6),
    ]
    for name, description, price, image_file, sort_order in products:
        cur.execute("""
            INSERT INTO products (name, description, price_rub, image_file, sort_order)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET
                description = EXCLUDED.description,
                price_rub = EXCLUDED.price_rub,
                image_file = EXCLUDED.image_file,
                sort_order = EXCLUDED.sort_order,
                updated_at = CURRENT_TIMESTAMP
        """, (name, description, price, image_file, sort_order))



def seed_stage2_content(cur):
    cur.execute("SELECT COUNT(*) FROM daily_predictions")
    if cur.fetchone()[0] == 0:
        try:
            from predictions import PREDICTIONS
        except Exception:
            PREDICTIONS=[]
        for i,text in enumerate(PREDICTIONS,1):
            cur.execute("INSERT INTO daily_predictions(text,sort_order) VALUES(%s,%s)",(text,i))
    cur.execute("SELECT COUNT(*) FROM magic8_answers")
    if cur.fetchone()[0] == 0:
        answers=["Да.","Определённо да!","Без сомнений.","Скорее да, чем нет.","Пока не ясно, попробуйте позже.","Скорее нет, чем да.","Нет.","Определённо нет.","Даже не думайте.","Мой ответ — нет."]
        for i,text in enumerate(answers,1): cur.execute("INSERT INTO magic8_answers(text,sort_order) VALUES(%s,%s)",(text,i))
    cur.execute("SELECT COUNT(*) FROM legal_documents")
    if cur.fetchone()[0] == 0:
        docs=[
            ("policy","Политика ПДн","/legal/01_policy_personal_data.html"),("consent","Согласие ПДн","/legal/02_consent_personal_data.html"),
            ("confidentiality","Конфиденциальность","/legal/03_confidentiality_security.html"),("agreement","Пользовательское соглашение","/legal/04_user_agreement.html"),
            ("offer","Публичная оферта","/legal/05_public_offer.html"),("disclaimer","Дисклеймер","/legal/06_disclaimer_predictions.html"),
            ("marketing","Рекламное согласие","/legal/07_marketing_consent.html"),("rights","Права субъекта ПДн","/legal/08_data_subject_requests.html")]
        for key,title,url in docs: cur.execute("INSERT INTO legal_documents(doc_key,title,url) VALUES(%s,%s,%s)",(key,title,url))

def get_daily_predictions(active_only=True):
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            sql="SELECT id,text,is_active,sort_order FROM daily_predictions" + (" WHERE is_active=TRUE" if active_only else "") + " ORDER BY sort_order,id"
            cur.execute(sql); rows=cur.fetchall()
        return [{"id":r[0],"text":r[1],"is_active":bool(r[2]),"sort_order":r[3]} for r in rows]
    finally: conn.close()

def get_random_daily_prediction():
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT text FROM daily_predictions WHERE is_active=TRUE ORDER BY RANDOM() LIMIT 1")
            r=cur.fetchone(); return r[0] if r else None
    finally: conn.close()

def add_daily_prediction(text):
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM daily_predictions"); order=cur.fetchone()[0]
            cur.execute("INSERT INTO daily_predictions(text,sort_order) VALUES(%s,%s) RETURNING id",(text,order)); pid=cur.fetchone()[0]
        conn.commit(); return pid
    finally: conn.close()

def update_daily_prediction(pid,text):
    conn=get_conn()
    try:
        with conn.cursor() as cur: cur.execute("UPDATE daily_predictions SET text=%s,updated_at=CURRENT_TIMESTAMP WHERE id=%s",(text,pid))
        conn.commit()
    finally: conn.close()

def set_daily_prediction_active(pid,active):
    conn=get_conn()
    try:
        with conn.cursor() as cur: cur.execute("UPDATE daily_predictions SET is_active=%s,updated_at=CURRENT_TIMESTAMP WHERE id=%s",(active,pid))
        conn.commit()
    finally: conn.close()

def delete_daily_prediction(pid):
    conn=get_conn()
    try:
        with conn.cursor() as cur: cur.execute("DELETE FROM daily_predictions WHERE id=%s",(pid,)); deleted=cur.rowcount>0
        conn.commit(); return deleted
    finally: conn.close()

def get_magic8_answers(active_only=True):
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            sql="SELECT id,text,is_active,sort_order FROM magic8_answers" + (" WHERE is_active=TRUE" if active_only else "") + " ORDER BY sort_order,id"
            cur.execute(sql); rows=cur.fetchall()
        return [{"id":r[0],"text":r[1],"is_active":bool(r[2]),"sort_order":r[3]} for r in rows]
    finally: conn.close()

def get_random_magic8_answer():
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT text FROM magic8_answers WHERE is_active=TRUE ORDER BY RANDOM() LIMIT 1"); r=cur.fetchone(); return r[0] if r else "Пока не ясно."
    finally: conn.close()

def add_magic8_answer(text):
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM magic8_answers"); order=cur.fetchone()[0]
            cur.execute("INSERT INTO magic8_answers(text,sort_order) VALUES(%s,%s) RETURNING id",(text,order)); pid=cur.fetchone()[0]
        conn.commit(); return pid
    finally: conn.close()

def update_magic8_answer(pid,text):
    conn=get_conn()
    try:
        with conn.cursor() as cur: cur.execute("UPDATE magic8_answers SET text=%s,updated_at=CURRENT_TIMESTAMP WHERE id=%s",(text,pid))
        conn.commit()
    finally: conn.close()

def set_magic8_active(pid,active):
    conn=get_conn()
    try:
        with conn.cursor() as cur: cur.execute("UPDATE magic8_answers SET is_active=%s,updated_at=CURRENT_TIMESTAMP WHERE id=%s",(active,pid))
        conn.commit()
    finally: conn.close()

def get_legal_documents():
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT doc_key,title,url,version,is_active,updated_at FROM legal_documents ORDER BY id"); rows=cur.fetchall()
        return [{"key":r[0],"title":r[1],"url":r[2],"version":r[3],"active":bool(r[4]),"updated_at":r[5]} for r in rows]
    finally: conn.close()

def log_backup(admin_id,backup_type,file_name,status,details=""):
    conn=get_conn()
    try:
        with conn.cursor() as cur: cur.execute("INSERT INTO backup_log(admin_id,backup_type,file_name,status,details) VALUES(%s,%s,%s,%s,%s)",(admin_id,backup_type,file_name,status,details[:2000]))
        conn.commit()
    finally: conn.close()

def get_last_backup():
    conn=get_conn()
    try:
        with conn.cursor() as cur: cur.execute("SELECT backup_type,file_name,status,details,created_at FROM backup_log ORDER BY created_at DESC LIMIT 1"); r=cur.fetchone()
        return None if not r else {"type":r[0],"file":r[1],"status":r[2],"details":r[3],"created_at":r[4]}
    finally: conn.close()


def ensure_user(telegram_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (telegram_id) VALUES (%s) ON CONFLICT DO NOTHING", (telegram_id,))
        conn.commit()
    finally:
        conn.close()


def give_consent(telegram_id: int):
    ensure_user(telegram_id)
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""UPDATE users SET consent_given=TRUE, consent_version=%s, consent_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE telegram_id=%s""",(CONSENT_VERSION, telegram_id))
        conn.commit()
    finally: conn.close()


def delete_user_data(telegram_id: int):
    conn=get_conn()
    try:
        with conn.cursor() as cur: cur.execute("DELETE FROM users WHERE telegram_id=%s",(telegram_id,))
        conn.commit()
    finally: conn.close()


def get_user(telegram_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT name_enc, gender_enc, birthdate_enc, phone_enc,
                       username_enc, consent_given, consent_version, consent_at, created_at
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
        "username": decrypt(row[4]) if row[4] else "",
        "consent_given": bool(row[5]),
        "consent_version": row[6] or "",
        "consent_at": row[7],
        "created_at": row[8],
    }


def update_username(telegram_id: int, username: str):
    username=(username or "").strip().lstrip("@").lower()
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET username_enc=%s, updated_at=CURRENT_TIMESTAMP WHERE telegram_id=%s",(encrypt(username),telegram_id))
        conn.commit()
    finally: conn.close()

def get_user_by_username(username: str):
    username=(username or "").strip().lstrip("@").lower()
    if not username: return None
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id, username_enc FROM users WHERE username_enc IS NOT NULL")
            rows=cur.fetchall()
    finally: conn.close()
    for telegram_id, username_enc in rows:
        if decrypt(username_enc).lower()==username:
            return get_user(telegram_id)
    return None


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
            sql = "SELECT id, name, description, price_rub, image_file, is_active, sort_order FROM products"
            if active_only:
                sql += " WHERE is_active = TRUE"
            sql += " ORDER BY sort_order, id"
            cur.execute(sql)
            rows = cur.fetchall()
    finally:
        conn.close()
    return [{"id": r[0], "name": r[1], "description": r[2], "price_rub": r[3], "image_file": r[4], "is_active": bool(r[5]), "sort_order": r[6]} for r in rows]


def get_product(product_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, description, price_rub, image_file, is_active, sort_order FROM products WHERE id = %s", (product_id,))
            r = cur.fetchone()
    finally:
        conn.close()
    return None if not r else {"id": r[0], "name": r[1], "description": r[2], "price_rub": r[3], "image_file": r[4], "is_active": bool(r[5]), "sort_order": r[6]}


def get_product_by_name(name: str):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, description, price_rub, image_file, is_active, sort_order FROM products WHERE name = %s", (name,))
            r = cur.fetchone()
    finally:
        conn.close()
    return None if not r else {"id": r[0], "name": r[1], "description": r[2], "price_rub": r[3], "image_file": r[4], "is_active": bool(r[5]), "sort_order": r[6]}


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


def update_product_image(product_id:int,image_file):
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE products SET image_file=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",(image_file,product_id))
        conn.commit()
    finally: conn.close()


def set_product_active(product_id: int, active: bool):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE products SET is_active = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s", (active, product_id))
        conn.commit()
    finally:
        conn.close()



def delete_product(product_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM orders WHERE product_id = %s", (product_id,))
            order_count = cur.fetchone()[0]
            if order_count:
                return False, order_count

            cur.execute("DELETE FROM products WHERE id = %s", (product_id,))
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted, 0
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
    if status not in {"new", "confirmed", "assembling", "ready", "shipping", "completed", "rejected"}:
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


def get_analytics(days=None):
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            if days:
                since="CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')"; arg=(days,)
                cur.execute(f"SELECT COUNT(*) FROM users WHERE created_at >= {since}",arg); users=cur.fetchone()[0]
                cur.execute(f"SELECT COUNT(*) FROM orders WHERE created_at >= {since}",arg); orders=cur.fetchone()[0]
                cur.execute(f"SELECT COUNT(*) FROM orders WHERE created_at >= {since} AND status='new'",arg); new_orders=cur.fetchone()[0]
            else:
                cur.execute("SELECT COUNT(*) FROM users"); users=cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM orders"); orders=cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM orders WHERE status='new'"); new_orders=cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM orders WHERE status='confirmed'"); confirmed=cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM orders WHERE status='assembling'"); assembling=cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM orders WHERE status='ready'"); ready=cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM orders WHERE status='shipping'"); shipping=cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM orders WHERE status='completed'"); completed=cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM orders WHERE status='rejected'"); rejected=cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM users WHERE created_at>=CURRENT_DATE"); new_today=cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM subscriptions WHERE active=TRUE"); subs=cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM reviews"); reviews=cur.fetchone()[0]
            cur.execute("SELECT COALESCE(AVG(rating),0) FROM reviews"); avg_rating=float(cur.fetchone()[0])
            cur.execute("SELECT product_name_snapshot,COUNT(*) FROM orders GROUP BY product_name_snapshot ORDER BY COUNT(*) DESC LIMIT 1"); top=cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM users WHERE consent_given=TRUE"); consented=cur.fetchone()[0]
        return {"users":users,"new_today":new_today,"orders":orders,"new_orders":new_orders,"confirmed":confirmed,"assembling":assembling,"ready":ready,"shipping":shipping,"completed":completed,"rejected":rejected,"subscriptions":subs,"reviews":reviews,"avg_rating":avg_rating,"top_product":top[0] if top else None,"consented":consented}
    finally: conn.close()

def search_users(query,limit=20):
    q=(query or '').lower().strip(); conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id,name_enc,phone_enc,username_enc,birthdate_enc,created_at FROM users ORDER BY created_at DESC")
            rows=cur.fetchall()
    finally: conn.close()
    result=[]
    for r in rows:
        vals=[str(r[0]),decrypt(r[1]),decrypt(r[2]) if r[2] else '',decrypt(r[3]) if r[3] else '',decrypt(r[4])]
        if q in ' '.join(vals).lower():
            result.append({"telegram_id":r[0],"name":vals[1],"phone":vals[2],"username":vals[3],"birthdate":vals[4],"created_at":r[5]})
            if len(result)>=limit: break
    return result

def get_user_admin_card(telegram_id):
    u=get_user(telegram_id)
    return None if not u else {"user":u,"orders":get_user_orders(telegram_id)}

def add_admin_audit(admin_id,action,entity_type='',entity_id='',details=''):
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO admin_audit(admin_id,action,entity_type,entity_id,details) VALUES(%s,%s,%s,%s,%s)",(admin_id,action,entity_type,str(entity_id),details[:2000]))
        conn.commit()
    finally: conn.close()

def get_admin_audit(limit=30):
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id,admin_id,action,entity_type,entity_id,details,created_at FROM admin_audit ORDER BY created_at DESC LIMIT %s",(limit,)); return cur.fetchall()
    finally: conn.close()

def get_admin_settings():
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT notify_new_order,notify_status_change,notify_new_user,notify_security FROM admin_settings WHERE id=1"); r=cur.fetchone()
    finally: conn.close()
    if not r: return {"new_order":True,"status_change":True,"new_user":False,"security":True}
    return {"new_order":bool(r[0]),"status_change":bool(r[1]),"new_user":bool(r[2]),"security":bool(r[3])}

def set_admin_notifications(field,enabled):
    cols={"new_order":"notify_new_order","status_change":"notify_status_change","new_user":"notify_new_user","security":"notify_security"}
    if field not in cols: raise ValueError('unsupported setting')
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE admin_settings SET {cols[field]}=%s,updated_at=CURRENT_TIMESTAMP WHERE id=1",(enabled,)); conn.commit()
    finally: conn.close()

def get_broadcast_recipients(audience):
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            if audience=='all': cur.execute("SELECT telegram_id FROM users WHERE consent_given=TRUE")
            elif audience=='subscribed': cur.execute("SELECT telegram_id FROM subscriptions WHERE active=TRUE")
            elif audience=='buyers': cur.execute("SELECT DISTINCT telegram_id FROM orders")
            else: cur.execute("SELECT telegram_id FROM users WHERE consent_given=TRUE AND created_at>=CURRENT_DATE")
            return [r[0] for r in cur.fetchall()]
    finally: conn.close()

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


def get_magic8_remaining(telegram_id):
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT question_count FROM magic8_usage WHERE telegram_id=%s AND usage_date=CURRENT_DATE",(telegram_id,)); r=cur.fetchone()
            return 3-(r[0] if r else 0)
    finally: conn.close()


def consume_magic8_question(telegram_id):
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO magic8_usage (telegram_id,usage_date,question_count) VALUES (%s,CURRENT_DATE,0) ON CONFLICT DO NOTHING",(telegram_id,))
            cur.execute("UPDATE magic8_usage SET question_count=question_count+1 WHERE telegram_id=%s AND usage_date=CURRENT_DATE AND question_count<3 RETURNING question_count",(telegram_id,)); r=cur.fetchone(); conn.commit()
            return (False,0) if not r else (True,3-r[0])
    finally: conn.close()


def add_review(order_id,telegram_id,rating,text):
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO reviews(order_id,telegram_id,rating,review_text) VALUES(%s,%s,%s,%s) ON CONFLICT(order_id) DO UPDATE SET rating=EXCLUDED.rating,review_text=EXCLUDED.review_text",(order_id,telegram_id,rating,text[:1000])); conn.commit()
    finally: conn.close()


def get_recent_reviews(limit=20):
    conn=get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id,order_id,telegram_id,rating,review_text,created_at FROM reviews ORDER BY created_at DESC LIMIT %s",(limit,)); return cur.fetchall()
    finally: conn.close()


def acquire_bot_lock():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT pg_try_advisory_lock(8142026081501)")
    if not cur.fetchone()[0]:
        cur.close(); conn.close()
        raise RuntimeError("Another bot instance is already running with this database.")
    return conn, cur
