import asyncio, html, os, random
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import aiohttp
from aiohttp import web
from db import *
from personal import SPHERES, get_personal_forecast, get_sphere_forecast
from predictions import get_random_prediction

BOT_TOKEN=os.getenv("BOT_TOKEN")
ADMIN_ID=int(os.getenv("ADMIN_ID","0"))
PORT=int(os.getenv("PORT","10000"))
TZ=ZoneInfo("Europe/Moscow")
BASE=Path(__file__).resolve().parent
STATIC=BASE/"static"
app=web.Application()
telegram_session=None
lock_conn=None
lock_cur=None
states={}
activity={}
last_notified={}
BLACK_ANSWERS=["✅ ДА","❌ НЕТ","🎱 Скорее да","🎱 Скорее нет","🤔 Пока неясно","✨ Определённо да","🌙 Сейчас лучше не решать","🍀 Да, но не торопись","⚡ НЕТ — подожди","🔮 Ответ скрыт, попробуй позже"]

BAD_WORDS={
"бляд","бля","сука","хуй","хуе","пизд","еб","ёб","еба","ёба","ебл","мудак","дебил","идиот",
"тупиц","козёл","козел","урод","мраз","гандон","пидор","педик","шлюх","проститут"
}

def kb(rows,one=False): return {"keyboard":rows,"resize_keyboard":True,"one_time_keyboard":one,"is_persistent":False}
def main_kb(): return kb([[{"text":"🔮 Предсказание на день"},{"text":"✨ Персональное"}],[{"text":"🎯 По сферам"},{"text":"💎 Каталог"}],[{"text":"🔔 Уведомления"},{"text":"👤 Личный кабинет"}],[{"text":"🎱 Чёрный шар"},{"text":"🎁 Подарить"}]])
def back_kb(): return kb([[{"text":"🔙 Главное меню"}]])
def product_kb(): return kb([[{"text":"🛒 Заказать"}],[{"text":"🔙 К каталогу"}]])
def catalog_kb(): 
    ps=get_products(True); rows=[]
    for i in range(0,len(ps),2): rows.append([{"text":p["name"]} for p in ps[i:i+2]])
    rows.append([{"text":"🔙 Главное меню"}]); return kb(rows)
def gender_kb(): return kb([[{"text":"👨 Мужчина"},{"text":"👩 Женщина"}],[{"text":"🙂 Не хочу указывать"}]],True)
def sphere_kb(): return kb([[{"text":"❤️ Любовь"},{"text":"💼 Карьера"}],[{"text":"💰 Финансы"},{"text":"👨‍👩‍👧 Семья"}],[{"text":"🌿 Самочувствие"},{"text":"📚 Развитие"}],[{"text":"🔙 Главное меню"}]])
def account_kb(): return kb([[{"text":"📅 Мой день"},{"text":"✏️ Изменить данные"}],[{"text":"📦 Мои заказы"},{"text":"⭐ Оставить отзыв"}],[{"text":"🔙 Главное меню"}]])
def edit_kb(): return kb([[{"text":"Имя"},{"text":"Пол"}],[{"text":"Дата рождения"},{"text":"Телефон"}],[{"text":"🔙 Личный кабинет"}]])
def phone_kb(): return {"keyboard":[[{"text":"📱 Отправить мой номер","request_contact":True}],[{"text":"⌨️ Ввести вручную"}],[{"text":"❌ Отмена"}]],"resize_keyboard":True,"one_time_keyboard":True,"is_persistent":False}
def order_confirm_kb(): return kb([[{"text":"✅ Подтвердить заказ"}],[{"text":"❌ Отменить заказ"}]])
def notify_kb(active): return kb([[{"text":"🕒 Изменить время"}],[{"text":"🔕 Отписаться"}],[{"text":"🔙 Главное меню"}]]) if active else kb([[{"text":"🔔 Включить уведомления"}],[{"text":"🔙 Главное меню"}]])
def time_kb(): return kb([[{"text":t} for t in ["08:00","09:00","10:00","11:00"]],[{"text":t} for t in ["12:00","13:00","14:00","15:00"]],[{"text":t} for t in ["16:00","17:00","18:00","19:00"]],[{"text":t} for t in ["20:00","21:00","22:00","23:00"]],[{"text":"⌨️ Другое время"}],[{"text":"❌ Отмена"}]],True)
def admin_kb(): return kb([[{"text":"📊 Статистика"},{"text":"📦 Заказы"}],[{"text":"🛍 Каталог"},{"text":"⭐ Отзывы"}],[{"text":"📣 Рассылка"},{"text":"👥 Пользователи"}],[{"text":"🚪 Выйти"}]])
def admin_order_kb(): return kb([[{"text":"✅ Подтвердить"},{"text":"❌ Отклонить"}],[{"text":"🔙 К заказам"}]])

