# BUILD: PREDBOT-2026-08-23-MINI-APP-RUNTIME-02
import asyncio
import html
import os
import re
import csv
import subprocess
import shutil as file_shutil
import hmac
import hashlib
import json
import time
from urllib.parse import parse_qsl, urlencode
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
from aiohttp import web

from db import (
    reset_all_user_data_once,
    CONSENT_VERSION,
    add_review, consume_magic8_question, delete_product, delete_user_data, give_consent, get_magic8_remaining, get_recent_reviews,
    acquire_bot_lock, add_product, create_order, ensure_user, get_active_subscriptions,
    get_all_users, get_order, get_product, get_product_by_name, get_products, get_recent_orders,
    get_user_by_username, update_username,
    get_stats, get_subscription, get_user, get_user_orders, init_db, set_order_status,
    get_analytics, search_users, get_user_admin_card, get_admin_audit, add_admin_audit,
    get_daily_predictions, get_random_daily_prediction, add_daily_prediction, update_daily_prediction, set_daily_prediction_active, delete_daily_prediction,
    get_magic8_answers, get_random_magic8_answer, add_magic8_answer, update_magic8_answer, set_magic8_active,
    get_legal_documents, log_backup, get_last_backup,
    get_admin_settings, set_admin_notifications, get_broadcast_recipients, update_product_image,
    set_product_active, set_subscription, update_product, update_user_field,
)
from personal import SPHERES, get_personal_forecast, get_sphere_forecast
from predictions import get_random_prediction

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))
MOSCOW_TZ = ZoneInfo("Europe/Moscow")
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
LEGAL_DIR = STATIC_DIR / "legal"
IMAGES_DIR = STATIC_DIR / "images"
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
WEBAPP_DIR = STATIC_DIR / "webapp"
WEBAPP_MAX_AGE = int(os.getenv("WEBAPP_MAX_AGE", "86400"))
BOT_ACCESS_MODE = os.getenv("BOT_ACCESS_MODE", "development").strip().lower()

def parse_user_ids(value):
    result = set()
    for item in value.split(","):
        item = item.strip()
        if item.isdigit():
            result.add(int(item))
    return result

ALLOWED_USER_IDS = parse_user_ids(os.getenv("ALLOWED_USER_IDS", ""))


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
if ADMIN_ID <= 0:
    raise RuntimeError("ADMIN_ID must be set")

@web.middleware
async def webapp_error_middleware(request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as exc:
        print(f"WEB APP ERROR {request.method} {request.path}: {exc}")
        if request.path.startswith("/api/webapp/"):
            return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)
        raise


app = web.Application(middlewares=[webapp_error_middleware])
user_states = {}
user_activity = {}
last_notification_sent = {}
telegram_session = None
bot_lock_conn = None
bot_lock_cursor = None
MAX_ACTIONS_PER_MINUTE = 20


def kb(rows, one_time=False):
    return {"keyboard": rows, "resize_keyboard": True, "one_time_keyboard": one_time, "is_persistent": False}


def is_admin(chat_id):
    return chat_id == ADMIN_ID


def is_access_allowed(chat_id):
    if BOT_ACCESS_MODE == "production":
        return True
    if chat_id == ADMIN_ID:
        return True
    return chat_id in ALLOWED_USER_IDS


def restricted_access_message():

    return (
        "🔒 <b>Бот находится в режиме разработки.</b>\n\n"
        "Доступ открыт только владельцу и назначенным тестировщикам."
    )


def main_kb():
    return kb([
        [{"text": "🔮 Предсказание на день"}, {"text": "✨ Персональное"}],
        [{"text": "🎯 По сферам"}, {"text": "💎 Каталог"}],
        [{"text": "🎱 Чёрный шар 8"}, {"text": "🎁 Подарок другу"}],
        [{"text": "🔔 Уведомления"}, {"text": "👤 Личный кабинет"}],
        [{"text": "📄 Правовые документы"}],
    ])


def consent_kb():
    return kb([
        [{"text": "✅ Ознакомился(лась) и даю согласие"}],
        [{"text": "❌ Не согласен(на)"}],
    ])

def back_kb(): return kb([[{"text": "🔙 Главное меню"}]])
def legal_kb():
    base = PUBLIC_URL
    return {
        "keyboard": [
            [{"text": "🔐 Политика ПДн", "web_app": {"url": f"{base}/legal/01_policy_personal_data.html"}}],
            [{"text": "✅ Согласие ПДн", "web_app": {"url": f"{base}/legal/02_consent_personal_data.html"}}],
            [{"text": "🛡 Конфиденциальность", "web_app": {"url": f"{base}/legal/03_confidentiality_security.html"}}],
            [{"text": "📜 Пользовательское соглашение", "web_app": {"url": f"{base}/legal/04_user_agreement.html"}}],
            [{"text": "🛒 Публичная оферта", "web_app": {"url": f"{base}/legal/05_public_offer.html"}}],
            [{"text": "⚠️ Дисклеймер", "web_app": {"url": f"{base}/legal/06_disclaimer_predictions.html"}}],
            [{"text": "📣 Рекламное согласие", "web_app": {"url": f"{base}/legal/07_marketing_consent.html"}}],
            [{"text": "📩 Права субъекта ПДн", "web_app": {"url": f"{base}/legal/08_data_subject_requests.html"}}],
            [{"text": "🔙 Главное меню"}],
        ],
        "resize_keyboard": True,
        "is_persistent": False,
    }


def gender_kb(): return kb([[{"text": "👨 Мужчина"}, {"text": "👩 Женщина"}], [{"text": "🙂 Не хочу указывать"}], [{"text": "🔙 Главное меню"}]], True)
def phone_kb(): return {"keyboard":[[{"text":"📱 Отправить мой номер","request_contact":True}],[{"text":"⌨️ Ввести номер вручную"}],[{"text":"❌ Отмена"}]],"resize_keyboard":True,"one_time_keyboard":True,"is_persistent":False}
def sphere_kb(): return kb([[{"text":"❤️ Любовь"},{"text":"💼 Карьера"}],[{"text":"💰 Финансы"},{"text":"👨‍👩‍👧 Семья"}],[{"text":"🌿 Самочувствие"},{"text":"📚 Развитие"}],[{"text":"🔙 Главное меню"}]])
def catalog_kb():
    ps=get_products(True); rows=[]; row=[]
    for p in ps:
        row.append({"text":p["name"]})
        if len(row)==2: rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([{"text":"🔙 Главное меню"}]); return kb(rows)
def product_kb(): return kb([[{"text":"🛒 Заказать"}],[{"text":"🔙 К каталогу"}]])
def order_confirm_kb(): return kb([[{"text":"✅ Подтвердить заказ"}],[{"text":"❌ Отменить заказ"}]])
def notifications_kb(active): return kb([[{"text":"🕒 Изменить время"}],[{"text":"🔕 Отписаться"}],[{"text":"🔙 Главное меню"}]]) if active else kb([[{"text":"🔔 Включить уведомления"}],[{"text":"🔙 Главное меню"}]])
def account_kb(): return kb([[{"text":"📊 Мой день"}],[{"text":"✏️ Изменить данные"}],[{"text":"📦 Мои заказы"}],[{"text":"⭐ Мои отзывы"}],[{"text":"🗑 Удалить мои данные"}],[{"text":"🔙 Главное меню"}]])
def edit_account_kb(): return kb([[{"text":"Имя"},{"text":"Пол"}],[{"text":"Дата рождения"},{"text":"Телефон"}],[{"text":"🔙 Личный кабинет"}]])
def time_kb():
    times=[f"{h:02d}:00" for h in range(7,23)]
    rows=[]
    for i in range(0,len(times),4):
        rows.append([{"text":t} for t in times[i:i+4]])
    rows.append([{"text":"⌨️ Другое время"}])
    rows.append([{"text":"❌ Отмена"}])
    return kb(rows, True)


def admin_kb():
    return kb([
        [{"text":"📊 Аналитика"},{"text":"📦 Заказы"}],
        [{"text":"👥 Пользователи"},{"text":"🛍 Каталог"}],
        [{"text":"📝 Контент"},{"text":"🎱 Magic 8"}],
        [{"text":"⚖️ Документы"},{"text":"📤 Экспорт"}],
        [{"text":"💾 Резервная копия"},{"text":"🚨 Уведомления"}],
        [{"text":"🛡 Журнал"},{"text":"⭐ Отзывы"}],
        [{"text":"🚪 Выйти"}],
    ])
def admin_order_kb():
    return kb([[{"text":"✅ Подтвердить"},{"text":"❌ Отклонить"}],[{"text":"🔧 В сборке"},{"text":"📦 Готов"}],[{"text":"🚚 В доставке"},{"text":"✅ Завершить"}],[{"text":"🔙 К списку заказов"}]])
def admin_product_kb(active): return kb([[{"text":"✏️ Изменить название"}],[{"text":"📝 Изменить описание"}],[{"text":"💰 Изменить цену"}],[{"text":"⏸ Скрыть товар" if active else "▶️ Показать товар"}],[{"text":"🗑 Удалить товар"}],[{"text":"🔙 Каталог админа"}]])

async def tg(method, payload=None, timeout=30):
    if telegram_session is None: raise RuntimeError("Telegram session not ready")
    async with telegram_session.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=payload or {}, timeout=timeout) as resp:
        data=await resp.json(content_type=None)
        if resp.status != 200 or not data.get("ok"): raise RuntimeError(f"Telegram {method}: {resp.status} {data}")
        return data["result"]

async def send(chat_id,text,reply_markup=None,parse_mode="HTML"):
    p={"chat_id":chat_id,"text":text}
    if reply_markup is not None: p["reply_markup"]=reply_markup
    if parse_mode: p["parse_mode"]=parse_mode
    return await tg("sendMessage",p)

async def send_photo(chat_id, filename, caption=None):
    path=IMAGES_DIR/filename
    if not path.exists(): return False
    form=aiohttp.FormData(); form.add_field("chat_id",str(chat_id))
    with path.open("rb") as fh:
        form.add_field("photo",fh,filename=path.name,content_type="image/png")
        if caption: form.add_field("caption",caption); form.add_field("parse_mode","HTML")
        async with telegram_session.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",data=form,timeout=40) as resp:
            data=await resp.json(content_type=None)
            if resp.status!=200 or not data.get("ok"): raise RuntimeError(f"Telegram sendPhoto: {resp.status} {data}")
    return True

async def ai_processing(chat_id, kind="daily"):
    steps = {
        "daily": [
            "🔮 Анализирую энергетику сегодняшнего дня...",
            "✨ Сопоставляю ключевые тенденции...",
            "📚 Формирую прогноз...",
        ],
        "personal": [
            "🔮 Анализирую ваши данные...",
            "✨ Формирую персональный профиль...",
            "📚 Собираю прогноз на сегодня...",
        ],
        "sphere": [
            "🔮 Анализирую дату рождения...",
            "✨ Сопоставляю выбранную сферу...",
            "📚 Формирую персональный ответ...",
        ],
    }[kind]
    for step in steps:
        await typing(chat_id)
        await send(chat_id, step)
        await asyncio.sleep(0.65)

async def welcome(chat_id):
    await send_photo(chat_id, "welcome.png")
    links = []
    if PUBLIC_URL:
        links = [
            f'<a href="{esc(legal_url("01_policy_personal_data.html"))}">Политика обработки ПДн</a>',
            f'<a href="{esc(legal_url("02_consent_personal_data.html"))}">Согласие на обработку ПДн</a>',
            f'<a href="{esc(legal_url("03_confidentiality_security.html"))}">Конфиденциальность и защита</a>',
            f'<a href="{esc(legal_url("04_user_agreement.html"))}">Пользовательское соглашение</a>',
            f'<a href="{esc(legal_url("05_public_offer.html"))}">Публичная оферта</a>',
            f'<a href="{esc(legal_url("06_disclaimer_predictions.html"))}">Дисклеймер прогнозов</a>',
        ]
    docs_text = "\n".join(links) if links else "Ссылки появятся после настройки PUBLIC_URL."
    await send(
        chat_id,
        "🔮 <b>Добро пожаловать!</b>\n\n"
        "Предсказания на день, персональные прогнозы, 6 сфер, Чёрный шар 8, "
        "каталог бусин Дзи, заказы, уведомления и личный кабинет.\n\n"
        "Перед началом, пожалуйста, ознакомьтесь с документами:\n\n"
        f"{docs_text}\n\n"
        "После ознакомления отдельно подтвердите согласие на обработку ваших персональных данных.",
        consent_kb(),
    )

