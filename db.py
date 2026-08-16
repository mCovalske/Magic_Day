import os
import psycopg2
from cryptography.fernet import Fernet, InvalidToken

DATABASE_URL = os.getenv("DATABASE_URL")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not DATABASE_URL: raise RuntimeError("DATABASE_URL is not set")
if not ENCRYPTION_KEY: raise RuntimeError("ENCRYPTION_KEY is not set")
try: FERNET = Fernet(ENCRYPTION_KEY.encode())
except Exception as exc: raise RuntimeError("Invalid ENCRYPTION_KEY") from exc

def get_conn():
    return psycopg2.connect(DATABASE_URL, connect_timeout=10)

def enc(v): return FERNET.encrypt((v or "").encode()).decode()
def dec(v):
    if not v: return ""
    try: return FERNET.decrypt(v.encode()).decode()
    except (InvalidToken, ValueError, TypeError): return ""

def init_db():
    conn=get_conn()
    try:
        with conn.cursor() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS users(
                telegram_id BIGINT PRIMARY KEY,
                name_enc TEXT NOT NULL DEFAULT '',
                gender_enc TEXT NOT NULL DEFAULT '',
                birthdate_enc TEXT NOT NULL DEFAULT '',
                phone_enc TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            c.execute("""CREATE TABLE IF NOT EXISTS products(
                id SERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT NOT NULL,
                price_rub INTEGER NOT NULL CHECK(price_rub>=0), is_active BOOLEAN NOT NULL DEFAULT TRUE,
                sort_order INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            c.execute("""CREATE TABLE IF NOT EXISTS orders(
                id BIGSERIAL PRIMARY KEY, telegram_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                product_id INTEGER REFERENCES products(id) ON DELETE SET NULL, product_name_snapshot TEXT NOT NULL,
                customer_name_enc TEXT NOT NULL, customer_phone_enc TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new' CHECK(status IN('new','confirmed','rejected','completed')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            c.execute("""CREATE TABLE IF NOT EXISTS subscriptions(
                telegram_id BIGINT PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
                time_local TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            c.execute("""CREATE TABLE IF NOT EXISTS ball_questions(
                telegram_id BIGINT PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
                question_date DATE NOT NULL, question_count INTEGER NOT NULL DEFAULT 0)""")
            c.execute("""CREATE TABLE IF NOT EXISTS reviews(
                id BIGSERIAL PRIMARY KEY, telegram_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                order_id BIGINT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5), text TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            seed(c)
        conn.commit()
    finally: conn.close()

def seed(c):
    items=[
        ("Дзи 9 глаз","Символ удачи, защиты и внутренней силы.",5000,1),
        ("Дзи 3 глаза","Символ благополучия, энергии и движения вперёд.",4500,2),
        ("Дзи 2 глаза","Символ гармонии и партнёрства.",4000,3),
        ("Дзи 1 глаз","Символ ясности, концентрации и уверенного выбора.",3500,4),
        ("Дзи 5 глаз","Символ движения к целям и новых возможностей.",4200,5),
        ("Дзи 6 глаз","Символ внутреннего равновесия и спокойствия.",4300,6),
    ]
    for x in items:
        c.execute("INSERT INTO products(name,description,price_rub,sort_order) VALUES(%s,%s,%s,%s) ON CONFLICT(name) DO NOTHING",x)

def ensure_user(uid):
    con=get_conn()
    try:
        with con.cursor() as c: c.execute("INSERT INTO users(telegram_id) VALUES(%s) ON CONFLICT DO NOTHING",(uid,))
        con.commit()
    finally: con.close()

def get_user(uid):
    con=get_conn()
    try:
        with con.cursor() as c:
            c.execute("SELECT name_enc,gender_enc,birthdate_enc,phone_enc FROM users WHERE telegram_id=%s",(uid,))
            r=c.fetchone()
    finally: con.close()
    return None if not r else {"name":dec(r[0]),"gender":dec(r[1]),"birthdate":dec(r[2]),"phone":dec(r[3])}

def update_user(uid, field, value):
    cols={"name":"name_enc","gender":"gender_enc","birthdate":"birthdate_enc","phone":"phone_enc"}
    if field not in cols: raise ValueError("invalid user field")
    con=get_conn()
    try:
        with con.cursor() as c: c.execute(f"UPDATE users SET {cols[field]}=%s,updated_at=CURRENT_TIMESTAMP WHERE telegram_id=%s",(enc(value),uid))
        con.commit()
    finally: con.close()

def get_products(active_only=True):
    con=get_conn()
    try:
        with con.cursor() as c:
            c.execute("SELECT id,name,description,price_rub,is_active,sort_order FROM products WHERE is_active=%s ORDER BY sort_order,id",(True,)) if active_only else c.execute("SELECT id,name,description,price_rub,is_active,sort_order FROM products ORDER BY sort_order,id")
            rows=c.fetchall()
    finally: con.close()
    return [dict(id=r[0],name=r[1],description=r[2],price_rub=r[3],is_active=bool(r[4]),sort_order=r[5]) for r in rows]

def get_product(pid):
    con=get_conn()
    try:
        with con.cursor() as c: c.execute("SELECT id,name,description,price_rub,is_active,sort_order FROM products WHERE id=%s",(pid,)); r=c.fetchone()
    finally: con.close()
    return None if not r else dict(id=r[0],name=r[1],description=r[2],price_rub=r[3],is_active=bool(r[4]),sort_order=r[5])

def get_product_by_name(name):
    con=get_conn()
    try:
        with con.cursor() as c: c.execute("SELECT id,name,description,price_rub,is_active,sort_order FROM products WHERE name=%s",(name,)); r=c.fetchone()
    finally: con.close()
    return None if not r else dict(id=r[0],name=r[1],description=r[2],price_rub=r[3],is_active=bool(r[4]),sort_order=r[5])

def add_product(name,desc,price):
    con=get_conn()
    try:
        with con.cursor() as c:
            c.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM products"); order=c.fetchone()[0]
            c.execute("INSERT INTO products(name,description,price_rub,sort_order) VALUES(%s,%s,%s,%s)",(name,desc,price,order))
        con.commit()
    finally: con.close()

def update_product(pid,field,value):
    if field not in {"name","description","price_rub"}: raise ValueError("invalid product field")
    con=get_conn()
    try:
        with con.cursor() as c: c.execute(f"UPDATE products SET {field}=%s,updated_at=CURRENT_TIMESTAMP WHERE id=%s",(value,pid))
        con.commit()
    finally: con.close()

def set_product_active(pid,active):
    con=get_conn()
    try:
        with con.cursor() as c: c.execute("UPDATE products SET is_active=%s,updated_at=CURRENT_TIMESTAMP WHERE id=%s",(active,pid))
        con.commit()
    finally: con.close()

def delete_product(pid):
    con=get_conn()
    try:
        with con.cursor() as c:
            c.execute("SELECT COUNT(*) FROM orders WHERE product_id=%s",(pid,))
            if c.fetchone()[0]>0: return False
            c.execute("DELETE FROM products WHERE id=%s",(pid,))
            deleted=c.rowcount>0
        con.commit(); return deleted
    finally: con.close()

def create_order(uid,pid,name,phone):
    p=get_product(pid)
    if not p or not p["is_active"]: raise ValueError("product unavailable")
    con=get_conn()
    try:
        with con.cursor() as c:
            c.execute("""INSERT INTO orders(telegram_id,product_id,product_name_snapshot,customer_name_enc,customer_phone_enc)
                         VALUES(%s,%s,%s,%s,%s) RETURNING id""",(uid,pid,p["name"],enc(name),enc(phone)))
            oid=c.fetchone()[0]
        con.commit(); return oid
    finally: con.close()

def get_order(oid):
    con=get_conn()
    try:
        with con.cursor() as c:
            c.execute("SELECT id,telegram_id,product_id,product_name_snapshot,customer_name_enc,customer_phone_enc,status,created_at FROM orders WHERE id=%s",(oid,))
            r=c.fetchone()
    finally: con.close()
    return None if not r else {"id":r[0],"telegram_id":r[1],"product_id":r[2],"product_name":r[3],"customer_name":dec(r[4]),"customer_phone":dec(r[5]),"status":r[6],"created_at":r[7]}

def user_orders(uid):
    con=get_conn()
    try:
        with con.cursor() as c:
            c.execute("SELECT id,product_name_snapshot,status,created_at FROM orders WHERE telegram_id=%s ORDER BY created_at DESC",(uid,)); rows=c.fetchall()
    finally: con.close()
    return [{"id":r[0],"product_name":r[1],"status":r[2],"created_at":r[3]} for r in rows]

def recent_orders(limit=30):
    con=get_conn()
    try:
        with con.cursor() as c:
            c.execute("SELECT id,telegram_id,product_name_snapshot,customer_name_enc,customer_phone_enc,status,created_at FROM orders ORDER BY created_at DESC LIMIT %s",(limit,)); rows=c.fetchall()
    finally: con.close()
    return [{"id":r[0],"telegram_id":r[1],"product_name":r[2],"customer_name":dec(r[3]),"customer_phone":dec(r[4]),"status":r[5],"created_at":r[6]} for r in rows]

def set_order_status(oid,status):
    if status not in {"new","confirmed","rejected","completed"}: raise ValueError("invalid status")
    con=get_conn()
    try:
        with con.cursor() as c: c.execute("UPDATE orders SET status=%s,updated_at=CURRENT_TIMESTAMP WHERE id=%s",(status,oid))
        con.commit()
    finally: con.close()

def set_subscription(uid,time_local,active=True):
    con=get_conn()
    try:
        with con.cursor() as c: c.execute("""INSERT INTO subscriptions(telegram_id,time_local,active) VALUES(%s,%s,%s)
        ON CONFLICT(telegram_id) DO UPDATE SET time_local=EXCLUDED.time_local,active=EXCLUDED.active,updated_at=CURRENT_TIMESTAMP""",(uid,time_local,active))
        con.commit()
    finally: con.close()

def get_subscription(uid):
    con=get_conn()
    try:
        with con.cursor() as c: c.execute("SELECT time_local,active FROM subscriptions WHERE telegram_id=%s",(uid,)); r=c.fetchone()
    finally: con.close()
    return None if not r else {"time":r[0],"active":bool(r[1])}

def due_subscriptions(time_local):
    con=get_conn()
    try:
        with con.cursor() as c: c.execute("SELECT telegram_id FROM subscriptions WHERE active=TRUE AND time_local=%s",(time_local,)); rows=c.fetchall()
    finally: con.close()
    return [r[0] for r in rows]

def ball_state(uid):
    con=get_conn()
    try:
        with con.cursor() as c:
            c.execute("SELECT question_date,question_count FROM ball_questions WHERE telegram_id=%s",(uid,)); r=c.fetchone()
            from datetime import date
            today=date.today()
            if not r or r[0]!=today:
                c.execute("""INSERT INTO ball_questions(telegram_id,question_date,question_count) VALUES(%s,%s,0)
                ON CONFLICT(telegram_id) DO UPDATE SET question_date=EXCLUDED.question_date,question_count=0""",(uid,today)); con.commit()
                return 0
            return r[1]
    finally: con.close()

def increment_ball(uid):
    con=get_conn()
    try:
        with con.cursor() as c:
            c.execute("UPDATE ball_questions SET question_count=question_count+1 WHERE telegram_id=%s",(uid,))
        con.commit()
    finally: con.close()

def add_review(uid,oid,rating,text):
    con=get_conn()
    try:
        with con.cursor() as c:
            c.execute("SELECT status,telegram_id FROM orders WHERE id=%s",(oid,)); r=c.fetchone()
            if not r or r[1]!=uid or r[0]!="completed": return False
            c.execute("SELECT 1 FROM reviews WHERE order_id=%s",(oid,))
            if c.fetchone(): return False
            c.execute("INSERT INTO reviews(telegram_id,order_id,rating,text) VALUES(%s,%s,%s,%s)",(uid,oid,rating,(text or "")[:1000]))
        con.commit(); return True
    finally: con.close()

def get_reviewable_orders(uid):
    con=get_conn()
    try:
        with con.cursor() as c:
            c.execute("""SELECT o.id,o.product_name_snapshot FROM orders o
            LEFT JOIN reviews r ON r.order_id=o.id
            WHERE o.telegram_id=%s AND o.status='completed' AND r.id IS NULL
            ORDER BY o.created_at DESC""",(uid,)); rows=c.fetchall()
    finally: con.close()
    return [{"id":r[0],"product_name":r[1]} for r in rows]

def get_reviews(limit=30):
    con=get_conn()
    try:
        with con.cursor() as c:
            c.execute("SELECT r.id,r.order_id,r.rating,r.text,r.created_at,r.telegram_id,o.product_name_snapshot FROM reviews r JOIN orders o ON o.id=r.order_id ORDER BY r.created_at DESC LIMIT %s",(limit,)); rows=c.fetchall()
    finally: con.close()
    return [{"id":r[0],"order_id":r[1],"rating":r[2],"text":r[3],"created_at":r[4],"telegram_id":r[5],"product_name":r[6]} for r in rows]

def acquire_bot_lock():
    con=get_conn(); cur=con.cursor()
    cur.execute("SELECT pg_try_advisory_lock(8142026081501)")
    if not cur.fetchone()[0]:
        cur.close(); con.close()
        raise RuntimeError("BOT_LOCK_BUSY")
    return con,cur