async def tg(method,payload=None,timeout=30):
    async with telegram_session.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",json=payload or {},timeout=timeout) as r:
        data=await r.json(content_type=None)
        if r.status!=200 or not data.get("ok"): raise RuntimeError(f"Telegram {method}: {r.status} {data}")
        return data["result"]
async def send(cid,text,markup=None):
    p={"chat_id":cid,"text":text,"parse_mode":"HTML"}
    if markup is not None:p["reply_markup"]=markup
    return await tg("sendMessage",p)
async def typing(cid): return await tg("sendChatAction",{"chat_id":cid,"action":"typing"})

def clean(s): return str(s or "").strip()
def valid_date(s):
    try: datetime.strptime(s,"%d.%m.%Y"); return True
    except ValueError:return False
def valid_time(s):
    try: datetime.strptime(s,"%H:%M"); return True
    except ValueError:return False
def valid_phone(s):
    d=clean(s).replace("+","").replace(" ","").replace("-","").replace("(","").replace(")",""); return d.isdigit() and 10<=len(d)<=15
def esc(s): return html.escape(str(s or ""))
def status(s): return {"new":"🆕 Новый","confirmed":"✅ Подтверждён","rejected":"❌ Отклонён","completed":"📦 Завершён"}.get(s,s)
def bad_text(s): 
    t=clean(s).lower().replace("ё","е")
    t=re.sub(r"[^а-яa-z0-9]+"," ",t) if 're' in globals() else t
    return any(w in t for w in BAD_WORDS)

# import re lazily without making message code messy
import re

async def ensure(cid): ensure_user(cid); return get_user(cid)
def flood_ok(cid):
    now=datetime.now(); a=activity.setdefault(cid,[]); a[:]=[x for x in a if now-x<timedelta(minutes=1)]
    if len(a)>=20:return False
    a.append(now);return True

async def profile_flow(cid,sphere=None):
    u=await ensure(cid)
    if not u["name"]: states[cid]={"type":"name","sphere":sphere}; await send(cid,"Как вас зовут?",back_kb()); return
    if not u["gender"]: states[cid]={"type":"gender","sphere":sphere}; await send(cid,"Укажите ваш пол:",gender_kb()); return
    if not u["birthdate"]: states[cid]={"type":"birth","sphere":sphere}; await send(cid,"Введите дату рождения в формате ДД.ММ.ГГГГ:",back_kb()); return
    await make_forecast(cid,u,sphere)

async def make_forecast(cid,u,sphere=None):
    await typing(cid); await asyncio.sleep(.4)
    txt=get_sphere_forecast(u["birthdate"],u["gender"],u["name"],sphere) if sphere else get_personal_forecast(u["birthdate"],u["gender"],u["name"])
    await send(cid,txt,main_kb())

async def show_account(cid):
    u=await ensure(cid); orders=user_orders(cid)
    await send(cid,f"👤 <b>Личный кабинет</b>\n\nИмя: {esc(u['name'] or 'не указано')}\nПол: {esc({'male':'мужчина','female':'женщина','other':'не указан'}.get(u['gender'],'не указан'))}\nДата рождения: {esc(u['birthdate'] or 'не указана')}\nТелефон: {esc(u['phone'] or 'не указан')}\nЗаказов: <b>{len(orders)}</b>",account_kb())

async def my_day(cid):
    u=await ensure(cid)
    if not (u["name"] and u["gender"] and u["birthdate"]):
        await profile_flow(cid); return
    day_score=(datetime.now(TZ).day+cid)%100
    await send(cid,f"📅 <b>Мой день</b>\n\n⚡ Энергия: <b>{50+day_score%46}%</b>\n❤️ Отношения: <b>{45+(day_score*3)%51}%</b>\n💼 Дела: <b>{50+(day_score*5)%46}%</b>\n🍀 Удача: <b>{40+(day_score*7)%61}%</b>\n\n💫 Совет: сегодня лучше выбрать одну главную задачу и довести её до результата.",main_kb())