async def typing(chat_id): return await tg("sendChatAction",{"chat_id":chat_id,"action":"typing"})

def valid_date(v):
    try: datetime.strptime(v,"%d.%m.%Y"); return True
    except ValueError: return False

def valid_time(v):
    try: datetime.strptime(v,"%H:%M"); return True
    except ValueError: return False

def clean_phone(v): return v.strip().replace("+","").replace(" ","").replace("-","").replace("(","").replace(")","")
def valid_phone(v):
    d=clean_phone(v); return 10<=len(d)<=15 and d.isdigit()
def esc(v): return html.escape(str(v or ""))
def flood_ok(uid):
    now=datetime.now(); arr=user_activity.setdefault(uid,[]); arr[:]=[x for x in arr if now-x<timedelta(minutes=1)]
    if len(arr)>=MAX_ACTIONS_PER_MINUTE: return False
    arr.append(now); return True

BAD_WORD_PATTERNS = [
    r"\bбля(?:д|т)\w*\b", r"\bсука\w*\b", r"\bхуй\w*\b",
    r"\bпизд\w*\b", r"\bеб(?:а|о|и|л|н)\w*\b", r"\bмудак\w*\b",
    r"\bдолбо[её]б\w*\b", r"\bидиот\w*\b", r"\bдебил\w*\b",
]
BAD_WORD_REGEX = [re.compile(p, re.IGNORECASE) for p in BAD_WORD_PATTERNS]

def is_bad(text):
    normalized = (text or "").lower().replace("ё", "е")
    normalized = re.sub(r"[^a-zа-я0-9]+", " ", normalized)
    return any(rx.search(normalized) for rx in BAD_WORD_REGEX)

def gender_from_text(t): return "male" if t=="👨 Мужчина" else "female" if t=="👩 Женщина" else "other"
def status_text(s): return {"new":"🆕 Новый","confirmed":"✅ Подтверждён","rejected":"❌ Отклонён","completed":"📦 Завершён"}.get(s,s)


def _telegram_init_data_hash(raw: str) -> str:
    data = dict(parse_qsl(raw, keep_blank_values=True))
    received_hash = data.pop("hash", "")
    pairs = [f"{k}={v}" for k, v in sorted(data.items())]
    data_check_string = "\n".join(pairs)
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return received_hash, calc


def validate_webapp_init_data(raw: str):
    if not raw:
        raise web.HTTPUnauthorized(text=json.dumps({"error": "Telegram авторизация не получена"}), content_type="application/json")

    received_hash, calc = _telegram_init_data_hash(raw)
    if not received_hash or not hmac.compare_digest(received_hash, calc):
        raise web.HTTPUnauthorized(text=json.dumps({"error": "Недействительные данные Telegram"}), content_type="application/json")

    parsed = dict(parse_qsl(raw, keep_blank_values=True))
    try:
        auth_date = int(parsed.get("auth_date", "0"))
    except ValueError:
        auth_date = 0

    if not auth_date or time.time() - auth_date > WEBAPP_MAX_AGE:
        raise web.HTTPUnauthorized(text=json.dumps({"error": "Сессия Telegram устарела"}), content_type="application/json")

    try:
        user = json.loads(parsed.get("user", "{}"))
    except json.JSONDecodeError:
        user = {}

    telegram_id = int(user.get("id", 0))
    if telegram_id <= 0:
        raise web.HTTPUnauthorized(text=json.dumps({"error": "Не удалось определить пользователя"}), content_type="application/json")

    if not is_access_allowed(telegram_id):
        raise web.HTTPForbidden(text=json.dumps({"error": "Доступ к боту закрыт"}), content_type="application/json")

    return telegram_id, user


async def webapp_user(request):
    raw = request.headers.get("X-Telegram-Init-Data", "")
    return validate_webapp_init_data(raw)


def web_json_value(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def sanitize_user_for_web(user):
    if not user:
        return {}
    return {
        "telegram_id": int(user.get("telegram_id") or 0),
        "name": user.get("name") or "",
        "gender": user.get("gender") or "",
        "birthdate": user.get("birthdate") or "",
        "phone": user.get("phone") or "",
        "username": user.get("username") or "",
        "consent_given": bool(user.get("consent_given")),
        "consent_version": user.get("consent_version") or "",
        "consent_at": web_json_value(user.get("consent_at")),
        "created_at": web_json_value(user.get("created_at")),
    }


def sanitize_subscription_for_web(value):
    if not value:
        return {"time": "09:00", "active": False}
    return {
        "time": str(value.get("time") or "09:00"),
        "active": bool(value.get("active")),
    }


def sanitize_order_for_web(order):
    if not order:
        return None
    return {
        "id": int(order.get("id") or 0),
        "telegram_id": int(order.get("telegram_id") or 0),
        "product_id": int(order["product_id"]) if order.get("product_id") is not None else None,
        "product_name": order.get("product_name") or "",
        "customer_name": order.get("customer_name") or "",
        "customer_phone": order.get("customer_phone") or "",
        "status": order.get("status") or "",
        "status_label": web_status_label(order.get("status") or ""),
        "created_at": web_json_value(order.get("created_at")),
        "updated_at": web_json_value(order.get("updated_at")),
    }


def web_status_label(status):
    return {
        "new": "🆕 Новый",
        "confirmed": "✅ Подтверждён",
        "assembling": "🔧 В сборке",
        "ready": "📦 Готов",
        "shipping": "🚚 В доставке",
        "completed": "✅ Завершён",
        "rejected": "❌ Отклонён",
    }.get(status, status)


def web_user_payload(telegram_id):
    ensure_user(telegram_id)
    u = get_user(telegram_id) or {
        "telegram_id": telegram_id, "name": "", "gender": "", "birthdate": "",
        "phone": "", "username": "", "consent_given": False, "consent_version": "",
    }
    return sanitize_user_for_web(u)

async def webapp_index(request):
    f = WEBAPP_DIR / "index.html"
    if not f.exists():
        return web.Response(status=404, text="Web App is not installed")
    return web.FileResponse(f)


async def webapp_auth(request):
    uid, tg_user = await webapp_user(request)
    u = web_user_payload(uid)
    return web.json_response({
        "user": u,
        "is_admin": uid == ADMIN_ID,
        "access_mode": BOT_ACCESS_MODE,
        "telegram_user": tg_user,
    })


async def webapp_consent(request):
    uid, _ = await webapp_user(request)
    give_consent(uid)
    return web.json_response({"user": web_user_payload(uid)})


async def webapp_catalog(request):
    uid, _ = await webapp_user(request)
    u = web_user_payload(uid)
    if not u["consent_given"]:
        raise web.HTTPForbidden(text=json.dumps({"error": "Сначала подтвердите согласие"}), content_type="application/json")
    return web.json_response({"products": get_products(True)})


async def webapp_catalog_item(request):
    uid, _ = await webapp_user(request)
    u = web_user_payload(uid)
    if not u["consent_given"]:
        raise web.HTTPForbidden(text=json.dumps({"error": "Сначала подтвердите согласие"}), content_type="application/json")
    p = get_product(int(request.match_info["product_id"]))
    if not p or not p["is_active"]:
        raise web.HTTPNotFound(text=json.dumps({"error": "Товар не найден"}), content_type="application/json")
    return web.json_response({"product": p})


async def webapp_orders(request):
    uid, _ = await webapp_user(request)
    u = web_user_payload(uid)
    if not u["consent_given"]:
        raise web.HTTPForbidden(text=json.dumps({"error": "Сначала подтвердите согласие"}), content_type="application/json")

    if request.method == "GET":
        return web.json_response({"orders": [sanitize_order_for_web(x) for x in get_user_orders(uid)]})

    body = await request.json()
    try:
        product_id = int(body.get("product_id", 0))
    except (TypeError, ValueError):
        product_id = 0
    name = (body.get("name") or "").strip()[:50]
    phone = (body.get("phone") or "").strip()
    if not product_id or not name or not valid_phone(phone):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Проверьте товар, имя и номер телефона"}), content_type="application/json")
    product = get_product(product_id)
    if not product or not product["is_active"]:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Товар недоступен"}), content_type="application/json")
    oid = create_order(uid, product_id, name, phone)
    update_user_field(uid, "name", name)
    update_user_field(uid, "phone", phone)
    if ADMIN_ID:
        try:
            await send(ADMIN_ID, f"🆕 <b>Новый заказ №{oid}</b>\n\n💎 {esc(product['name'])}\n👤 {esc(name)}\n📞 {esc(phone)}\n🆔 {uid}", admin_kb())
            add_admin_audit(ADMIN_ID, "web_order", "order", oid, f"user={uid}")
        except Exception as exc:
            print(f"WEB APP ADMIN NOTIFY ERROR: {exc}")
    return web.json_response({"order_id": oid})

async def webapp_profile(request):
    uid, _ = await webapp_user(request)
    u = web_user_payload(uid)
    if request.method == "GET":
        return web.json_response({"user": u})
    if request.method != "POST":
        raise web.HTTPMethodNotAllowed(request.method, ["GET", "POST"])
    body = await request.json()
    name = (body.get("name") or "").strip()[:50]
    gender = (body.get("gender") or "").strip()
    birthdate = (body.get("birthdate") or "").strip()
    phone = (body.get("phone") or "").strip()
    if birthdate and not valid_date(birthdate):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Неверная дата рождения"}), content_type="application/json")
    if phone and not valid_phone(phone):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Неверный номер телефона"}), content_type="application/json")
    for field, value in [("name", name), ("gender", gender), ("birthdate", birthdate), ("phone", phone)]:
        update_user_field(uid, field, value)
    return web.json_response({"user": sanitize_user_for_web(web_user_payload(uid))})


async def webapp_delete_profile(request):
    uid, _ = await webapp_user(request)
    delete_user_data(uid)
    return web.json_response({"ok": True})


async def webapp_subscription(request):
    uid, _ = await webapp_user(request)
    u = web_user_payload(uid)
    if not u["consent_given"]:
        raise web.HTTPForbidden(text=json.dumps({"error": "Сначала подтвердите согласие"}), content_type="application/json")
    if request.method == "GET":
        sub = get_subscription(uid) or {"time": "09:00", "active": False}
        return web.json_response(sub)
    body = await request.json()
    time_local = str(body.get("time") or "09:00")
    active = bool(body.get("active", True))
    try:
        datetime.strptime(time_local, "%H:%M")
    except ValueError:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Неверное время"}), content_type="application/json")
    set_subscription(uid, time_local, active)
    return web.json_response(sanitize_subscription_for_web(get_subscription(uid)))


async def webapp_forecast(request):
    uid, _ = await webapp_user(request)
    u = web_user_payload(uid)
    if not u["consent_given"]:
        raise web.HTTPForbidden(text=json.dumps({"error": "Сначала подтвердите согласие"}), content_type="application/json")
    body = await request.json()
    kind = request.match_info["kind"]

    if kind == "daily":
        forecast = get_random_daily_prediction() or get_random_prediction()
        return web.json_response({"html": esc(forecast).replace("\n", "<br>")})

    name = (body.get("name") or u["name"] or "").strip()[:50]
    gender = (body.get("gender") or u["gender"] or "other").strip()
    birthdate = (body.get("birthdate") or u["birthdate"] or "").strip()
    sphere = body.get("sphere")

    if not name:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Введите имя"}), content_type="application/json")
    if not valid_date(birthdate):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Дата рождения должна быть в формате ДД.ММ.ГГГГ"}), content_type="application/json")

    update_user_field(uid, "name", name)
    update_user_field(uid, "gender", gender)
    update_user_field(uid, "birthdate", birthdate)

    if sphere:
        forecast = get_sphere_forecast(birthdate, gender, name, sphere)
    else:
        forecast = get_personal_forecast(birthdate, gender, name)

    return web.json_response({"html": esc(forecast).replace("\n", "<br>"), "user": web_user_payload(uid)})


async def webapp_my_day(request):
    uid, _ = await webapp_user(request)
    u = web_user_payload(uid)
    if not u["consent_given"] or not u["birthdate"]:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Сначала заполните дату рождения"}), content_type="application/json")
    text = get_personal_forecast(u["birthdate"], u["gender"] or "other", u["name"] or "Друг")
    return web.json_response({"html": esc(text).replace("\n", "<br>")})


async def webapp_magic8(request):
    uid, _ = await webapp_user(request)
    u = web_user_payload(uid)
    if not u["consent_given"]:
        raise web.HTTPForbidden(text=json.dumps({"error": "Сначала подтвердите согласие"}), content_type="application/json")
    if request.method == "GET":
        return web.json_response({"remaining": get_magic8_remaining(uid)})
    body = await request.json()
    q = (body.get("question") or "").strip()[:250]
    if not q or is_bad(q):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Сформулируйте короткий и уважительный вопрос"}), content_type="application/json")
    ok, remaining = consume_magic8_question(uid)
    if not ok:
        raise web.HTTPTooManyRequests(text=json.dumps({"error": "Лимит в 3 вопроса на сегодня исчерпан"}), content_type="application/json")
    return web.json_response({"answer": get_random_magic8_answer(), "remaining": remaining})


async def webapp_gift_prediction(request):
    uid, _ = await webapp_user(request)
    u = web_user_payload(uid)
    if not u["consent_given"]:
        raise web.HTTPForbidden(text=json.dumps({"error": "Сначала подтвердите согласие"}), content_type="application/json")
    body = await request.json()
    username = (body.get("username") or "").strip()
    if not username.startswith("@") or len(username) < 2:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Введите @username"}), content_type="application/json")
    recipient = get_user_by_username(username)
    if not recipient:
        raise web.HTTPNotFound(text=json.dumps({"error": "Пользователь не найден среди тех, кто уже запускал бота"}), content_type="application/json")
    if recipient["telegram_id"] == uid:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Нельзя отправить подарок самому себе"}), content_type="application/json")
    text = get_random_daily_prediction() or get_random_prediction()
    await send(recipient["telegram_id"], f"🎁 <b>Вам подарили предсказание!</b>\n\n{esc(text)}", main_kb())
    return web.json_response({"ok": True})


async def webapp_review(request):
    uid, _ = await webapp_user(request)
    u = web_user_payload(uid)
    if not u["consent_given"]:
        raise web.HTTPForbidden(text=json.dumps({"error": "Сначала подтвердите согласие"}), content_type="application/json")
    body = await request.json()
    order_id = int(body.get("order_id", 0))
    rating = int(body.get("rating", 0))
    text = (body.get("text") or "").strip()[:1000]
    if rating not in {1,2,3,4,5}:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Оценка должна быть от 1 до 5"}), content_type="application/json")
    order = get_order(order_id)
    if not order or order["telegram_id"] != uid or order["status"] != "completed":
        raise web.HTTPBadRequest(text=json.dumps({"error": "Отзыв доступен только по вашему завершённому заказу"}), content_type="application/json")
    add_review(order_id, uid, rating, text)
    return web.json_response({"ok": True})


async def webapp_legal(request):
    await webapp_user(request)
    docs = get_legal_documents()
    mapping = {
        "policy": ("01_policy_personal_data.html", "Политика ПДн"),
        "consent": ("02_consent_personal_data.html", "Согласие ПДн"),
        "confidentiality": ("03_confidentiality_security.html", "Конфиденциальность"),
        "agreement": ("04_user_agreement.html", "Пользовательское соглашение"),
        "offer": ("05_public_offer.html", "Публичная оферта"),
        "disclaimer": ("06_disclaimer_predictions.html", "Дисклеймер"),
        "marketing": ("07_marketing_consent.html", "Рекламное согласие"),
        "rights": ("08_data_subject_requests.html", "Права субъекта ПДн"),
    }
    result=[]
    for doc in docs:
        file_name, fallback_title = mapping.get(doc.get("key", ""), ("", doc.get("title") or "Документ"))
        if not file_name:
            continue
        result.append({
            "title": fallback_title,
            "file": file_name,
            "url": f"{PUBLIC_URL}/legal/{file_name}" if PUBLIC_URL else f"/legal/{file_name}",
            "version": doc.get("version") or "",
        })
    if not result:
        result=[
            {"title": title, "file": file_name, "url": f"{PUBLIC_URL}/legal/{file_name}" if PUBLIC_URL else f"/legal/{file_name}", "version": ""}
            for file_name,title in mapping.values()
        ]
    return web.json_response({"documents": result})

def require_admin(uid):
    if uid != ADMIN_ID:
        raise web.HTTPForbidden(text=json.dumps({"error": "Недостаточно прав"}), content_type="application/json")


async def webapp_admin_analytics(request):
    uid, _ = await webapp_user(request); require_admin(uid)
    raw = request.query.get("days", "")
    days = int(raw) if raw else None
    return web.json_response({"stats": get_analytics(days)})


async def webapp_admin_orders(request):
    uid, _ = await webapp_user(request)
    require_admin(uid)
    if request.method == "GET":
        return web.json_response({"orders": [sanitize_order_for_web(x) for x in get_recent_orders(100)]})
    try:
        oid=int(request.match_info["order_id"])
    except (TypeError,ValueError):
        raise web.HTTPBadRequest(text=json.dumps({"error":"Некорректный номер заказа"}), content_type="application/json")
    body=await request.json()
    status=str(body.get("status") or "")
    try:
        set_order_status(oid,status)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=json.dumps({"error":str(exc)}), content_type="application/json")
    add_admin_audit(uid,"web_order_status","order",oid,status)
    order=get_order(oid)
    if order:
        try:
            await send(order["telegram_id"],f"{web_status_label(status)} Заказ №{oid}.",main_kb())
        except Exception as exc:
            print(f"WEB APP STATUS NOTIFY ERROR: {exc}")
    return web.json_response({"ok":True})

