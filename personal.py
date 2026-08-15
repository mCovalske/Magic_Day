from datetime import date, datetime

SPHERES = {
    "love": "❤️ Любовь и отношения",
    "career": "💼 Карьера и работа",
    "finance": "💰 Финансы",
    "family": "👨‍👩‍👧 Семья и дом",
    "health": "🌿 Самочувствие",
    "growth": "📚 Личное развитие",
}

ZODIAC = [(1,20,"Водолей"),(2,19,"Рыбы"),(3,21,"Овен"),(4,20,"Телец"),(5,21,"Близнецы"),(6,21,"Рак"),(7,23,"Лев"),(8,23,"Дева"),(9,23,"Весы"),(10,23,"Скорпион"),(11,22,"Стрелец"),(12,22,"Козерог")]
ANIMALS = ["Крыса","Бык","Тигр","Кролик","Дракон","Змея","Лошадь","Коза","Обезьяна","Петух","Собака","Свинья"]

TEXTS = {
"love": "Сегодня важно выбирать искренность и спокойный разговор. Небольшой знак внимания может заметно улучшить атмосферу.",
"career": "Лучше сосредоточиться на одной главной задаче. Последовательность сегодня полезнее спешки.",
"finance": "Подходящий день для спокойного планирования. Перед расходом или решением полезно сравнить варианты.",
"family": "Сегодня стоит уделить близким немного больше внимания. Тёплый разговор может снять ненужное напряжение.",
"health": "Выбирайте умеренный темп, полноценный отдых и нормальный сон. Это прогноз для развлечения, а не медицинский совет.",
"growth": "Хороший день для небольшого обучения или полезной привычки. Маленький шаг сегодня даст основу для результата позже.",
}


def parse_date(value):
    return datetime.strptime(value, "%d.%m.%Y").date()


def zodiac_for(b):
    for month, day, name in ZODIAC:
        next_month = 1 if month == 12 else month + 1
        if (b.month == month and b.day >= day) or (b.month == next_month and b.day < day):
            return name
    return "Козерог"


def animal_for(year):
    return ANIMALS[(year - 4) % 12]


def get_personal_forecast(birthdate, gender, name):
    try:
        birth = parse_date(birthdate)
    except ValueError:
        return "Некорректная дата. Используйте формат ДД.ММ.ГГГГ."
    today = date.today()
    zodiac = zodiac_for(birth)
    animal = animal_for(birth.year)
    seed = (today.toordinal() + birth.toordinal() + len(name)) % 6
    mood = ["двигаться вперёд", "не спешить с решением", "завершить начатое", "заметить новую возможность", "уделить время важному разговору", "позволить себе небольшую перемену"][seed]
    pronoun = "для вас"
    return (
        f"✨ <b>Персональное предсказание на {today:%d.%m.%Y}</b>\n\n"
        f"👤 <b>{name}</b>\n♈ Знак: <b>{zodiac}</b>\n🐉 Восточный знак: <b>{animal}</b>\n\n"
        f"Сегодня хороший ориентир {pronoun} — <b>{mood}</b>.\n\n"
        + "\n\n".join(f"{SPHERES[k]}: {TEXTS[k]}" for k in ["love","career","finance"]) +
        f"\n\n💫 <b>Совет дня:</b> не пытайтесь успеть всё сразу. Выберите главное."
    )


def get_sphere_forecast(birthdate, gender, name, sphere_key):
    try:
        birth = parse_date(birthdate)
    except ValueError:
        return "Некорректная дата. Используйте формат ДД.ММ.ГГГГ."
    sphere_key = sphere_key if sphere_key in SPHERES else "growth"
    today = date.today()
    return (
        f"✨ <b>{SPHERES[sphere_key]}</b>\n"
        f"На {today:%d.%m.%Y}\n\n"
        f"👤 <b>{name}</b>\n"
        f"♈ Знак: <b>{zodiac_for(birth)}</b>\n"
        f"🐉 Восточный знак: <b>{animal_for(birth.year)}</b>\n\n"
        f"{TEXTS[sphere_key]}\n\n"
        f"💫 <b>Совет:</b> выберите одно конкретное действие по этой сфере и сделайте его сегодня."
    )