async def handle_state(cid,text,state,msg):
    typ=state["type"]

    if typ=="selected_product":
        if text=="🛒 Заказать":
            product=get_product(state["pid"])
            if not product or not product["is_active"]:
                states.pop(cid,None)
                await send(cid,"Этот товар больше недоступен.",main_kb())
                return
            u=get_user(cid)
            if not u["name"]:
                states[cid]={"type":"order_name","pid":product["id"]}
                await send(cid,"Как вас зовут для оформления заказа?",back_kb())
                return
            if not u["phone"]:
                states[cid]={"type":"order_phone","pid":product["id"],"name":u["name"]}
                await send(cid,"Теперь укажите номер телефона.",phone_kb())
                return
            states[cid]={"type":"order_confirm","pid":product["id"],"name":u["name"],"phone":u["phone"]}
            await send(
                cid,
                f"🧾 <b>Проверьте заказ</b>\n\n"
                f"💎 {esc(product['name'])}\n"
                f"💰 {product['price_rub']:,} ₽\n"
                f"👤 {esc(u['name'])}\n"
                f"📞 {esc(u['phone'])}\n\n"
                f"Всё верно?",
                order_confirm_kb(),
            )
            return
        if text=="🔙 К каталогу":
            states.pop(cid,None)
            await send(cid,"💎 <b>Каталог Дзи</b>\n\nВыберите бусину:",catalog_kb())
            return
        states.pop(cid,None)
        await send(cid,"Используйте кнопки товара.",product_kb())
        return

    if text in {"❌ Отмена","/cancel"} or text=="🔙 Главное меню":
        states.pop(cid,None); await send(cid,"Действие отменено.",main_kb()); return
    if bad_text(text) and typ not in {"order_confirm"}:
        await send(cid,"Пожалуйста, используйте нейтральные слова.",back_kb()); return
    if len(text)>1000:
        await send(cid,"Сообщение слишком длинное.",back_kb()); return
    if typ=="name":
        update_user(cid,"name",text[:50]); states[cid]={"type":"gender","sphere":state.get("sphere")}; await send(cid,"Укажите ваш пол:",gender_kb()); return
    if typ=="gender":
        mp={"👨 Мужчина":"male","👩 Женщина":"female","🙂 Не хочу указывать":"other"}
        if text not in mp: await send(cid,"Выберите вариант кнопкой.",gender_kb()); return
        update_user(cid,"gender",mp[text]); states[cid]={"type":"birth","sphere":state.get("sphere")}; await send(cid,"Введите дату рождения в формате ДД.ММ.ГГГГ:",back_kb()); return
    if typ=="birth":
        if not valid_date(text): await send(cid,"Неверная дата. Формат ДД.ММ.ГГГГ:",back_kb()); return
        update_user(cid,"birthdate",text); states.pop(cid,None); await make_forecast(cid,get_user(cid),state.get("sphere")); return
    if typ=="notify":
        if text=="⌨️ Другое время": states[cid]={"type":"notify_custom"}; await send(cid,"Введите время ЧЧ:ММ:",back_kb()); return
        if not valid_time(text): await send(cid,"Выберите время:",time_kb()); return
        set_subscription(cid,text,True); states.pop(cid,None); await send(cid,f"🔔 Уведомления включены. Каждый день в {text} по Москве.",main_kb()); return
    if typ=="notify_custom":
        if not valid_time(text): await send(cid,"Неверное время:",back_kb()); return
        set_subscription(cid,text,True); states.pop(cid,None); await send(cid,f"🔔 Уведомления будут приходить каждый день в {text}.",main_kb()); return
    if typ=="order_name":
        states[cid]={"type":"order_phone","pid":state["pid"],"name":text[:50]}; await send(cid,"Теперь укажите номер телефона.",phone_kb()); return
    if typ=="order_phone":
        if text=="⌨️ Ввести вручную": states[cid]={"type":"order_manual","pid":state["pid"],"name":state["name"]}; await send(cid,"Введите номер телефона:",back_kb()); return
        phone=msg.get("contact",{}).get("phone_number","") if msg.get("contact") else text
        if not valid_phone(phone): await send(cid,"Неверный номер. Используйте контакт или введите номер вручную.",phone_kb()); return
        states[cid]={"type":"order_confirm","pid":state["pid"],"name":state["name"],"phone":phone}; p=get_product(state["pid"])
        await send(cid,f"🧾 <b>Проверьте заказ</b>\n\n💎 {esc(p['name'])}\n💰 {p['price_rub']:,} ₽\n👤 {esc(state['name'])}\n📞 {esc(phone)}\n\nПодтвердить?",order_confirm_kb()); return
    if typ=="order_manual":
        if not valid_phone(text): await send(cid,"Неверный номер.",back_kb()); return
        states[cid]={"type":"order_confirm","pid":state["pid"],"name":state["name"],"phone":text}; p=get_product(state["pid"])
        await send(cid,f"🧾 <b>Проверьте заказ</b>\n\n💎 {esc(p['name'])}\n💰 {p['price_rub']:,} ₽\n👤 {esc(state['name'])}\n📞 {esc(text)}\n\nПодтвердить?",order_confirm_kb()); return
    if typ=="order_confirm":
        if text!="✅ Подтвердить заказ": states.pop(cid,None); await send(cid,"Заказ отменён.",main_kb()); return
        oid=create_order(cid,state["pid"],state["name"],state["phone"]); update_user(cid,"name",state["name"]); update_user(cid,"phone",state["phone"]); p=get_product(state["pid"]); states.pop(cid,None)
        await send(cid,f"🙏 <b>Спасибо за ваш заказ!</b>\n\nЗаказ №{oid} на «{esc(p['name'])}» принят.\n\nЗаказ передан менеджеру, и скоро приступят к сборке браслета. Менеджер свяжется с вами.",main_kb())
        await send(ADMIN_ID,f"🆕 <b>Новый заказ №{oid}</b>\n\n💎 {esc(p['name'])}\n👤 {esc(state['name'])}\n📞 {esc(state['phone'])}\n🆔 {cid}",admin_kb()); return
    if typ=="ball_question":
        count=ball_state(cid)
        if count>=3: states.pop(cid,None); await send(cid,"🎱 На сегодня три вопроса уже использованы. Возвращайтесь завтра.",main_kb()); return
        increment_ball(cid); states.pop(cid,None); await send(cid,f"🎱 <b>Чёрный шар отвечает:</b>\n\n{random.choice(BLACK_ANSWERS)}",main_kb()); return
    if typ=="review_pick":
        try: oid=int(text.split("№",1)[1].split(" ",1)[0])
        except: await send(cid,"Выберите заказ кнопкой.",main_kb()); return
        states[cid]={"type":"review_rating","oid":oid}; await send(cid,"Поставьте оценку от 1 до 5:",kb([[{"text":"⭐ 1"},{"text":"⭐ 2"},{"text":"⭐ 3"},{"text":"⭐ 4"},{"text":"⭐ 5"}],[{"text":"❌ Отмена"}]],True)); return
    if typ=="review_rating":
        m=re.search(r"([1-5])",text)
        if not m: await send(cid,"Выберите оценку 1–5.",back_kb()); return
        states[cid]={"type":"review_text","oid":state["oid"],"rating":int(m.group(1))}; await send(cid,"Напишите отзыв или нажмите /skip:",back_kb()); return
    if typ=="review_text":
        add_review(cid,state["oid"],state["rating"],"" if text=="/skip" else text); states.pop(cid,None); await send(cid,"⭐ Спасибо за отзыв!",main_kb()); return
    if typ=="edit_name": update_user(cid,"name",text[:50]); states.pop(cid,None); await show_account(cid); return
    if typ=="edit_gender":
        mp={"👨 Мужчина":"male","👩 Женщина":"female","🙂 Не хочу указывать":"other"}
        if text in mp: update_user(cid,"gender",mp[text]); states.pop(cid,None); await show_account(cid)
        else: await send(cid,"Выберите вариант.",gender_kb())
        return
    if typ=="edit_birth":
        if valid_date(text): update_user(cid,"birthdate",text); states.pop(cid,None); await show_account(cid)
        else: await send(cid,"Неверная дата.",back_kb())
        return
    if typ=="edit_phone":
        if text=="⌨️ Ввести вручную": states[cid]={"type":"edit_manual_phone"}; await send(cid,"Введите номер:",back_kb()); return
        phone=msg.get("contact",{}).get("phone_number","") if msg.get("contact") else text
        if valid_phone(phone): update_user(cid,"phone",phone); states.pop(cid,None); await show_account(cid)
        else: await send(cid,"Неверный номер.",phone_kb())
        return
    if typ=="edit_manual_phone":
        if valid_phone(text): update_user(cid,"phone",text); states.pop(cid,None); await show_account(cid)
        else: await send(cid,"Неверный номер.",back_kb())
        return
    if typ=="admin_order":
        if text=="🔙 К заказам": states.pop(cid,None); await admin_orders(cid); return
        if text not in {"✅ Подтвердить","❌ Отклонить"}: await send(cid,"Выберите действие.",admin_order_kb()); return
        oid=state["oid"]; st="confirmed" if text=="✅ Подтвердить" else "rejected"; set_order_status(oid,st); o=get_order(oid); states.pop(cid,None)
        await send(o["telegram_id"],f"{'✅ Заказ подтверждён.' if st=='confirmed' else '❌ Заказ отклонён.'}\n\nСтатус заказа №{oid}: {status(st)}",main_kb()); await send(cid,f"Статус заказа №{oid} изменён.",admin_kb()); return
    if typ=="admin_edit":
        update_product(state["pid"],state["field"],text.strip()[:1000]); states.pop(cid,None); await admin_product(cid,get_product(state["pid"])); return
    if typ=="admin_add":
        step=state["step"]
        if step==1: states[cid]={"type":"admin_add","step":2,"name":text[:100],"desc":""}; await send(cid,"Введите описание:",back_kb()); return
        if step==2: state["desc"]=text[:1000]; state["step"]=3; states[cid]=state; await send(cid,"Введите цену:",back_kb()); return
        try: price=int(text.replace(" ","").replace(",",""))
        except: await send(cid,"Цена должна быть числом.",back_kb()); return
        add_product(state["name"],state["desc"],price); states.pop(cid,None); await send(cid,"✅ Товар добавлен.",admin_kb()); return
    if typ=="admin_product":
        p=get_product(state["pid"])
        if text=="🗑 Удалить товар":
            if delete_product(p["id"]): states.pop(cid,None); await send(cid,"🗑 Товар удалён.",admin_kb())
            else: await send(cid,"У товара уже есть заказы. Его можно только скрыть.",admin_kb())
            return
        if text=="⏸ Скрыть товар" or text=="▶️ Показать товар":
            set_product_active(p["id"],not p["is_active"]); await admin_product(cid,get_product(p["id"])); return
        if text in {"✏️ Название","📝 Описание","💰 Цена"}:
            field={"✏️ Название":"name","📝 Описание":"description","💰 Цена":"price_rub"}[text]
            states[cid]={"type":"admin_edit","pid":p["id"],"field":field}; await send(cid,"Введите новое значение:",back_kb()); return
        await admin_product(cid,p); return
    if typ=="admin_broadcast":
        users=get_all_users(10000); ok=bad=0
        for u in users:
            try: await send(u["telegram_id"],text,main_kb()); ok+=1
            except: bad+=1
            await asyncio.sleep(.04)
        states.pop(cid,None); await send(cid,f"📣 Рассылка завершена. Отправлено: {ok}, ошибок: {bad}.",admin_kb()); return