async def webapp_admin_users(request):
    uid, _ = await webapp_user(request); require_admin(uid)
    q = request.query.get("q", "").strip()
    rows = search_users(q, 100)
    for row in rows:
        if hasattr(row.get("created_at"), "isoformat"):
            row["created_at"] = row["created_at"].isoformat()
    return web.json_response({"users": rows})


async def webapp_admin_products(request):
    uid, _ = await webapp_user(request); require_admin(uid)
    return web.json_response({"products": get_products(False)})


async def webapp_admin_content(request):
    uid, _ = await webapp_user(request); require_admin(uid)
    return web.json_response({"predictions": get_daily_predictions(False)})


async def webapp_admin_magic(request):
    uid, _ = await webapp_user(request); require_admin(uid)
    return web.json_response({"answers": get_magic8_answers(False)})


async def webapp_admin_audit(request):
    uid, _ = await webapp_user(request); require_admin(uid)
    raw = get_admin_audit(100)
    rows = []
    for r in raw:
        rows.append({
            "id": r[0], "admin_id": r[1], "action": r[2],
            "entity_type": r[3], "entity_id": r[4], "details": r[5],
            "created_at": r[6].isoformat() if hasattr(r[6], "isoformat") else str(r[6]),
        })
    return web.json_response({"rows": rows})


async def homepage(request):
    # The root URL must no longer expose the legacy landing page.
    # It now serves the Telegram Mini App as well, so stale Telegram menu URLs
    # cannot accidentally open the old date/prediction page.
    return await webapp_index(request)
async def health(request): return web.json_response({"status":"ok","build":"PREDBOT-2026-08-23-MINI-APP-01"})
app.router.add_get("/",homepage); app.router.add_get("/health",health); app.router.add_static("/static",path=str(STATIC_DIR),name="static"); app.router.add_static("/legal",path=str(LEGAL_DIR),name="legal")
app.router.add_get("/app", webapp_index)
app.router.add_get("/app/", webapp_index)
app.router.add_static("/app/static", path=str(WEBAPP_DIR), name="webapp-static")
app.router.add_post("/api/webapp/auth", webapp_auth)
app.router.add_post("/api/webapp/consent", webapp_consent)
app.router.add_route("GET", "/api/webapp/catalog", webapp_catalog)
app.router.add_route("GET", "/api/webapp/catalog/{product_id}", webapp_catalog_item)
app.router.add_route("GET", "/api/webapp/orders", webapp_orders)
app.router.add_route("POST", "/api/webapp/orders", webapp_orders)
app.router.add_route("GET", "/api/webapp/profile", webapp_profile)
app.router.add_route("POST", "/api/webapp/profile", webapp_profile)
app.router.add_post("/api/webapp/profile/delete", webapp_delete_profile)
app.router.add_route("GET", "/api/webapp/subscription", webapp_subscription)
app.router.add_route("POST", "/api/webapp/subscription", webapp_subscription)
app.router.add_post("/api/webapp/forecast/{kind}", webapp_forecast)
app.router.add_get("/api/webapp/my-day", webapp_my_day)
app.router.add_route("GET", "/api/webapp/magic8/remaining", webapp_magic8)
app.router.add_post("/api/webapp/magic8/ask", webapp_magic8)
app.router.add_post("/api/webapp/gift/prediction", webapp_gift_prediction)
app.router.add_post("/api/webapp/reviews", webapp_review)
app.router.add_get("/api/webapp/legal", webapp_legal)
app.router.add_get("/api/webapp/admin/analytics", webapp_admin_analytics)
app.router.add_route("GET", "/api/webapp/admin/orders", webapp_admin_orders)
app.router.add_post("/api/webapp/admin/orders/{order_id}", webapp_admin_orders)
app.router.add_get("/api/webapp/admin/users", webapp_admin_users)
app.router.add_get("/api/webapp/admin/products", webapp_admin_products)
app.router.add_get("/api/webapp/admin/content", webapp_admin_content)
app.router.add_get("/api/webapp/admin/magic8", webapp_admin_magic)
app.router.add_get("/api/webapp/admin/audit", webapp_admin_audit)


def legal_url(filename):
    base = PUBLIC_URL or ""
    return f"{base}/legal/{filename}"

async def show_legal_documents(chat_id):
    if not PUBLIC_URL:
        await send(
            chat_id,
            "📄 Юридические документы временно недоступны. Администратору необходимо настроить PUBLIC_URL в Render.",
            main_kb(),
        )
        return

    await send(
        chat_id,
        "📄 <b>Правовые документы</b>\n\n"
        "Выберите документ на клавиатуре ниже. "
        "Документ откроется прямо в Telegram.",
        legal_kb(),
    )


async def ensure_profile(chat_id): ensure_user(chat_id); return get_user(chat_id)

async def begin_personal(chat_id,sphere=None):
    u=await ensure_profile(chat_id)
    if not u["name"]: user_states[chat_id]={"type":"personal_name","sphere":sphere}; await send(chat_id,"Как вас зовут?",back_kb()); return
    if not u["gender"]: user_states[chat_id]={"type":"personal_gender","sphere":sphere}; await send(chat_id,"Укажите ваш пол — это помогает сформировать обращение.",gender_kb()); return
    if not u["birthdate"]: user_states[chat_id]={"type":"personal_birthdate","sphere":sphere}; await send(chat_id,"Введите дату рождения в формате ДД.ММ.ГГГГ:",back_kb()); return
    await produce_forecast(chat_id,u,sphere)

async def produce_forecast(chat_id,u,sphere=None):
    await ai_processing(chat_id, "sphere" if sphere else "personal")
    text=get_sphere_forecast(u["birthdate"],u["gender"],u["name"],sphere) if sphere else get_personal_forecast(u["birthdate"],u["gender"],u["name"])
    await send(chat_id,text,main_kb())

async def show_catalog(chat_id):
    if not get_products(True): await send(chat_id,"Каталог временно пуст.",main_kb()); return
    await send(chat_id,"💎 <b>Каталог</b>\n\nВыберите бусину Дзи:",catalog_kb())

async def show_product(chat_id,p):
    if p.get("image_file"): await send_photo(chat_id,p["image_file"],f"💎 <b>{esc(p['name'])}</b>")
    price=f"{p['price_rub']:,}".replace(","," ")
    await send(chat_id,f"💎 <b>{esc(p['name'])}</b>\n\n{esc(p['description'])}\n\n💰 <b>{price} ₽</b>",product_kb())

async def show_account(chat_id):
    u=await ensure_profile(chat_id); orders=get_user_orders(chat_id)
    g={"male":"мужчина","female":"женщина","other":"не указано"}.get(u["gender"],"не указано")
    await send(chat_id,f"👤 <b>Личный кабинет</b>\n\nИмя: <b>{esc(u['name'] or 'не указано')}</b>\nПол: <b>{g}</b>\nДата рождения: <b>{esc(u['birthdate'] or 'не указана')}</b>\nТелефон: <b>{esc(u['phone'] or 'не указан')}</b>\nЗаказов: <b>{len(orders)}</b>",account_kb())

