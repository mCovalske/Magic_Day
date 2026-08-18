# BUILD: PREDBOT-2026-08-18-GIFT-NOTIFY-01
import asyncio
import html
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
from aiohttp import web

from db import (
    add_review, consume_magic8_question, delete_product, delete_user_data, give_consent, get_magic8_remaining, get_recent_reviews,
    acquire_bot_lock, add_product, create_order, ensure_user, get_active_subscriptions,
    get_all_users, get_order, get_product, get_product_by_name, get_products, get_recent_orders,
    get_user_by_username, update_username,
    get_stats, get_subscription, get_user, get_user_orders, init_db, set_order_status,
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
IMAGES_DIR = STATIC_DIR / "images"
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
if ADMIN_ID <= 0:
    raise RuntimeError("ADMIN_ID must be set")

app = web.Application()
user_states = {}
user_activity = {}
last_notification_sent = {}
telegram_session = None
bot_lock_conn = None
bot_lock_cursor = None
MAX_ACTIONS_PER_MINUTE = 20


def kb(rows, one_time=False):
    return {"keyboard": rows, "resize_keyboard": True, "one_time_keyboard": one_time, "is_persistent": False}


def main_kb():
    return kb([
        [{"text": "🔮 Предсказание на день"}, {"text": "✨ Персональное"}],
        [{"text": "🎯 По сферам"}, {"text": "💎 Каталог"}],
        [{"text": "🎱 Чёрный шар 8"}, {"text": "🎁 Подарок другу"}],
        [{"text": "🔔 Уведомления"}, {"text": "👤 Личный кабинет"}],
    ])


def consent_kb():
    return kb([
        [{"text": "✅ Я согласен(на)"}],
        [{"text": "❌ Не согласен(на)"}],
    ])

def back_kb(): return kb([[{"text": "🔙 Главное меню"}]])
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


def admin_kb(): return kb([[{"text":"📊 Статистика"},{"text":"📦 Заказы"}],[{"text":"🛍 Каталог"},{"text":"🔔 Уведомления"}],[{"text":"⭐ Отзывы"},{"text":"👥 Пользователи"}],[{"text":"📣 Рассылка"},{"text":"🚪 Выйти"}]])
def admin_order_kb(): return kb([[{"text":"✅ Подтвердить"},{"text":"❌ Отклонить"}],[{"text":"📦 Завершить"}],[{"text":"🔙 К списку заказов"}]])
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
    await send_photo(chat_id,"welcome.png")
    await send(chat_id,"🔮 <b>Добро пожаловать!</b>\n\nЭто развлекательный бот предсказаний, персональных прогнозов, ответов Чёрного шара 8 и каталога бусин Дзи.\n\nПеред началом бот должен получить ваше добровольное согласие на обработку и хранение предоставленных персональных данных.",consent_kb())

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

async def homepage(request):
    f=STATIC_DIR/"index.html"
    return web.FileResponse(f) if f.exists() else web.Response(text="OK")
async def health(request): return web.json_response({"status":"ok","build":"PREDBOT-2026-08-18-FULL-CHECK-02"})
app.router.add_get("/",homepage); app.router.add_get("/health",health); app.router.add_static("/static",path=str(STATIC_DIR),name="static")

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
        if text=="✅ Я согласен(на)":
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
        answers=["Да.","Определённо да!","Без сомнений.","Скорее да, чем нет.","Пока не ясно, попробуйте позже.","Скорее нет, чем да.","Нет.","Определённо нет.","Даже не думайте.","Мой ответ — нет."]
        await send(chat_id,f"🎱 <b>Ответ:</b> {random.choice(answers)}\n\nОсталось вопросов сегодня: <b>{remaining}</b>.",kb([[{"text":"❓ Задать ещё вопрос"}],[{"text":"🔙 Главное меню"}]])); return

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
        username = text.strip()
        if not username.startswith("@") or len(username) < 2 or len(username) > 33:
            await send(chat_id, "Неверный формат. Введите логин в формате <b>@username</b>.", back_kb())
            return
        recipient = get_user_by_username(username)
        if not recipient:
            user_states.pop(chat_id, None)
            await send(chat_id, "Я не нашёл этого пользователя среди тех, кто уже запускал нашего бота. Telegram не позволяет боту первым написать человеку, который ещё не взаимодействовал с ним.\n\nПопросите получателя открыть бота и нажать /start, затем повторите отправку.", main_kb())
            return
        if recipient["telegram_id"] == chat_id:
            await send(chat_id, "Нельзя отправить подарок самому себе. Укажите другой @username.", back_kb())
            return
        user_states.pop(chat_id, None)
        await ai_processing(chat_id, "daily")
        prediction = get_random_prediction()
        await send(recipient["telegram_id"], "🎁 <b>Вам подарили предсказание!</b>\n\n" + prediction, main_kb())
        await send(chat_id, f"✅ Предсказание отправлено пользователю <b>{esc(username)}</b>.", main_kb())
        return
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
        oid=create_order(chat_id,state["product_id"],state["name"],state["phone"]); update_user_field(chat_id,"phone",state["phone"]); p=get_product(state["product_id"]); user_states.pop(chat_id,None)
        await send(chat_id,f"🙏 <b>Спасибо за ваш заказ!</b>\n\nЗаказ №{oid} на браслет «{esc(p['name'])}» принят.\n\nЗаказ передан менеджеру, и скоро приступят к сборке браслета. Менеджер свяжется с вами для подтверждения.",main_kb())
        await send(ADMIN_ID,f"🆕 <b>Новый заказ №{oid}</b>\n\n💎 {esc(p['name'])}\n👤 {esc(state['name'])}\n📞 {esc(state['phone'])}\n🆔 {chat_id}",admin_kb()); return
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
        if text not in {"✅ Подтвердить","❌ Отклонить","📦 Завершить"}:
            await send(chat_id,"Выберите действие.",admin_order_kb())
            return
        status = {
            "✅ Подтвердить": "confirmed",
            "❌ Отклонить": "rejected",
            "📦 Завершить": "completed",
        }[text]
        set_order_status(o["id"], status)
        user_states.pop(chat_id, None)
        await send(o["telegram_id"], f"{status_text(status)} Заказ №{o['id']}.", main_kb())
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
    m=update["message"]
    chat_id=int(m["chat"]["id"])
    text=(m.get("text") or "").strip()
    sender=m.get("from") or {}
    sender_username=(sender.get("username") or "").strip().lstrip("@").lower()
    if sender_username:
        try:
            update_username(chat_id, sender_username)
        except Exception as exc:
            print(f"USERNAME UPDATE ERROR: {exc}")
    if len(text) > 1000:
        await send(chat_id,"Сообщение слишком длинное.",main_kb()); return
    if chat_id != ADMIN_ID and text and is_bad(text):
        await send(chat_id,"Пожалуйста, общайтесь уважительно. Оскорбительные и нецензурные сообщения не обрабатываются.",main_kb()); return
    if not flood_ok(chat_id): return
    ensure_user(chat_id)
    u=get_user(chat_id)
    state=user_states.get(chat_id)

    # /start is always a safe reset command once consent has been granted.
    if u.get("consent_given") and text == "/start":
        user_states.pop(chat_id, None)
        await send(chat_id, "🔮 <b>С возвращением!</b>\n\nВыберите действие:", main_kb())
        return

    # Consent has priority while it is actually pending. The database is the
    # source of truth, so a restart cannot accidentally return to onboarding.
    if not u.get("consent_given"):
        if text == "✅ Я согласен(на)":
            give_consent(chat_id)
            user_states.pop(chat_id, None)
            await send(chat_id, "✅ Спасибо. Согласие сохранено. Теперь доступен весь функционал.", main_kb())
            return
        if text == "❌ Не согласен(на)":
            user_states[chat_id] = {"type": "consent"}
            await send(chat_id, "Без согласия бот не сможет сохранять персональные данные и оформлять заказы.", consent_kb())
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
    if text=="/admin" and chat_id==ADMIN_ID: await send(chat_id,"👑 <b>Админ-панель</b>",admin_kb()); return
    if text in {"🔙 Главное меню","🔙 Главнoe меню"}: await send(chat_id,"Главное меню:",main_kb()); return
    if text=="🔮 Предсказание на день": await ai_processing(chat_id,"daily"); await send(chat_id,get_random_prediction(),main_kb()); return
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
    if text == "🔮 Подарить предсказание":
        user_states[chat_id] = {"type": "gift_prediction_username"}
        await send(chat_id, "🎁 <b>Подарить предсказание</b>\n\nВведите Telegram-логин получателя в формате <b>@username</b>.", back_kb())
        return
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
        if text=="📊 Статистика":
            s=get_stats(); await send(chat_id,f"📊 <b>Статистика</b>\n\nПользователей: {s['users']}\nУведомлений: {s['subscriptions']}\nТоваров: {s['products']}\nЗаказов: {s['orders']}\nНовых: {s['new_orders']}\nПодтверждённых: {s['confirmed']}\nОтклонённых: {s['rejected']}",admin_kb()); return
        if text=="📦 Заказы": await admin_orders(chat_id); return
        if text.startswith("Заказ №"):
            try: await admin_order(chat_id,int(text.split("№",1)[1].split(" ",1)[0]))
            except Exception: await send(chat_id,"Некорректный номер заказа.",admin_kb())
            return
        if text=="🔙 К списку заказов": await admin_orders(chat_id); return
        if text=="🛍 Каталог": await admin_catalog(chat_id); return
        if text=="➕ Добавить товар": user_states[chat_id]={"type":"admin_add_name"}; await send(chat_id,"Введите название товара:",back_kb()); return
        if text=="🔙 Каталог админа": await admin_catalog(chat_id); return
        for p in get_products(False):
            if text in {"🟢 "+p["name"],"⚪ "+p["name"]}: user_states[chat_id]={"type":"admin_selected_product","product_id":p["id"]}; await admin_product(chat_id,p); return
        if text=="✏️ Изменить название": return
        if text=="📝 Изменить описание": return
        if text=="💰 Изменить цену": return
        if text in {"⏸ Скрыть товар","▶️ Показать товар"}: return
        if text=="🔙 Админ-панель": await send(chat_id,"👑 <b>Админ-панель</b>",admin_kb()); return
        if text=="⭐ Отзывы":
            rows = get_recent_reviews(20)
            if not rows:
                await send(chat_id, "⭐ Отзывов пока нет.", admin_kb())
                return
            lines = ["⭐ <b>Последние отзывы</b>"]
            for r in rows:
                lines.append(f"\nЗаказ №{r[1]} · {r[3]}/5\n{esc(r[4] or 'Без текста')}")
            await send(chat_id, "\n".join(lines), admin_kb())
            return
        if text=="👥 Пользователи":
            us=get_all_users(30); await send(chat_id,"\n".join(["👥 <b>Пользователи</b>"]+[f"🆔 {u['telegram_id']} — {esc(u['name'] or 'без имени')}" for u in us]) if us else "Пользователей пока нет.",admin_kb()); return
        if text=="🔔 Уведомления": await send(chat_id,f"🔔 Активных уведомлений: <b>{len(get_active_subscriptions())}</b>",admin_kb()); return
        if text=="📣 Рассылка": user_states[chat_id]={"type":"admin_broadcast"}; await send(chat_id,"Введите текст рассылки. Для отмены /cancel",back_kb()); return
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
                    await send(
                        sub["telegram_id"],
                        "🔔 <b>Ваше предсказание на день готово!</b>\n\n✨ Загляните в чат-бот и узнайте, что вас ждёт сегодня.",
                        main_kb(),
                    )
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
    telegram_session = aiohttp.ClientSession()
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