async def admin_orders(cid):
    os_=recent_orders(20)
    rows=[]
    for o in os_: rows.append([{"text":f"Заказ №{o['id']} — {o['product_name']}"}])
    rows.append([{"text":"🔙 Админ-панель"}])
    await send(cid,"📦 <b>Заказы</b>\n\nВыберите заказ:",kb(rows))

async def admin_product(cid,p):
    toggle="⏸ Скрыть товар" if p["is_active"] else "▶️ Показать товар"
    await send(cid,f"💎 <b>{esc(p['name'])}</b>\n\n{esc(p['description'])}\n\nЦена: <b>{p['price_rub']} ₽</b>",kb([[{"text":"✏️ Название"}],[{"text":"📝 Описание"}],[{"text":"💰 Цена"}],[{"text":toggle}],[{"text":"🗑 Удалить товар"}],[{"text":"🔙 Каталог"}]]))

async def process(update):
    if "message" not in update:return
    m=update["message"]; cid=int(m["chat"]["id"]); text=clean(m.get("text"))
    if not flood_ok(cid): await send(cid,"Слишком много сообщений. Подождите минуту."); return
    ensure_user(cid)
    if bad_text(text): await send(cid,"Пожалуйста, используйте нейтральные слова.",main_kb()); return
    state=states.get(cid)
    if state: await handle_state(cid,text,state,m); return
    if text=="/start": await send(cid,"🔮 <b>Добро пожаловать!</b>\n\nВыберите функцию:",main_kb()); return
    if text=="🔙 Главное меню": await send(cid,"Главное меню:",main_kb()); return
    if text=="🔮 Предсказание на день": await send(cid,get_random_prediction(),main_kb()); return
    if text=="✨ Персональное": await profile_flow(cid); return
    if text=="🎯 По сферам": await send(cid,"Выберите сферу:",sphere_kb()); return
    sm={"❤️ Любовь":"love","💼 Карьера":"career","💰 Финансы":"finance","👨‍👩‍👧 Семья":"family","🌿 Самочувствие":"health","📚 Развитие":"growth"}
    if text in sm: await profile_flow(cid,sm[text]); return
    if text=="💎 Каталог": await send(cid,"💎 <b>Каталог Дзи</b>\n\nВыберите бусину:",catalog_kb()); return
    if text=="🔙 К каталогу": await send(cid,"💎 <b>Каталог Дзи</b>\n\nВыберите бусину:",catalog_kb()); return
    p=get_product_by_name(text)
    if p and p["is_active"]: states[cid]={"type":"selected_product","pid":p["id"]}; await send(cid,f"💎 <b>{esc(p['name'])}</b>\n\n{esc(p['description'])}\n\n💰 <b>{p['price_rub']} ₽</b>",product_kb()); return
    if text=="🛒 Заказать":
        st=states.get(cid)
        if not st or st["type"]!="selected_product": await send(cid,"Сначала выберите товар.",catalog_kb()); return
        p=get_product(st["pid"]); u=get_user(cid)
        if not u["name"]: states[cid]={"type":"order_name","pid":p["id"]}; await send(cid,"Как вас зовут?",back_kb()); return
        if not u["phone"]: states[cid]={"type":"order_phone","pid":p["id"],"name":u["name"]}; await send(cid,"Укажите номер телефона:",phone_kb()); return
        states[cid]={"type":"order_confirm","pid":p["id"],"name":u["name"],"phone":u["phone"]}; await send(cid,f"Заказ на <b>{esc(p['name'])}</b> за {p['price_rub']} ₽.\n\nПодтвердить?",order_confirm_kb()); return
    if text=="🔔 Уведомления":
        sub=get_subscription(cid); await send(cid, f"🔔 <b>Уведомления</b>\n\n"+(f"Включены. Время: {sub['time']} по Москве." if sub and sub["active"] else "Уведомления выключены.\nЯ могу каждый день сообщать, что ваше предсказание на день готово."),notify_kb(bool(sub and sub["active"]))); return
    if text in {"🔔 Включить уведомления","🕒 Изменить время"}: states[cid]={"type":"notify"}; await send(cid,"Выберите время:",time_kb()); return
    if text=="🔕 Отписаться": set_subscription(cid,"00:00",False); await send(cid,"🔕 Уведомления отключены.",main_kb()); return
    if text=="👤 Личный кабинет": await show_account(cid); return
    if text=="📅 Мой день": await my_day(cid); return
    if text=="✏️ Изменить данные": await send(cid,"Что изменить?",edit_kb()); return
    if text=="🔙 Личный кабинет": await show_account(cid); return
    if text=="Имя": states[cid]={"type":"edit_name"}; await send(cid,"Введите новое имя:",back_kb()); return
    if text=="Пол": states[cid]={"type":"edit_gender"}; await send(cid,"Выберите пол:",gender_kb()); return
    if text=="Дата рождения": states[cid]={"type":"edit_birth"}; await send(cid,"Введите дату:",back_kb()); return
    if text=="Телефон": states[cid]={"type":"edit_phone"}; await send(cid,"Укажите телефон:",phone_kb()); return
    if text=="📦 Мои заказы":
        os_=user_orders(cid)
        if not os_: await send(cid,"📦 Заказов пока нет.",account_kb()); return
        s=["📦 <b>Мои заказы</b>"]+[f"№{o['id']} · {esc(o['product_name'])} · {status(o['status'])}" for o in os_]
        await send(cid,"\n".join(s),account_kb()); return
    if text=="⭐ Оставить отзыв":
        r=get_reviewable_orders(cid)
        if not r: await send(cid,"Пока нет завершённых заказов, для которых можно оставить отзыв.",account_kb()); return
        rows=[[{"text":f"Заказ №{x['id']} — {x['product_name']}"}] for x in r]
        rows.append([{"text":"🔙 Личный кабинет"}]); states[cid]={"type":"review_pick"}; await send(cid,"Выберите заказ:",kb(rows)); return
    if text=="🎱 Чёрный шар":
        if ball_state(cid)>=3: await send(cid,"🎱 Сегодня вы уже задали 3 вопроса. Возвращайтесь завтра.",main_kb()); return
        states[cid]={"type":"ball_question"}; await send(cid,f"🎱 <b>Чёрный шар</b>\n\nЗадайте вопрос, на который можно ответить ДА или НЕТ.\nСегодня доступно: {3-ball_state(cid)} вопрос(а).",back_kb()); return
    if text=="🎁 Подарить":
        await send(cid,"🎁 <b>Подарок</b>\n\nВыберите, что хотите отправить другу:",kb([[{"text":"🔮 Подарить предсказание"}],[{"text":"💎 Подарить браслет"}],[{"text":"🔙 Главное меню"}]])); return
    if text=="🔮 Подарить предсказание":
        await send(cid,"🎁 Предложение для друга:\n\n"+get_random_prediction()+"\n\nМожете переслать это сообщение другу.",main_kb()); return
    if text=="💎 Подарить браслет":
        await send(cid,"🎁 <b>Идея для подарка</b>\n\nВыберите браслет в каталоге и оформите заказ. После создания заказа можно переслать другу название и описание понравившейся бусины.",main_kb()); return

    if cid==ADMIN_ID:
        if text=="/admin": await send(cid,"👑 <b>Админ-панель</b>",admin_kb()); return
        if text=="📦 Заказы": await admin_orders(cid); return
        if text.startswith("Заказ №"):
            oid=int(text.split("№",1)[1].split(" ",1)[0]); o=get_order(oid)
            if o: states[cid]={"type":"admin_order","oid":oid}; await send(cid,f"📦 <b>Заказ №{oid}</b>\n\n💎 {esc(o['product_name'])}\n👤 {esc(o['customer_name'])}\n📞 {esc(o['customer_phone'])}\n📌 {status(o['status'])}",admin_order_kb())
            return
        if text=="✅ Подтвердить" or text=="❌ Отклонить": await send(cid,"Сначала выберите заказ.",admin_kb()); return
        if text=="🔙 К заказам": await admin_orders(cid); return
        if text=="🛍 Каталог":
            ps=get_products(False); rows=[[{"text":("🟢 " if p["is_active"] else "⚪ ")+p["name"]}] for p in ps]+[[{"text":"➕ Добавить товар"}],[{"text":"🔙 Админ-панель"}]]
            await send(cid,"🛍 <b>Каталог</b>",kb(rows)); return
        if text.startswith("🟢 ") or text.startswith("⚪ "):
            name=text[2:]; p=get_product_by_name(name)
            if p: states[cid]={"type":"admin_product","pid":p["id"]}; await admin_product(cid,p)
            return
        if text=="➕ Добавить товар": states[cid]={"type":"admin_add","step":1,"name":"","desc":""}; await send(cid,"Введите название товара:",back_kb()); return
        if text=="⭐ Отзывы":
            rs=get_reviews(20)
            if not rs: await send(cid,"Отзывов пока нет.",admin_kb()); return
            txt=["⭐ <b>Последние отзывы</b>"]+[f"\nЗаказ №{r['order_id']} · {r['rating']}/5\n{esc(r['text'])}" for r in rs]
            await send(cid,"\n".join(txt),admin_kb()); return
        if text=="📊 Статистика":
            await send(cid,"📊 Статистика доступна в текущей версии в базовом виде через раздел заказов и отзывов. Расширенный дашборд пока не добавлен.",admin_kb()); return
        if text=="👥 Пользователи": await send(cid,f"👥 Пользователей: {len(get_all_users(10000))}",admin_kb()); return
        if text=="📣 Рассылка": states[cid]={"type":"admin_broadcast"}; await send(cid,"Введите текст рассылки:",back_kb()); return
        if text=="🚪 Выйти": await send(cid,"Вы вышли из админ-панели.",main_kb()); return

    await send(cid,"Используйте кнопки меню.",main_kb())