async def show_orders(chat_id):
    orders = get_user_orders(chat_id)
    if not orders:
        await send(chat_id, "📦 У вас пока нет заказов.", account_kb())
        return

    parts = ["📦 <b>Мои заказы</b>"]
    review_rows = []

    for o in orders[:20]:
        dt = o["created_at"].astimezone(MOSCOW_TZ).strftime("%d.%m.%Y %H:%M")
        parts.append(
            f"\n<b>Заказ №{o['id']}</b>\n"
            f"💎 {esc(o['product_name'])}\n"
            f"📌 {status_text(o['status'])}\n"
            f"🗓 {dt}"
        )
        if o["status"] == "completed":
            review_rows.append([{"text": f"⭐ Заказ №{o['id']} — оставить отзыв"}])

    if review_rows:
        review_rows.append([{"text": "🔙 Личный кабинет"}])
        await send(chat_id, "\n".join(parts), kb(review_rows))
    else:
        await send(chat_id, "\n".join(parts), account_kb())

async def show_notifications(chat_id):
    sub=get_subscription(chat_id)
    if sub and sub["active"]:
        await send(chat_id,f"🔔 <b>Уведомления включены</b>\n\nКаждый день в <b>{sub['time']}</b> по Москве я сообщу, что ваше предсказание на день готово.",notifications_kb(True))
    else:
        await send(chat_id,"🔔 <b>Ежедневные уведомления</b>\n\nЯ буду сообщать: «Ваше предсказание на день готово — узнайте, что вас ждёт». Выберите удобное время.",notifications_kb(False))


def admin_content_kb(): return kb([[{"text":"🔮 Дневные прогнозы"}],[{"text":"🔙 Админ-панель"}]])
def admin_magic_kb(): return kb([[{"text":"🎱 Ответы шара"}],[{"text":"➕ Добавить ответ"}],[{"text":"🔙 Админ-панель"}]])
def admin_export_kb(): return kb([[{"text":"👥 Пользователи CSV"}],[{"text":"📦 Заказы CSV"}],[{"text":"⭐ Отзывы CSV"}],[{"text":"🔙 Админ-панель"}]])
def admin_backup_kb(): return kb([[{"text":"💾 Сделать резервную копию"}],[{"text":"📋 Последняя копия"}],[{"text":"🔙 Админ-панель"}]])

aasync=0

async def admin_content_menu(chat_id): await send(chat_id,"📝 <b>Управление контентом</b>",admin_content_kb())

async def admin_predictions_menu(chat_id):
    user_states[chat_id]={"type":"admin_prediction_list"}
    items=get_daily_predictions(False); rows=[]
    for p in items: rows.append([{"text":f"{'🟢' if p['is_active'] else '⚪'} #{p['id']} {p['text'][:42]}"}])
    rows.append([{"text":"➕ Добавить прогноз"}]); rows.append([{"text":"🔙 Контент"}])
    await send(chat_id,f"🔮 <b>Дневные прогнозы</b>\nВсего: {len(items)}",kb(rows))

async def admin_magic_menu(chat_id):
    user_states[chat_id]={"type":"admin_magic_list"}
    items=get_magic8_answers(False); rows=[]
    for p in items: rows.append([{"text":f"{'🟢' if p['is_active'] else '⚪'} #{p['id']} {p['text'][:42]}"}])
    rows.append([{"text":"➕ Добавить ответ"}]); rows.append([{"text":"🔙 Админ-панель"}])
    await send(chat_id,f"🎱 <b>Magic 8</b>\nОтветов: {len(items)}",kb(rows))

async def admin_documents_view(chat_id):
    docs=get_legal_documents(); lines=["⚖️ <b>Документы</b>"]
    for d in docs: lines.append(f"\n{'🟢' if d['active'] else '⚪'} <b>{esc(d['title'])}</b>\nВерсия: {esc(d['version'])}\n{esc((PUBLIC_URL or '')+d['url'])}")
    await send(chat_id,"\n".join(lines),kb([[{"text":"🔙 Админ-панель"}]]))

async def send_document_file(chat_id,path,caption=None):
    form=aiohttp.FormData(); form.add_field("chat_id",str(chat_id))
    with Path(path).open("rb") as fh:
        form.add_field("document",fh,filename=Path(path).name,content_type="application/octet-stream")
        if caption: form.add_field("caption",caption)
        async with telegram_session.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",data=form,timeout=60) as resp:
            data=await resp.json(content_type=None)
            if resp.status!=200 or not data.get("ok"): raise RuntimeError(f"Telegram sendDocument: {resp.status} {data}")

async def export_csv(chat_id,kind):
    export_dir=BASE_DIR/"exports"; export_dir.mkdir(exist_ok=True)
    if kind=="users":
        filename="users.csv"; headers=["telegram_id","username","name","gender","birthdate","phone","consent_given","created_at"]
        rows=get_all_users(10000); data=[[u.get('telegram_id'),u.get('username',''),u.get('name',''),u.get('gender',''),u.get('birthdate',''),u.get('phone',''),u.get('consent_given'),u.get('created_at')] for u in rows]
    elif kind=="orders":
        filename="orders.csv"; headers=["id","telegram_id","product","customer_name","customer_phone","status","created_at"]
        rows=get_recent_orders(100000); data=[[o['id'],o['telegram_id'],o['product_name'],o['customer_name'],o['customer_phone'],o['status'],o['created_at']] for o in rows]
    else:
        filename="reviews.csv"; headers=["id","order_id","telegram_id","rating","review_text","created_at"]
        data=get_recent_reviews(100000)
    path=export_dir/filename
    with path.open('w',encoding='utf-8-sig',newline='') as fh:
        w=csv.writer(fh); w.writerow(headers); w.writerows(data)
    await send_document_file(chat_id,path,f"📤 {filename}")
    add_admin_audit(chat_id,"export",kind,filename,f"rows={len(data)}")
    await send(chat_id,"✅ Экспорт готов.",admin_export_kb())

async def backup_status(chat_id):
    b=get_last_backup()
    if not b: await send(chat_id,"💾 Резервных копий пока нет.",admin_backup_kb()); return
    dt=b['created_at'].astimezone(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M')
    await send(chat_id,f"💾 <b>Последняя копия</b>\nСтатус: {esc(b['status'])}\nФайл: {esc(b['file'] or '—')}\nВремя: {dt}\n{esc(b['details'] or '')}",admin_backup_kb())

async def make_backup(chat_id):
    if not file_shutil.which('pg_dump'):
        log_backup(chat_id,'pg_dump','', 'unavailable','pg_dump не найден на Render')
        await send(chat_id,"❌ pg_dump не найден. Создание SQL-дампа из текущего контейнера невозможно.",admin_backup_kb()); return
    backup_dir=BASE_DIR/'backups'; backup_dir.mkdir(exist_ok=True)
    filename=f"predbot_{datetime.now(MOSCOW_TZ):%Y%m%d_%H%M%S}.sql"; path=backup_dir/filename
    try:
        with path.open('wb') as fh:
            result=subprocess.run(['pg_dump',os.environ['DATABASE_URL'],'--no-owner','--no-privileges'],stdout=fh,stderr=subprocess.PIPE,timeout=120,check=True)
        log_backup(chat_id,'pg_dump',filename,'success','SQL dump created')
        await send_document_file(chat_id,path,f"💾 Резервная копия {filename}")
        add_admin_audit(chat_id,'backup','database',filename,'success')
        await send(chat_id,'✅ Резервная копия создана. Храните её отдельно от Render.',admin_backup_kb())
    except Exception as exc:
        log_backup(chat_id,'pg_dump',filename,'failed',str(exc)); add_admin_audit(chat_id,'backup','database',filename,str(exc))
        await send(chat_id,f"❌ Ошибка резервной копии: {esc(str(exc))}",admin_backup_kb())

def admin_analytics_kb(): return kb([[{"text":"Сегодня"},{"text":"7 дней"}],[{"text":"30 дней"},{"text":"Всё время"}],[{"text":"🔙 Админ-панель"}]])
def admin_users_kb(): return kb([[{"text":"🔎 Найти пользователя"}],[{"text":"👥 Последние пользователи"}],[{"text":"🔙 Админ-панель"}]])
def admin_broadcast_kb(): return kb([[{"text":"👥 Все согласившиеся"}],[{"text":"🔔 С подпиской"}],[{"text":"📦 Покупатели"}],[{"text":"🆕 Новые сегодня"}],[{"text":"🔙 Админ-панель"}]])
def admin_notification_kb(s): return kb([[{"text":("🟢 Новые заказы" if s["new_order"] else "⚪ Новые заказы")}],[{"text":("🟢 Изменение статуса" if s["status_change"] else "⚪ Изменение статуса")}],[{"text":("🟢 Новые пользователи" if s["new_user"] else "⚪ Новые пользователи")}],[{"text":("🟢 Безопасность" if s["security"] else "⚪ Безопасность")}],[{"text":"🔙 Админ-панель"}]])

async def admin_show_analytics(chat_id,days=None):
    a=get_analytics(days); label="всё время" if not days else ("сегодня" if days==1 else f"за {days} дней")
    await send(chat_id,f"📊 <b>Аналитика — {label}</b>\n\n👥 Пользователей: <b>{a['users']}</b>\n🆕 Новых сегодня: <b>{a['new_today']}</b>\n✅ Согласие ПДн: <b>{a['consented']}</b>\n\n📦 Заказов: <b>{a['orders']}</b>\n🆕 Новых: <b>{a['new_orders']}</b>\n✅ Подтверждено: <b>{a['confirmed']}</b>\n🔧 В сборке: <b>{a['assembling']}</b>\n📦 Готово: <b>{a['ready']}</b>\n🚚 В доставке: <b>{a['shipping']}</b>\n✅ Завершено: <b>{a['completed']}</b>\n❌ Отклонено: <b>{a['rejected']}</b>\n\n🔔 Уведомлений: <b>{a['subscriptions']}</b>\n⭐ Отзывов: <b>{a['reviews']}</b>\n⭐ Средняя оценка: <b>{a['avg_rating']:.2f}</b>\n🔥 Популярный товар: <b>{esc(a['top_product'] or 'нет данных')}</b>",admin_analytics_kb())

async def admin_users_menu(chat_id): await send(chat_id,"👥 <b>Пользователи</b>\n\nВыберите действие:",admin_users_kb())
async def admin_users_latest(chat_id):
    us=get_all_users(20)
    rows=[]
    for u in us: rows.append([{"text":f"{u.get('username') or u.get('name') or u['telegram_id']} · {u['telegram_id']}"}])
    rows.append([{"text":"🔎 Найти пользователя"},{"text":"🔙 Пользователи"}]); await send(chat_id,"👥 <b>Последние пользователи</b>",kb(rows))
async def admin_user_card(chat_id,uid):
    c=get_user_admin_card(uid)
    if not c: await send(chat_id,"Пользователь не найден.",admin_users_kb()); return
    u=c['user']; orders=c['orders']; g={"male":"мужчина","female":"женщина","other":"не указано"}.get(u.get('gender'),"не указано")
    txt=f"👤 <b>Пользователь {uid}</b>\n\nTelegram: <b>@{esc(u.get('username') or 'нет')}</b>\nИмя: <b>{esc(u.get('name') or 'не указано')}</b>\nПол: <b>{g}</b>\nДата рождения: <b>{esc(u.get('birthdate') or 'не указана')}</b>\nТелефон: <b>{esc(u.get('phone') or 'не указан')}</b>\nСогласие: <b>{'да' if u.get('consent_given') else 'нет'}</b>\nЗаказов: <b>{len(orders)}</b>"
    if orders:
        txt += "\n\n" + "\n".join([f"№{o['id']} · {esc(o['product_name'])} · {status_text(o['status'])}" for o in orders[:10]])
    await send(chat_id,txt,admin_users_kb())
async def admin_audit_view(chat_id):
    rows=get_admin_audit(30)
    if not rows: await send(chat_id,"🛡 Журнал пока пуст.",admin_kb()); return
    lines=["🛡 <b>Журнал действий</b>"]
    for r in rows:
        dt=r[6].astimezone(MOSCOW_TZ).strftime('%d.%m %H:%M'); lines.append(f"{dt} · <b>{esc(r[2])}</b> · {esc(r[3] or '')} {esc(r[4] or '')}\n{esc(r[5] or '')}")
    await send(chat_id,"\n\n".join(lines),admin_kb())
async def admin_notifications_view(chat_id): await send(chat_id,"🚨 <b>Уведомления администратора</b>\n\nНастройте события:",admin_notification_kb(get_admin_settings()))

async def admin_orders(chat_id):
    os_=get_recent_orders(20)
    if not os_: await send(chat_id,"Заказов пока нет.",admin_kb()); return
    rows=[[{"text":f"Заказ №{o['id']} — {o['product_name']}"}] for o in os_]
    rows.append([{"text":"🔙 Админ-панель"}]); await send(chat_id,"📦 <b>Заказы</b>\n\nВыберите заказ:",kb(rows))

async def admin_order(chat_id,oid):
    o=get_order(oid)
    if not o: await send(chat_id,"Заказ не найден.",admin_kb()); return
    dt=o["created_at"].astimezone(MOSCOW_TZ).strftime("%d.%m.%Y %H:%M")
    user_states[chat_id]={"type":"admin_order","order_id":oid}
    await send(chat_id,f"📦 <b>Заказ №{oid}</b>\n\n💎 {esc(o['product_name'])}\n👤 {esc(o['customer_name'])}\n📞 {esc(o['customer_phone'])}\n🆔 {o['telegram_id']}\n📌 {status_text(o['status'])}\n🗓 {dt}",admin_order_kb())

async def admin_catalog(chat_id):
    ps=get_products(False); rows=[]
    for p in ps: rows.append([{"text":("🟢 " if p["is_active"] else "⚪ ")+p["name"]}])
    rows += [[{"text":"➕ Добавить товар"}],[{"text":"🔙 Админ-панель"}]]
    await send(chat_id,"🛍 <b>Управление каталогом</b>",kb(rows))

async def admin_product(chat_id,p):
    await send(chat_id,f"💎 <b>{esc(p['name'])}</b>\n\n{esc(p['description'])}\n\nЦена: <b>{p['price_rub']:,} ₽</b>\nСтатус: {'активен' if p['is_active'] else 'скрыт'}".replace(","," "),admin_product_kb(p["is_active"]))

async def handle_state(chat_id, text, state, message):
    t = state["type"]

    if text == "🔙 Главное меню" and t != "consent":
        user_states.pop(chat_id, None)
        await send(chat_id, "Главное меню:", main_kb())
        return

    if text in {"❌ Отмена", "/cancel"} and t != "consent":
        user_states.pop(chat_id, None)
        await send(chat_id, "Действие отменено.", main_kb())
        return

    if t=="consent":
        if text=="✅ Ознакомился(лась) и даю согласие":
            give_consent(chat_id); user_states.pop(chat_id,None); await send(chat_id,"✅ Спасибо. Согласие сохранено. Теперь доступен весь функционал.",main_kb()); return
        if text=="❌ Не согласен(на)":
            await send(chat_id,"Без согласия бот не сможет сохранять ваши персональные данные и оформлять заказ.",consent_kb()); return
        await send(chat_id,"Выберите один из вариантов ниже.",consent_kb()); return

    if t=="selected_product":
        if text=="🔙 К каталогу":
            user_states.pop(chat_id,None); await show_catalog(chat_id); return
        if text=="🛒 Заказать":
            p=get_product(state["product_id"])
            if not p or not p["is_active"]:
                user_states.pop(chat_id,None); await send(chat_id,"Товар недоступен.",main_kb()); return
            u=get_user(chat_id) or {}; name=u.get("name") or ""; phone=u.get("phone") or ""
            if not name:
                user_states[chat_id]={"type":"order_name","product_id":p["id"]}; await send(chat_id,"Как вас зовут для оформления заказа?",back_kb()); return
            if not phone:
                user_states[chat_id]={"type":"order_phone","product_id":p["id"],"name":name}; await send(chat_id,"Укажите номер телефона:",phone_kb()); return
            user_states[chat_id]={"type":"order_confirm","product_id":p["id"],"name":name,"phone":phone}
            price=f"{p['price_rub']:,}".replace(","," ")
            await send(chat_id,f"🧾 <b>Проверьте заказ</b>\n\n💎 {esc(p['name'])}\n💰 {price} ₽\n👤 {esc(name)}\n📞 {esc(phone)}\n\nВсё верно?",order_confirm_kb()); return
        await show_product(chat_id,get_product(state["product_id"]))
        return
    if t=="admin_find_user":
        rs=search_users(text,20)
        if not rs: await send(chat_id,"Пользователь не найден.",admin_users_kb()); return
        rows=[]
        for u in rs: rows.append([{ "text":f"{u.get('username') or u.get('name') or u['telegram_id']} · {u['telegram_id']}" }])
        rows.append([{ "text":"🔙 Пользователи" }]); user_states[chat_id]={"type":"admin_find_results","results":{str(u['telegram_id']):u for u in rs}}; await send(chat_id,"👥 <b>Результаты</b>",kb(rows)); return
    if t=="admin_find_results":
        found=None
        for u in state.get("results",{}).values():
            label=f"{u.get('username') or u.get('name') or u['telegram_id']} · {u['telegram_id']}"
            if text==label: found=u; break
        if not found: await send(chat_id,"Выберите пользователя.",back_kb()); return
        user_states.pop(chat_id,None); await admin_user_card(chat_id,found['telegram_id']); return

    if t=="admin_prediction_add":
        if len(text)<10: await send(chat_id,"Слишком короткий текст.",back_kb()); return
        pid=add_daily_prediction(text[:2000]); add_admin_audit(chat_id,'content_add','daily_prediction',pid,''); user_states.pop(chat_id,None); await admin_predictions_menu(chat_id); return
    if t=="admin_prediction_edit":
        update_daily_prediction(state['prediction_id'],text[:2000]); add_admin_audit(chat_id,'content_edit','daily_prediction',state['prediction_id'],''); user_states.pop(chat_id,None); await admin_predictions_menu(chat_id); return
    if t=="admin_prediction_action":
        pid=state['prediction_id']; p=next((x for x in get_daily_predictions(False) if x['id']==pid),None)
        if not p: user_states.pop(chat_id,None); await admin_predictions_menu(chat_id); return
        if text=="✏️ Изменить": user_states[chat_id]={'type':'admin_prediction_edit','prediction_id':pid}; await send(chat_id,'Введите новый текст прогноза:',back_kb()); return
        if text in {"⏸ Скрыть","▶️ Показать"}: set_daily_prediction_active(pid,not p['is_active']); user_states.pop(chat_id,None); await admin_predictions_menu(chat_id); return
        if text=="🗑 Удалить": delete_daily_prediction(pid); add_admin_audit(chat_id,'content_delete','daily_prediction',pid,''); user_states.pop(chat_id,None); await admin_predictions_menu(chat_id); return
        if text=="🔙 Дневные прогнозы": user_states.pop(chat_id,None); await admin_predictions_menu(chat_id); return
        await send(chat_id,'Выберите действие.',back_kb()); return
    if t=="admin_magic_add":
        if not text: await send(chat_id,'Введите ответ.',back_kb()); return
        pid=add_magic8_answer(text[:300]); add_admin_audit(chat_id,'content_add','magic8',pid,''); user_states.pop(chat_id,None); await admin_magic_menu(chat_id); return
    if t=="admin_magic_action":
        pid=state['magic_id']; p=next((x for x in get_magic8_answers(False) if x['id']==pid),None)
        if not p: user_states.pop(chat_id,None); await admin_magic_menu(chat_id); return
        if text=="✏️ Изменить": user_states[chat_id]={'type':'admin_magic_edit','magic_id':pid}; await send(chat_id,'Введите новый ответ:',back_kb()); return
        if text in {"⏸ Скрыть","▶️ Показать"}: set_magic8_active(pid,not p['is_active']); user_states.pop(chat_id,None); await admin_magic_menu(chat_id); return
        if text=="🔙 Ответы шара": user_states.pop(chat_id,None); await admin_magic_menu(chat_id); return
        await send(chat_id,'Выберите действие.',back_kb()); return
    if t=="admin_magic_edit":
        update_magic8_answer(state['magic_id'],text[:300]); add_admin_audit(chat_id,'content_edit','magic8',state['magic_id'],''); user_states.pop(chat_id,None); await admin_magic_menu(chat_id); return

    if t=="admin_prediction_list":
        m=re.match(r"^[🟢⚪] #(\d+)",text)
        if m:
            pid=int(m.group(1)); p=next((x for x in get_daily_predictions(False) if x['id']==pid),None)
            if p:
                user_states[chat_id]={"type":"admin_prediction_action","prediction_id":pid}
                await send(chat_id,f"🔮 <b>Прогноз #{pid}</b>\n\n{esc(p['text'])}",kb([[{"text":"✏️ Изменить"}],[{"text":"⏸ Скрыть" if p['is_active'] else "▶️ Показать"}],[{"text":"🗑 Удалить"}],[{"text":"🔙 Дневные прогнозы"}]])); return
        if text=="➕ Добавить прогноз": user_states[chat_id]={"type":"admin_prediction_add"}; await send(chat_id,'Введите текст нового прогноза:',back_kb()); return
        if text=="🔙 Контент": user_states.pop(chat_id,None); await admin_content_menu(chat_id); return
    if t=="admin_magic_list":
        m=re.match(r"^[🟢⚪] #(\d+)",text)
        if m:
            mid=int(m.group(1)); p=next((x for x in get_magic8_answers(False) if x['id']==mid),None)
            if p:
                user_states[chat_id]={"type":"admin_magic_action","magic_id":mid}
                await send(chat_id,f"🎱 <b>Ответ #{mid}</b>\n\n{esc(p['text'])}",kb([[{"text":"✏️ Изменить"}],[{"text":"⏸ Скрыть" if p['is_active'] else "▶️ Показать"}],[{"text":"🔙 Ответы шара"}]])); return
        if text=="➕ Добавить ответ": user_states[chat_id]={"type":"admin_magic_add"}; await send(chat_id,'Введите новый ответ шара:',back_kb()); return

    if t=="admin_prediction_add":
        if len(text)<10: await send(chat_id,"Слишком короткий текст.",back_kb()); return
        pid=add_daily_prediction(text[:2000]); add_admin_audit(chat_id,'content_add','daily_prediction',pid,''); user_states.pop(chat_id,None); await admin_predictions_menu(chat_id); return
    if t=="admin_prediction_edit":
        update_daily_prediction(state['prediction_id'],text[:2000]); add_admin_audit(chat_id,'content_edit','daily_prediction',state['prediction_id'],''); user_states.pop(chat_id,None); await admin_predictions_menu(chat_id); return
    if t=="admin_prediction_action":
        pid=state['prediction_id']; p=next((x for x in get_daily_predictions(False) if x['id']==pid),None)
        if not p: user_states.pop(chat_id,None); await admin_predictions_menu(chat_id); return
        if text=="✏️ Изменить": user_states[chat_id]={'type':'admin_prediction_edit','prediction_id':pid}; await send(chat_id,'Введите новый текст прогноза:',back_kb()); return
        if text in {"⏸ Скрыть","▶️ Показать"}: set_daily_prediction_active(pid,not p['is_active']); user_states.pop(chat_id,None); await admin_predictions_menu(chat_id); return
        if text=="🗑 Удалить": delete_daily_prediction(pid); add_admin_audit(chat_id,'content_delete','daily_prediction',pid,''); user_states.pop(chat_id,None); await admin_predictions_menu(chat_id); return
        if text=="🔙 Дневные прогнозы": user_states.pop(chat_id,None); await admin_predictions_menu(chat_id); return
        await send(chat_id,'Выберите действие.',back_kb()); return
    if t=="admin_magic_add":
        if not text: await send(chat_id,'Введите ответ.',back_kb()); return
        pid=add_magic8_answer(text[:300]); add_admin_audit(chat_id,'content_add','magic8',pid,''); user_states.pop(chat_id,None); await admin_magic_menu(chat_id); return
    if t=="admin_magic_action":
        pid=state['magic_id']; p=next((x for x in get_magic8_answers(False) if x['id']==pid),None)
        if not p: user_states.pop(chat_id,None); await admin_magic_menu(chat_id); return
        if text=="✏️ Изменить": user_states[chat_id]={'type':'admin_magic_edit','magic_id':pid}; await send(chat_id,'Введите новый ответ:',back_kb()); return
        if text in {"⏸ Скрыть","▶️ Показать"}: set_magic8_active(pid,not p['is_active']); user_states.pop(chat_id,None); await admin_magic_menu(chat_id); return
        if text=="🔙 Ответы шара": user_states.pop(chat_id,None); await admin_magic_menu(chat_id); return
        await send(chat_id,'Выберите действие.',back_kb()); return
    if t=="admin_magic_edit":
        update_magic8_answer(state['magic_id'],text[:300]); add_admin_audit(chat_id,'content_edit','magic8',state['magic_id'],''); user_states.pop(chat_id,None); await admin_magic_menu(chat_id); return
    if t=="admin_broadcast_audience":
        mp={"👥 Все согласившиеся":"all","🔔 С подпиской":"subscribed","📦 Покупатели":"buyers","🆕 Новые сегодня":"new_today"}
        if text not in mp: await send(chat_id,"Выберите аудиторию.",admin_broadcast_kb()); return
        user_states[chat_id]={"type":"admin_broadcast_text","audience":mp[text]}; await send(chat_id,"Введите текст рассылки. /cancel — отмена",back_kb()); return
    if t=="admin_broadcast_text":
        recipients=get_broadcast_recipients(state['audience']); sent_count=0; failed=0
        for uid in recipients:
            try: await send(uid,text,main_kb()); sent_count+=1; await asyncio.sleep(.05)
            except Exception: failed+=1
        add_admin_audit(chat_id,"broadcast","users",state['audience'],f"sent={sent_count}; failed={failed}"); user_states.pop(chat_id,None); await send(chat_id,f"📣 Рассылка завершена.\nОтправлено: {sent_count}\nОшибок: {failed}",admin_kb()); return
    if t=="admin_selected_product":
        p=get_product(state["product_id"])
        if not p:
            user_states.pop(chat_id,None); await send(chat_id,"Товар не найден.",admin_kb()); return
        if text=="🔙 Каталог админа":
            user_states.pop(chat_id,None); await admin_catalog(chat_id); return
        if text=="✏️ Изменить название":
            user_states[chat_id]={"type":"admin_edit_name","product_id":p["id"]}; await send(chat_id,"Введите новое название:",back_kb()); return
        if text=="📝 Изменить описание":
            user_states[chat_id]={"type":"admin_edit_description","product_id":p["id"]}; await send(chat_id,"Введите новое описание:",back_kb()); return
        if text=="💰 Изменить цену":
            user_states[chat_id]={"type":"admin_edit_price","product_id":p["id"]}; await send(chat_id,"Введите новую цену:",back_kb()); return
        if text=="🖼 Изменить изображение":
            user_states[chat_id]={"type":"admin_edit_image","product_id":p["id"]}; await send(chat_id,"Введите имя файла из static/images или - чтобы убрать.",back_kb()); return
        if text=="🖼 Изменить изображение":
            user_states[chat_id]={"type":"admin_edit_image","product_id":p["id"]}; await send(chat_id,"Введите имя файла из static/images, например <b>dzi_9.png</b>, или - чтобы убрать:",back_kb()); return
        if text in {"⏸ Скрыть товар","▶️ Показать товар"}:
            set_product_active(p["id"],not p["is_active"]); await admin_product(chat_id,get_product(p["id"])); return
        if text == "🗑 Удалить товар":
            deleted, order_count = delete_product(p["id"])
            user_states.pop(chat_id, None)
            if deleted:
                await send(chat_id, "🗑 Товар удалён.", admin_kb())
            else:
                await send(
                    chat_id,
                    "Товар нельзя удалить: по нему уже есть заказы. Его можно только скрыть.",
                    admin_kb(),
                )
            return
        await admin_product(chat_id,p); return
    if t=="magic8_question":
        if not text or len(text)>250 or is_bad(text): await send(chat_id,"Сформулируйте короткий и уважительный вопрос.",back_kb()); return
        ok,remaining=consume_magic8_question(chat_id)
        if not ok: user_states.pop(chat_id,None); await send(chat_id,"🎱 На сегодня лимит исчерпан.",main_kb()); return
        import random
        user_states.pop(chat_id,None)
        await send_photo(chat_id,"magic8.png","🎱 <b>Чёрный шар 8</b>")
        for msg in ["🔮 Концентрируюсь на вопросе...","✨ Формирую ответ..."]: await typing(chat_id); await send(chat_id,msg); await asyncio.sleep(.8)
        await send(chat_id,f"🎱 <b>Ответ:</b> {esc(get_random_magic8_answer())}\n\nОсталось вопросов сегодня: <b>{remaining}</b>.",kb([[{"text":"❓ Задать ещё вопрос"}],[{"text":"🔙 Главное меню"}]])); return

    if t=="personal_name":
        if not text: await send(chat_id,"Введите имя:",back_kb()); return
        update_user_field(chat_id,"name",text[:50]); user_states[chat_id]={"type":"personal_gender","sphere":state.get("sphere")}; await send(chat_id,"Укажите ваш пол:",gender_kb()); return
    if t=="personal_gender":
        if text not in {"👨 Мужчина","👩 Женщина","🙂 Не хочу указывать"}: await send(chat_id,"Выберите вариант кнопкой.",gender_kb()); return
        update_user_field(chat_id,"gender",gender_from_text(text)); user_states[chat_id]={"type":"personal_birthdate","sphere":state.get("sphere")}; await send(chat_id,"Введите дату рождения в формате ДД.ММ.ГГГГ:",back_kb()); return
    if t=="personal_birthdate":
        if not valid_date(text): await send(chat_id,"Неверная дата. Формат ДД.ММ.ГГГГ:",back_kb()); return
        update_user_field(chat_id,"birthdate",text); user_states.pop(chat_id,None); await produce_forecast(chat_id,get_user(chat_id),state.get("sphere")); return
    if t=="delete_data":
        if text=="✅ Да, удалить":
            delete_user_data(chat_id); user_states.pop(chat_id,None); await send(chat_id,"🗑 Данные удалены. Если захотите вернуться, отправьте /start.",consent_kb()); user_states[chat_id]={"type":"consent"}; return
        user_states.pop(chat_id,None); await send(chat_id,"Удаление отменено.",main_kb()); return

    if t=="notification_time":
        if text=="⌨️ Другое время": user_states[chat_id]={"type":"notification_custom_time"}; await send(chat_id,"Введите время ЧЧ:ММ, например 09:30:",back_kb()); return
        if not valid_time(text): await send(chat_id,"Выберите время кнопкой.",time_kb()); return
        set_subscription(chat_id,text,True); user_states.pop(chat_id,None); await send(chat_id,f"🔔 Уведомления включены. Каждый день в {text} по Москве я сообщу, что ваше предсказание готово.",main_kb()); return
    if t=="notification_custom_time":
        if not valid_time(text): await send(chat_id,"Неверное время. Пример: 09:30",back_kb()); return
        set_subscription(chat_id,text,True); user_states.pop(chat_id,None); await send(chat_id,f"🔔 Уведомления включены на {text} по Москве.",main_kb()); return
    if t=="gift_prediction_username":
        username=text.strip()
        if not username.startswith("@") or len(username)>33: await send(chat_id,"Введите логин в формате @username.",back_kb()); return
        recipient=get_user_by_username(username)
        if not recipient: user_states.pop(chat_id,None); await send(chat_id,"Пользователь не найден или ещё не запускал бота.",main_kb()); return
        if recipient['telegram_id']==chat_id: await send(chat_id,"Нельзя отправить подарок самому себе.",back_kb()); return
        user_states.pop(chat_id,None); await ai_processing(chat_id,"daily"); pred=get_random_prediction(); await send(recipient['telegram_id'],"🎁 <b>Вам подарили предсказание!</b>\n\n"+pred,main_kb()); await send(chat_id,f"✅ Предсказание отправлено <b>{esc(username)}</b>.",main_kb()); return
    if t=="order_name":
        if not text: await send(chat_id,"Введите имя:",back_kb()); return
        user_states[chat_id]={"type":"order_phone","product_id":state["product_id"],"name":text[:50]}; await send(chat_id,"Теперь укажите номер телефона для связи.",phone_kb()); return
    if t in {"order_phone","account_edit_phone"}:
        if text=="⌨️ Ввести номер вручную": user_states[chat_id]={"type":"order_manual_phone","product_id":state.get("product_id"),"name":state.get("name")} if t=="order_phone" else {"type":"account_manual_phone"}; await send(chat_id,"Введите номер телефона:",back_kb()); return
        phone=message.get("contact",{}).get("phone_number","") if message.get("contact") else (text if text else "")
        if not valid_phone(phone): await send(chat_id,"Не удалось распознать номер.",phone_kb()); return
        if t=="account_edit_phone": update_user_field(chat_id,"phone",phone); user_states.pop(chat_id,None); await show_account(chat_id); return
        user_states[chat_id]={"type":"order_confirm","product_id":state["product_id"],"name":state["name"],"phone":phone}; p=get_product(state["product_id"]); price=f"{p['price_rub']:,}".replace(","," "); await send(chat_id,f"🧾 <b>Проверьте заказ</b>\n\n💎 {esc(p['name'])}\n💰 {price} ₽\n👤 {esc(state['name'])}\n📞 {esc(phone)}\n\nВсё верно?",order_confirm_kb()); return
    if t=="order_manual_phone":
        if not valid_phone(text): await send(chat_id,"Неверный номер.",back_kb()); return
        user_states[chat_id]={"type":"order_confirm","product_id":state["product_id"],"name":state["name"],"phone":text}; p=get_product(state["product_id"]); price=f"{p['price_rub']:,}".replace(","," "); await send(chat_id,f"🧾 <b>Проверьте заказ</b>\n\n💎 {esc(p['name'])}\n💰 {price} ₽\n👤 {esc(state['name'])}\n📞 {esc(text)}\n\nВсё верно?",order_confirm_kb()); return
    if t=="order_confirm":
        if text!="✅ Подтвердить заказ": user_states.pop(chat_id,None); await send(chat_id,"Заказ отменён.",main_kb()); return
        oid=create_order(chat_id,state["product_id"],state["name"],state["phone"]); add_admin_audit(ADMIN_ID,"new_order","order",oid,f"user={chat_id}"); update_user_field(chat_id,"phone",state["phone"]); p=get_product(state["product_id"]); user_states.pop(chat_id,None)
        await send(chat_id,f"🙏 <b>Спасибо за ваш заказ!</b>\n\nЗаказ №{oid} на браслет «{esc(p['name'])}» принят.\n\nЗаказ передан менеджеру, и скоро приступят к сборке браслета. Менеджер свяжется с вами для подтверждения.",main_kb())
        if get_admin_settings()["new_order"]:
            await send(ADMIN_ID,f"🆕 <b>Новый заказ №{oid}</b>\n\n💎 {esc(p['name'])}\n👤 {esc(state['name'])}\n📞 {esc(state['phone'])}\n🆔 {chat_id}",admin_kb())
        return
    if t=="account_edit_name":
        if text=="🔙 Личный кабинет": user_states.pop(chat_id,None); await show_account(chat_id); return
        update_user_field(chat_id,"name",text[:50]); user_states.pop(chat_id,None); await show_account(chat_id); return
    if t=="account_edit_gender":
        if text not in {"👨 Мужчина","👩 Женщина","🙂 Не хочу указывать"}: await send(chat_id,"Выберите вариант.",gender_kb()); return
        update_user_field(chat_id,"gender",gender_from_text(text)); user_states.pop(chat_id,None); await show_account(chat_id); return
    if t=="account_edit_birthdate":
        if not valid_date(text): await send(chat_id,"Формат ДД.ММ.ГГГГ",back_kb()); return
        update_user_field(chat_id,"birthdate",text); user_states.pop(chat_id,None); await show_account(chat_id); return
    if t=="account_manual_phone":
        if not valid_phone(text): await send(chat_id,"Неверный номер.",back_kb()); return
        update_user_field(chat_id,"phone",text); user_states.pop(chat_id,None); await show_account(chat_id); return
    if t=="review_rating":
        if text not in {"⭐ 1","⭐⭐ 2","⭐⭐⭐ 3","⭐⭐⭐⭐ 4","⭐⭐⭐⭐⭐ 5"}:
            await send(chat_id,"Выберите оценку от 1 до 5.",back_kb()); return
        rating=int(text[-1])
        user_states[chat_id]={"type":"review_text","order_id":state["order_id"],"rating":rating}
        await send(chat_id,"Напишите короткий отзыв или напишите «Пропустить».",back_kb()); return
    if t=="review_text":
        review_text="" if text.lower()=="пропустить" else text[:1000]
        add_review(state["order_id"],chat_id,state["rating"],review_text)
        user_states.pop(chat_id,None)
        await send(chat_id,"⭐ Спасибо за отзыв!",account_kb()); return

    if t=="admin_order":
        if text=="🔙 К списку заказов": user_states.pop(chat_id,None); await admin_orders(chat_id); return
        o=get_order(state["order_id"])
        if not o: user_states.pop(chat_id,None); await send(chat_id,"Заказ не найден.",admin_kb()); return
        if text not in {"✅ Подтвердить","❌ Отклонить","🔧 В сборке","📦 Готов","🚚 В доставке","✅ Завершить"}:
            await send(chat_id,"Выберите действие.",admin_order_kb())
            return
        status = {"✅ Подтвердить":"confirmed","❌ Отклонить":"rejected","🔧 В сборке":"assembling","📦 Готов":"ready","🚚 В доставке":"shipping","✅ Завершить":"completed"}[text]
        set_order_status(o["id"], status)
        user_states.pop(chat_id, None)
        add_admin_audit(chat_id,"order_status","order",o["id"],status)
        if get_admin_settings()["status_change"]:
            await send(o["telegram_id"], f"📦 <b>Статус заказа №{o['id']}</b>\n\n{status_text(status)}", main_kb())
        await send(chat_id, f"Статус заказа №{o['id']} изменён: {status_text(status)}", admin_kb())
        return
    if t=="admin_add_name":
        user_states[chat_id]={"type":"admin_add_desc","name":text[:100]}; await send(chat_id,"Введите описание:",back_kb()); return
    if t=="admin_add_desc":
        user_states[chat_id]={"type":"admin_add_price","name":state["name"],"description":text[:1000]}; await send(chat_id,"Введите цену в рублях:",back_kb()); return
    if t=="admin_add_price":
        try: price=int(text.replace(" ","")); assert 0<=price<=10000000
        except Exception: await send(chat_id,"Введите целую цену от 0 до 10 000 000.",back_kb()); return
        add_product(state["name"],state["description"],price); user_states.pop(chat_id,None); await send(chat_id,"✅ Товар добавлен.",admin_kb()); return
    if t=="admin_edit_image":
        value=text.strip()
        if value=="-": value=None
        elif not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}",value) or not (IMAGES_DIR/value).exists():
            await send(chat_id,"Файл не найден в static/images.",back_kb()); return
        update_product_image(state["product_id"],value); add_admin_audit(chat_id,"product_image","product",state["product_id"],str(value)); user_states.pop(chat_id,None); await admin_product(chat_id,get_product(state["product_id"])); return
    if t=="admin_edit_image":
        value=text.strip()
        if value=="-": value=None
        elif not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}",value) or not (IMAGES_DIR/value).exists(): await send(chat_id,"Файл не найден.",back_kb()); return
        update_product_image(state['product_id'],value); add_admin_audit(chat_id,"product_image","product",state['product_id'],str(value)); user_states.pop(chat_id,None); await admin_product(chat_id,get_product(state['product_id'])); return
    if t.startswith("admin_edit_"):
        field=t.replace("admin_edit_","")
        value=text
        if field=="price":
            try: value=int(text.replace(" ","")); assert 0<=value<=10000000
            except Exception: await send(chat_id,"Введите корректную цену.",back_kb()); return
        update_product(state["product_id"],{"name":"name","description":"description","price":"price_rub"}[field],value); user_states.pop(chat_id,None); await admin_catalog(chat_id); return
    if t=="admin_broadcast":
        if chat_id!=ADMIN_ID: user_states.pop(chat_id,None); return
        if text=="/cancel": user_states.pop(chat_id,None); await send(chat_id,"Рассылка отменена.",admin_kb()); return
        users=get_all_users(10000); sent_count=0
        for u in users:
            try: await send(u["telegram_id"],text,main_kb()); sent_count+=1; await asyncio.sleep(.04)
            except Exception: pass
        user_states.pop(chat_id,None); await send(chat_id,f"📣 Рассылка завершена. Отправлено: {sent_count}.",admin_kb()); return