async def polling():
    off=0
    await tg("deleteWebhook",{"drop_pending_updates":False})
    print("Telegram polling is running.")
    while True:
        try:
            for u in await tg("getUpdates",{"offset":off,"timeout":50,"allowed_updates":["message"]},65):
                off=u["update_id"]+1
                try: await process(u)
                except Exception as exc: print("Update error:",exc)
        except asyncio.CancelledError: raise
        except Exception as exc: print("Polling error:",exc); await asyncio.sleep(4)

async def scheduler():
    while True:
        try:
            now=datetime.now(TZ); hhmm=now.strftime("%H:%M"); keyday=now.strftime("%Y-%m-%d")
            for uid in due_subscriptions(hhmm):
                key=(uid,keyday,hhmm)
                if last_notified.get(uid)==key: continue
                try:
                    await send(uid,"🔔 <b>Ваше предсказание на день готово</b>\n\nУзнайте, что вас ждёт.\n\n"+get_random_prediction(),main_kb())
                    last_notified[uid]=key
                except Exception as exc: print("Notify error:",uid,exc)
        except asyncio.CancelledError: raise
        except Exception as exc: print("Scheduler error:",exc)
        await asyncio.sleep(20)

async def leader(app_):
    global lock_conn,lock_cur
    while True:
        try:
            lock_conn,lock_cur=acquire_bot_lock()
            print("Telegram leader lock acquired.")
            app_["polling"]=asyncio.create_task(polling())
            app_["scheduler"]=asyncio.create_task(scheduler())
            await asyncio.gather(app_["polling"],app_["scheduler"])
        except asyncio.CancelledError: raise
        except Exception as exc: print("Leader waiting:",exc)
        finally:
            if lock_cur:
                try: lock_cur.close()
                except: pass
                lock_cur=None
            if lock_conn:
                try: lock_conn.close()
                except: pass
                lock_conn=None
        await asyncio.sleep(5)