async def show_magic8(chat_id):
    remaining=get_magic8_remaining(chat_id)
    await send_photo(chat_id,"magic8.png","🎱 <b>Мистический Чёрный шар 8</b>")
    await send(chat_id,f"Задайте вопрос, на который можно ответить «Да» или «Нет».\n\nСегодня осталось вопросов: <b>{remaining}</b> из 3.",kb([[{"text":"❓ Задать вопрос"}],[{"text":"🔙 Главное меню"}]]))

async def process_update(update):
    if "message" not in update: return
    m=update["message"]; chat_id=int(m["chat"]["id"]); text=(m.get("text") or "").strip()
    sender=m.get("from") or {}; uname=(sender.get("username") or "").strip().lstrip("@").lower()
    if uname:
        try: update_username(chat_id,uname)
        except Exception: pass
    if not is_access_allowed(chat_id):
        await send(chat_id, restricted_access_message())
        return
    if len(text) > 1000:
        await send(chat_id,"Сообщение слишком длинное.",main_kb()); return
    if chat_id != ADMIN_ID and text and is_bad(text):
        if get_admin_settings()["security"]:
            try:
                await send(ADMIN_ID, f"🚨 <b>Модерация</b>\n\nПользователь: {chat_id}", admin_kb())
                add_admin_audit(ADMIN_ID, "moderation", "security", chat_id, "blocked message")
            except Exception:
                pass
        await send(chat_id,"Пожалуйста, общайтесь уважительно. Оскорбительные и нецензурные сообщения не обрабатываются.",main_kb()); return
    if not flood_ok(chat_id):
        if chat_id != ADMIN_ID and get_admin_settings()["security"]:
            try:
                await send(ADMIN_ID, f"🚨 <b>Антиспам</b>\n\nПользователь: {chat_id}", admin_kb())
                add_admin_audit(ADMIN_ID, "flood", "security", chat_id, "rate limit")
            except Exception:
                pass
        return
    existed=get_user(chat_id)
    ensure_user(chat_id)
    u=get_user(chat_id)
    if existed is None and chat_id != ADMIN_ID and get_admin_settings()["new_user"]:
        try:
            await send(ADMIN_ID, f"👤 <b>Новый пользователь</b>\n\n🆔 {chat_id}", admin_kb())
            add_admin_audit(ADMIN_ID, "new_user", "user", chat_id, "")
        except Exception:
            pass
    state=user_states.get(chat_id)

    # /start is always a safe reset command once consent has been granted.
    if u.get("consent_given") and text == "/start":
        user_states.pop(chat_id, None)
        await send(chat_id, "🔮 <b>С возвращением!</b>\n\nВыберите действие:", main_kb())
        return

    # Consent is the source of truth. If the published consent version changes,
    # the user must see the documents and explicitly confirm the new version.
    consent_ok = bool(u.get("consent_given")) and u.get("consent_version") == CONSENT_VERSION

    if not consent_ok:
        if text == "✅ Ознакомился(лась) и даю согласие":
            give_consent(chat_id)
            user_states.pop(chat_id, None)
            await send(
                chat_id,
                f"✅ Спасибо. Согласие сохранено (версия {CONSENT_VERSION}). Теперь доступен весь функционал.",
                main_kb(),
            )
            return
        if text == "❌ Не согласен(на)":
            user_states[chat_id] = {"type": "consent"}
            await send(
                chat_id,
                "Без согласия бот не сможет сохранять ваши персональные данные и использовать функции, для которых они необходимы.",
                consent_kb(),
            )
            return
        if text == "/start" or not state or state.get("type") != "consent":
            user_states[chat_id] = {"type": "consent"}
            await welcome(chat_id)
            return
        await handle_state(chat_id, text, state, m)
        return

    if state:
        await handle_state(chat_id, text, state, m)
        return
    if text=="/admin" and chat_id==ADMIN_ID: await send(chat_id,f"👑 <b>Админ-панель</b>\n\nРежим доступа: <b>{BOT_ACCESS_MODE}</b>\nТестировщиков: <b>{len(ALLOWED_USER_IDS)}</b>",admin_kb()); return
    if text in {"🔙 Главное меню","🔙 Главнoe меню"}: await send(chat_id,"Главное меню:",main_kb()); return
    if text=="🔮 Предсказание на день": await ai_processing(chat_id,"daily"); await send(chat_id,f"🔮 <b>Предсказание на {datetime.now(MOSCOW_TZ):%d.%m.%Y}</b>\n\n{esc(get_random_daily_prediction() or get_random_prediction())}",main_kb()); return
    if text=="✨ Персональное": await begin_personal(chat_id); return
    if text=="🎯 По сферам": await send(chat_id,"Выберите сферу:",sphere_kb()); return
    sm={"❤️ Любовь":"love","💼 Карьера":"career","💰 Финансы":"finance","👨‍👩‍👧 Семья":"family","🌿 Самочувствие":"health","📚 Развитие":"growth"}
    if text in sm: await begin_personal(chat_id,sm[text]); return
    if text=="💎 Каталог": await show_catalog(chat_id); return
    if text=="🎱 Чёрный шар 8": await show_magic8(chat_id); return
    if text in {"❓ Задать вопрос","❓ Задать ещё вопрос"}:
        if get_magic8_remaining(chat_id)<=0: await send(chat_id,"🎱 Лимит из 3 вопросов на сегодня исчерпан.",main_kb()); return
        user_states[chat_id]={"type":"magic8_question"}; await send(chat_id,"Сформулируйте вопрос, на который возможен ответ «Да» или «Нет».",back_kb()); return
    if text=="🎁 Подарок другу":
        await send(chat_id,"🎁 <b>Подарок другу</b>\n\nВыберите подарок:",kb([[{"text":"🔮 Подарить предсказание"}],[{"text":"💎 Подарить браслет"}],[{"text":"🔙 Главное меню"}]])); return
    if text=="🔮 Подарить предсказание":
        user_states[chat_id]={"type":"gift_prediction_username"}; await send(chat_id,"🎁 <b>Подарить предсказание</b>\n\nВведите Telegram-логин получателя в формате <b>@username</b>.",back_kb()); return
        await ai_processing(chat_id,"daily"); await send(chat_id,"🎁 <b>Предсказание для друга</b>\n\n"+get_random_prediction()+"\n\nПерешлите это сообщение другу.",main_kb()); return
    if text=="💎 Подарить браслет": await show_catalog(chat_id); return
    if text=="🔙 К каталогу": await show_catalog(chat_id); return
    product=get_product_by_name(text)
    if product:
        if not product["is_active"]: await send(chat_id,"Товар скрыт.",main_kb()); return
        user_states[chat_id]={"type":"selected_product","product_id":product["id"]}; await show_product(chat_id,product); return
    if text=="🛒 Заказать":
        state=user_states.get(chat_id)
        if not state or state.get("type")!="selected_product": await show_catalog(chat_id); return
        p=get_product(state["product_id"])
        if not p or not p["is_active"]: user_states.pop(chat_id,None); await send(chat_id,"Товар недоступен.",main_kb()); return
        u=get_user(chat_id); name=u["name"] if u else ""; phone=u["phone"] if u else ""
        if not name: user_states[chat_id]={"type":"order_name","product_id":p["id"]}; await send(chat_id,"Как вас зовут для оформления заказа?",back_kb()); return
        if not phone: user_states[chat_id]={"type":"order_phone","product_id":p["id"],"name":name}; await send(chat_id,"Укажите номер телефона:",phone_kb()); return
        user_states[chat_id]={"type":"order_confirm","product_id":p["id"],"name":name,"phone":phone}; price=f"{p['price_rub']:,}".replace(","," "); await send(chat_id,f"🧾 <b>Проверьте заказ</b>\n\n💎 {esc(p['name'])}\n💰 {price} ₽\n👤 {esc(name)}\n📞 {esc(phone)}\n\nВсё верно?",order_confirm_kb()); return
    if text == "📄 Правовые документы":
        await show_legal_documents(chat_id)
        return

    if text=="🔔 Уведомления": await show_notifications(chat_id); return
    if text in {"🔔 Включить уведомления","🕒 Изменить время"}: user_states[chat_id]={"type":"notification_time"}; await send(chat_id,"Выберите время:",time_kb()); return
    if text=="🔕 Отписаться": set_subscription(chat_id,"00:00",False); await send(chat_id,"🔕 Уведомления отключены.",main_kb()); return
    if text=="👤 Личный кабинет": await show_account(chat_id); return
    if text=="✏️ Изменить данные": await send(chat_id,"Что изменить?",edit_account_kb()); return
    if text=="Имя": user_states[chat_id]={"type":"account_edit_name"}; await send(chat_id,"Введите новое имя:",back_kb()); return
    if text=="Пол": user_states[chat_id]={"type":"account_edit_gender"}; await send(chat_id,"Выберите пол:",gender_kb()); return
    if text=="Дата рождения": user_states[chat_id]={"type":"account_edit_birthdate"}; await send(chat_id,"Введите новую дату:",back_kb()); return
    if text=="Телефон": user_states[chat_id]={"type":"account_edit_phone"}; await send(chat_id,"Выберите способ указать номер:",phone_kb()); return
    if text=="🔙 Личный кабинет": await show_account(chat_id); return
    if text=="📦 Мои заказы": await show_orders(chat_id); return
    if text.startswith("⭐ Заказ №") and "оставить отзыв" in text:
        try:
            oid=int(text.split("№",1)[1].split(" ",1)[0])
            order=get_order(oid)
            if not order or order["telegram_id"] != chat_id or order["status"] != "completed":
                await send(chat_id,"Этот заказ недоступен для отзыва.",account_kb()); return
            user_states[chat_id]={"type":"review_rating","order_id":oid}
            await send(chat_id,"⭐ Оцените заказ:",kb([[{"text":"⭐ 1"},{"text":"⭐⭐ 2"},{"text":"⭐⭐⭐ 3"}],[{"text":"⭐⭐⭐⭐ 4"},{"text":"⭐⭐⭐⭐⭐ 5"}],[{"text":"🔙 Личный кабинет"}]])); return
        except Exception:
            await send(chat_id,"Не удалось открыть оценку заказа.",account_kb()); return
    if text=="📊 Мой день":
        u=get_user(chat_id)
        if not u.get("birthdate"): await begin_personal(chat_id); return
        await ai_processing(chat_id,"personal")
        await send(chat_id,"📊 <b>Мой день</b>\n\n"+get_personal_forecast(u["birthdate"],u["gender"] or "other",u["name"] or "Друг"),account_kb()); return
    if text=="🗑 Удалить мои данные":
        user_states[chat_id]={"type":"delete_data"}; await send(chat_id,"⚠️ Будут удалены профиль, заказы, уведомления и связанные данные. Продолжить?",kb([[{"text":"✅ Да, удалить"}],[{"text":"❌ Отмена"}]])); return
    if text=="⭐ Мои отзывы":
        rows=get_recent_reviews(20); mine=[r for r in rows if r[2]==chat_id]
        await send(chat_id,"⭐ У вас пока нет отзывов." if not mine else "\n\n".join([f"⭐ {r[3]}/5\n{esc(r[4] or 'Без текста')}" for r in mine]),account_kb()); return
    if chat_id==ADMIN_ID:
        if text=="📊 Аналитика": await admin_show_analytics(chat_id,None); return
        if text=="Сегодня": await admin_show_analytics(chat_id,1); return
        if text=="7 дней": await admin_show_analytics(chat_id,7); return
        if text=="30 дней": await admin_show_analytics(chat_id,30); return
        if text=="Всё время": await admin_show_analytics(chat_id,None); return
        if text=="📦 Заказы": await admin_orders(chat_id); return
        if text.startswith("Заказ №"):
            try: await admin_order(chat_id,int(text.split("№",1)[1].split(" ",1)[0]))
            except Exception: await send(chat_id,"Некорректный номер заказа.",admin_kb())
            return
        if text=="🔙 К списку заказов": await admin_orders(chat_id); return
        if text=="👥 Пользователи": await admin_users_menu(chat_id); return
        if text=="👥 Последние пользователи": await admin_users_latest(chat_id); return
        if text=="🔎 Найти пользователя": user_states[chat_id]={"type":"admin_find_user"}; await send(chat_id,"Введите Telegram ID, @username, имя, телефон или дату рождения:",back_kb()); return
        if text=="🔙 Пользователи": await admin_users_menu(chat_id); return
        if text=="🛍 Каталог": await admin_catalog(chat_id); return
        if text=="➕ Добавить товар": user_states[chat_id]={"type":"admin_add_name"}; await send(chat_id,"Введите название товара:",back_kb()); return
        if text=="🔙 Каталог админа": await admin_catalog(chat_id); return
        for p in get_products(False):
            if text in {"🟢 "+p["name"],"⚪ "+p["name"]}: user_states[chat_id]={"type":"admin_selected_product","product_id":p["id"]}; await admin_product(chat_id,p); return
        if text=="📣 Рассылка": user_states[chat_id]={"type":"admin_broadcast_audience"}; await send(chat_id,"📣 <b>Выберите аудиторию</b>",admin_broadcast_kb()); return
        if text=="📝 Контент": await admin_content_menu(chat_id); return
        if text=="🔮 Дневные прогнозы": await admin_predictions_menu(chat_id); return
        if text=="➕ Добавить прогноз": user_states[chat_id]={"type":"admin_prediction_add"}; await send(chat_id,"Введите текст нового дневного прогноза:",back_kb()); return
        if text=="🔙 Контент": await admin_content_menu(chat_id); return
        if text=="🎱 Magic 8": await admin_magic_menu(chat_id); return
        if text=="🎱 Ответы шара": await admin_magic_menu(chat_id); return
        if text=="➕ Добавить ответ": user_states[chat_id]={"type":"admin_magic_add"}; await send(chat_id,"Введите новый ответ шара:",back_kb()); return
        if text=="⚖️ Документы": await admin_documents_view(chat_id); return
        if text=="📤 Экспорт": await send(chat_id,"📤 <b>Экспорт данных</b>",admin_export_kb()); return
        if text=="👥 Пользователи CSV": await export_csv(chat_id,"users"); return
        if text=="📦 Заказы CSV": await export_csv(chat_id,"orders"); return
        if text=="⭐ Отзывы CSV": await export_csv(chat_id,"reviews"); return
        if text=="💾 Резервная копия": await send(chat_id,"💾 <b>Резервное копирование</b>",admin_backup_kb()); return
        if text=="💾 Сделать резервную копию": await make_backup(chat_id); return
        if text=="📋 Последняя копия": await backup_status(chat_id); return
        if text=="🚨 Уведомления": await admin_notifications_view(chat_id); return
        if text in {"🟢 Новые заказы","⚪ Новые заказы","🟢 Изменение статуса","⚪ Изменение статуса","🟢 Новые пользователи","⚪ Новые пользователи","🟢 Безопасность","⚪ Безопасность"}:
            s=get_admin_settings(); mp={"🟢 Новые заказы":"new_order","⚪ Новые заказы":"new_order","🟢 Изменение статуса":"status_change","⚪ Изменение статуса":"status_change","🟢 Новые пользователи":"new_user","⚪ Новые пользователи":"new_user","🟢 Безопасность":"security","⚪ Безопасность":"security"}; key=mp[text]; set_admin_notifications(key,not s[key]); add_admin_audit(chat_id,"notification_setting","admin",1,f"{key}={not s[key]}"); await admin_notifications_view(chat_id); return
        if text=="🛡 Журнал": await admin_audit_view(chat_id); return
        if text=="⭐ Отзывы":
            rows=get_recent_reviews(20)
            if not rows: await send(chat_id,"⭐ Отзывов пока нет.",admin_kb()); return
            await send(chat_id,"\n\n".join([f"Заказ №{r[1]} · {r[3]}/5\n{esc(r[4] or 'Без текста')}" for r in rows]),admin_kb()); return
        if text=="🚪 Выйти": await send(chat_id,"Вы вышли из админ-панели.",main_kb()); return
    await send(chat_id,"Используйте кнопки меню.",main_kb())

async def polling():
    offset=0
    await tg("deleteWebhook",{"drop_pending_updates":False})
    print("BOT POLLING STARTED")
    while True:
        try:
            updates=await tg("getUpdates",{"offset":offset,"timeout":50,"allowed_updates":["message"]},timeout=65)
            for u in updates:
                offset=u["update_id"]+1
                try: await process_update(u)
                except Exception as exc: print(f"UPDATE ERROR: {exc}")
        except asyncio.CancelledError: raise
        except Exception as exc: print(f"POLLING ERROR: {exc}"); await asyncio.sleep(3)

async def scheduler():
    while True:
        try:
            now=datetime.now(MOSCOW_TZ); hhmm=now.strftime("%H:%M"); day=now.strftime("%Y-%m-%d")
            for sub in get_active_subscriptions(hhmm):
                key=(sub["telegram_id"],day,hhmm)
                if last_notification_sent.get(sub["telegram_id"])==key: continue
                try:
                    await send(sub["telegram_id"],"🔔 <b>Ваше предсказание на день готово</b>\n\nУзнайте, что вас ждёт.\n\n"+get_random_prediction(),main_kb())
                    last_notification_sent[sub["telegram_id"]]=key
                except Exception as exc: print(f"NOTIFICATION ERROR: {exc}")
        except asyncio.CancelledError: raise
        except Exception as exc: print(f"SCHEDULER ERROR: {exc}")
        await asyncio.sleep(20)