async def startup(app_):
    global telegram_session
    init_db()
    telegram_session=aiohttp.ClientSession()
    app_["leader"]=asyncio.create_task(leader(app_))

async def cleanup(app_):
    if app_.get("leader"): app_["leader"].cancel()
    if app_.get("leader"):
        try: await app_["leader"]
        except asyncio.CancelledError: pass
        except Exception: pass
    if telegram_session: await telegram_session.close()

async def api_personal(request):
    from personal import get_personal_forecast, get_sphere_forecast
    birth = request.query.get("birthdate","").strip()
    sphere = request.query.get("sphere","general").strip()
    if not valid_date(birth):
        return web.json_response({"message":"Укажите дату в формате ДД.ММ.ГГГГ"},status=400)
    try:
        # Web version has no stored gender/name; use neutral demo values.
        text = get_personal_forecast(birth,"other","Друг") if sphere=="general" else get_sphere_forecast(birth,"other","Друг",sphere)
        return web.json_response({"prediction":text})
    except Exception as exc:
        return web.json_response({"message":"Не удалось сформировать прогноз"},status=500)

app.router.add_get("/health",lambda r:web.json_response({"status":"ok"}))
app.router.add_get("/api/personal",api_personal)
app.router.add_static("/static",path=str(STATIC),name="static")
app.router.add_get("/",lambda r:web.FileResponse(STATIC/"index.html"))
app.on_startup.append(startup); app.on_cleanup.append(cleanup)

if __name__=="__main__":
    web.run_app(app,host="0.0.0.0",port=PORT)