async def bot_leader(app_):
    global bot_lock_conn, bot_lock_cursor
    while True:
        try:
            bot_lock_conn, bot_lock_cursor = acquire_bot_lock()
            print("POSTGRES LOCK ACQUIRED")
            app_["polling"] = asyncio.create_task(polling())
            app_["scheduler"] = asyncio.create_task(scheduler())
            done, pending = await asyncio.wait({app_["polling"], app_["scheduler"]}, return_when=asyncio.FIRST_EXCEPTION)
            for task in pending:
                task.cancel()
            for task in pending:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            for task in done:
                exc = task.exception()
                if exc:
                    raise exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"LEADER ERROR: {exc}")
        finally:
            for key in ("polling", "scheduler"):
                task = app_.get(key)
                if task:
                    task.cancel()
            for key in ("polling", "scheduler"):
                task = app_.get(key)
                if task:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        print(f"TASK CLEANUP ERROR: {exc}")
            app_.pop("polling", None)
            app_.pop("scheduler", None)
            if bot_lock_cursor:
                try: bot_lock_cursor.close()
                except Exception: pass
                bot_lock_cursor = None
            if bot_lock_conn:
                try: bot_lock_conn.close()
                except Exception: pass
                bot_lock_conn = None
        await asyncio.sleep(5)

async def startup(app_):
    global telegram_session
    init_db()

    if reset_all_user_data_once():
        print("USER DATA RESET COMPLETED")
    telegram_session = aiohttp.ClientSession()

    if PUBLIC_URL:
        try:
            await telegram_request(
                "setChatMenuButton",
                {
                    "menu_button": {
                        "type": "web_app",
                        "text": "🌌 Приложение",
                        "web_app": {"url": f"{PUBLIC_URL}/app"},
                    }
                },
            )
            print("MINI APP MENU BUTTON CONFIGURED")
        except Exception as exc:
            print(f"MINI APP MENU BUTTON ERROR: {exc}")

    app_["leader"] = asyncio.create_task(bot_leader(app_))

async def cleanup(app_):
    global telegram_session,bot_lock_conn,bot_lock_cursor
    for k in ("polling","scheduler"):
        t=app_.get(k)
        if t: t.cancel()
    for k in ("polling","scheduler"):
        t=app_.get(k)
        if t:
            try: await t
            except asyncio.CancelledError: pass
    if telegram_session: await telegram_session.close(); telegram_session=None
    if bot_lock_cursor: bot_lock_cursor.close(); bot_lock_cursor=None
    if bot_lock_conn: bot_lock_conn.close(); bot_lock_conn=None

app.on_startup.append(startup); app.on_cleanup.append(cleanup)
if __name__=="__main__": web.run_app(app,host="0.0.0.0",port=PORT)
