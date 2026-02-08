import os, time, re, requests, hashlib, random
from collections import defaultdict, OrderedDict

XHR_URL = "https://srkvg.ru/extore/frontend/themes/vorob/ajax.php"
CHECK_EVERY = 20
MAX_DAYS = 14

# ========== ДАННЫЕ ПО УМОЛЧАНИЮ (для кнопки "Быстро") ==========
DEFAULT_NAME = "Bekhruz Rasamatov"
DEFAULT_EMAIL = "behruzrasamatovmoskva@gmail.com"
DEFAULT_PHONE = "7 (901) 906-94-05"
DEFAULT_AGE = 27
DEFAULT_GENDER = "Мужской"
DEFAULT_SIZE = "undefined"

# Настройки мониторинга
TARGET_DATES = ["2026-02-06", "2026-02-07", "2026-02-08", "2026-02-09", "2026-02-10"]

# Настройки автобронирования
AUTO_BOOK_ACTIVITIES = ["Сноуборд", "Горные лыжи"]
FORM_ACTIVITIES = ["Кёрлинг"]
# =========================================

TRACK = [
  {"name":"Кёрлинг", "page":"https://srkvg.ru/moskva-kataet/kerling/", "form":{"grajax":"1","item":"Керлинг"}},
  {"name":"Сноуборд", "page":"https://srkvg.ru/moskva-kataet/trenirovki-s-instruktorom-po-snoubordu/", "form":{"grajax":"1","item":"Тренировки (сноуборд)"}},
  {"name":"Горные лыжи", "page":"https://srkvg.ru/moskva-kataet/trenirovki-s-instruktorom-po-gornym-lyzham/", "form":{"grajax":"1","item":"Тренировки (горные лыжи)"}},
]

BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")

session = requests.Session()
session.headers.update({"User-Agent":"Mozilla/5.0","Origin":"https://srkvg.ru"})

# Хранилище для каждого пользователя
user_data = {}
last_update_id = 0
global_state = {}
bot_start_time = time.time()

def get_user_data(chat_id):
  """Получить данные пользователя"""
  if chat_id not in user_data:
    user_data[chat_id] = {
      "pending_slots": {},
      "waiting_form": {},
      "booked": set(),
      "notified": set(),
      "paused": False,
      "default_data": {
        "name": DEFAULT_NAME,
        "email": DEFAULT_EMAIL,
        "phone": DEFAULT_PHONE,
        "age": DEFAULT_AGE,
        "gender": DEFAULT_GENDER
      }
    }
  return user_data[chat_id]

def tg_send(chat_id: int, text: str, reply_markup=None):
  if not BOT_TOKEN:
    print(f"[TG {chat_id}] {text}")
    return
  api = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
  try:
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if reply_markup:
      payload["reply_markup"] = reply_markup
    r = session.post(api, json=payload, timeout=20)
    r.raise_for_status()
    return r.json().get("result", {}).get("message_id")
  except Exception as e:
    print(f"[TG ERROR {chat_id}] {e}")
    return None

def tg_send_with_buttons(chat_id: int, text: str, buttons: list):
  if not BOT_TOKEN:
    print(f"[TG {chat_id}] {text}")
    return

  keyboard = {"inline_keyboard": [[{"text": btn[0], "callback_data": btn[1]}] for btn in buttons]}
  return tg_send(chat_id, text, reply_markup=keyboard)

def tg_get_updates():
  global last_update_id
  if not BOT_TOKEN:
    return []
  api = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
  try:
    r = session.post(api, json={"offset": last_update_id + 1, "timeout": 0}, timeout=5)
    r.raise_for_status()
    data = r.json()
    if data.get("ok") and data.get("result"):
      updates = data["result"]
      if updates:
        last_update_id = updates[-1]["update_id"]
      return updates
    return []
  except Exception as e:
    return []

def tg_answer_callback(callback_id: str, text: str):
  if not BOT_TOKEN:
    return
  api = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
  try:
    r = session.post(api, json={"callback_query_id": callback_id, "text": text}, timeout=10)
    r.raise_for_status()
  except:
    pass

ts_re = re.compile(r'data-timestamp="(\d{4}-\d{2}-\d{2})\s+([^"]+)"')
svob_re = re.compile(r'class="svob">\s*(\d+)\s*<')

def parse_all_slots(html: str):
  out = []
  for m in re.finditer(r'data-timestamp="(\d{4}-\d{2}-\d{2})\s+([^"]+)"', html):
    d, t = m.group(1), m.group(2).strip()
    start = max(0, m.start() - 500)
    chunk = html[start:m.start()]
    sv = svob_re.findall(chunk)
    if not sv:
      continue
    n = int(sv[-1])
    out.append((d, t, n))
  out.sort()
  return out

def digest_days(daymap):
  s="|".join([f"{d}:{daymap[d]['total']}:" + ",".join([f"{t}={n}" for t,n in daymap[d]["slots_all"]]) for d in daymap.keys()])
  return hashlib.sha256(s.encode("utf-8")).hexdigest()

def summarize_days(all_slots, max_days=MAX_DAYS):
  daymap = OrderedDict()
  for d,t,n in all_slots:
    if d not in daymap:
      daymap[d] = {"total": 0, "slots_pos": [], "slots_all": []}
    daymap[d]["total"] += n
    daymap[d]["slots_all"].append((t, n))
    if n > 0:
      daymap[d]["slots_pos"].append((t, n))
  dates = list(daymap.keys())[:max_days]
  return OrderedDict((d, daymap[d]) for d in dates)

def format_daymap(daymap):
  if not daymap:
    return "Нет данных."
  lines=[]
  for d, info in daymap.items():
    if info["total"] <= 0:
      lines.append(f"{d}: 0 мест")
    else:
      slots_txt = ", ".join([f"{t}({n})" for t,n in info["slots_pos"]])
      lines.append(f"{d}: {info['total']} мест — {slots_txt}")
  return "\n".join(lines)

def get_current_status(chat_id):
  """Получить текущий статус для пользователя"""
  udata = get_user_data(chat_id)
  uptime = int(time.time() - bot_start_time)
  hours = uptime // 3600
  minutes = (uptime % 3600) // 60

  status_lines = [
    f"📊 ВАШ СТАТУС",
    f"",
    f"⏱️ Бот работает: {hours}ч {minutes}мин",
    f"🔄 Проверка каждые: {CHECK_EVERY} сек",
    f"⏸️ Ваш статус: {'ПРИОСТАНОВЛЕН' if udata['paused'] else 'АКТИВЕН'}",
    f"",
    f"📅 Отслеживаемые даты:",
    f"{', '.join(TARGET_DATES[:3])}...",
    f"",
    f"✅ Вы забронировали: {len(udata['booked'])} слотов",
    f"⏳ Ожидают выбора: {len(udata['pending_slots'])}",
    f"📝 Заполнение формы: {len(udata['waiting_form'])}",
    f"",
    f"👥 Всего пользователей бота: {len(user_data)}"
  ]

  return "\n".join(status_lines)

def get_current_slots():
  """Получить актуальное расписание"""
  result = ["📋 АКТУАЛЬНОЕ РАСПИСАНИЕ\n"]

  for cfg in TRACK:
    try:
      session.headers["Referer"] = cfg["page"]
      r = session.post(XHR_URL, data=cfg["form"], timeout=20)
      r.raise_for_status()

      all_slots = parse_all_slots(r.text)
      daymap = summarize_days(all_slots, 7)

      emoji = "🥌" if "Кёрлинг" in cfg['name'] else "🏂" if "Сноуборд" in cfg['name'] else "⛷️"
      result.append(f"{emoji} {cfg['name']}:")

      has_slots = False
      for date, info in daymap.items():
        if date not in TARGET_DATES:
          continue
        if info["total"] > 0:
          slots_txt = ", ".join([f"{t}({n})" for t,n in info["slots_pos"]])
          result.append(f"  {date}: {slots_txt}")
          has_slots = True

      if not has_slots:
        result.append(f"  Нет свободных мест")
      result.append("")
    except Exception as e:
      result.append(f"{cfg['name']}: ошибка загрузки\n")

  return "\n".join(result)

def book_slot(chat_id, cfg, date, time, booking_data=None, auto=False):
  udata = get_user_data(chat_id)
  slot_key = f"{cfg['name']}:{date}:{time}"

  if slot_key in udata["booked"]:
    return False

  mode_text = "АВТОБРОНИРОВАНИЕ" if auto else "БРОНИРОВАНИЕ"
  print(f"\n🎯 [{chat_id}] {mode_text}: {cfg['name']} - {date} {time}")

  if auto:
    tg_send(chat_id, f"⚡ АВТОБРОНИРОВАНИЕ\n{cfg['name']}\n📅 {date}\n🕐 {time}")

  booking_hash = str(random.random())

  if booking_data:
    name = booking_data.get("name", udata["default_data"]["name"])
    email = booking_data.get("email", udata["default_data"]["email"])
    phone = booking_data.get("phone", udata["default_data"]["phone"])
    age = booking_data.get("age", udata["default_data"]["age"])
    gender = booking_data.get("gender", udata["default_data"]["gender"])
  else:
    name = udata["default_data"]["name"]
    email = udata["default_data"]["email"]
    phone = udata["default_data"]["phone"]
    age = udata["default_data"]["age"]
    gender = udata["default_data"]["gender"]

  payload = {
    "tickets": "1",
    "item": cfg["form"]["item"],
    "date": date,
    "time": time,
    "mail": email,
    "phone": phone,
    "name": name,
    "age": str(age),
    "gender": gender,
    "size": DEFAULT_SIZE,
    "hash": booking_hash
  }

  try:
    session.headers["Referer"] = cfg["page"]
    r = session.post(XHR_URL, data=payload, timeout=20)
    r.raise_for_status()

    response_text = r.text.lower()

    if "успешно" in response_text or "success" in response_text or "спасибо" in response_text or "билет" in response_text:
      udata["booked"].add(slot_key)
      emoji = "🥌" if "Кёрлинг" in cfg['name'] else "🏂" if "Сноуборд" in cfg['name'] else "⛷️"
      msg = f"✅ УСПЕХ! Забронировано:\n\n{emoji} {cfg['name']}\n📅 {date}\n🕐 {time}\n👤 {name}\n✉️ {email}\n\nБилет отправлен на почту!"
      print(msg)
      tg_send(chat_id, msg)
      return True
    else:
      msg = f"❌ Не удалось забронировать\n{cfg['name']}\n{date} {time}"
      tg_send(chat_id, msg)
      return False

  except Exception as e:
    msg = f"❌ Ошибка: {repr(e)[:100]}"
    tg_send(chat_id, msg)
    return False

def check_one(cfg):
  """Проверить расписание и уведомить всех активных пользователей"""
  global global_state

  session.headers["Referer"]=cfg["page"]
  r=session.post(XHR_URL, data=cfg["form"], timeout=20)
  r.raise_for_status()

  all_slots = parse_all_slots(r.text)
  daymap = summarize_days(all_slots, MAX_DAYS)

  any_now = any(daymap[d]["total"] > 0 for d in daymap)
  d = digest_days(daymap)

  st = global_state.get(cfg["name"])
  if st is None:
    global_state[cfg["name"]]={"prev_any":any_now,"prev_digest":d,"warmed_up":False}
    return

  if not st["warmed_up"]:
    st["prev_any"]=any_now
    st["prev_digest"]=d
    st["warmed_up"]=True
    return

  is_auto = cfg['name'] in AUTO_BOOK_ACTIVITIES
  is_form = cfg['name'] in FORM_ACTIVITIES

  if any_now:
    for date, info in daymap.items():
      if TARGET_DATES and date not in TARGET_DATES:
        continue

      if info["slots_pos"]:
        for slot_time, slot_count in info["slots_pos"]:
          slot_key = f"{cfg['name']}:{date}:{slot_time}"

          # Уведомляем каждого пользователя
          for chat_id, udata in user_data.items():
            if udata["paused"]:
              continue

            if slot_key in udata["notified"] or slot_key in udata["booked"]:
              continue

            udata["notified"].add(slot_key)

            if is_auto:
              print(f"⚡ [{chat_id}] Автобронирование: {date} {slot_time}")
              book_slot(chat_id, cfg, date, slot_time, auto=True)

            elif is_form:
              udata["pending_slots"][slot_key] = (cfg, date, slot_time, slot_count)

              emoji = "🥌"
              msg = f"{emoji} {cfg['name']} — найден слот!\n\n📅 Дата: {date}\n🕐 Время: {slot_time}\n👥 Свободно: {slot_count} мест\n\nДля бронирования:"
              buttons = [
                (f"📝 Заполнить форму", f"form:{slot_key}"),
                (f"⚡ Быстро (только для Бехруза)", f"quick:{slot_key}"),
                ("❌ Пропустить", f"skip:{slot_key}")
              ]
              tg_send_with_buttons(chat_id, msg, buttons)
              print(f"📨 [{chat_id}] Уведомление: {date} {slot_time}")

  if d != st["prev_digest"]:
    print(f"🔄 [{cfg['name']}] обновление расписания")
    st["prev_digest"]=d

  st["prev_any"]=any_now

def handle_command(chat_id, text):
  """Обработка команд"""
  udata = get_user_data(chat_id)
  text = text.lower().strip()

  if text in ["/start", "/help", "помощь"]:
    help_text = """🤖 ДОБРО ПОЖАЛОВАТЬ!

Бот для бронирования на Воробьёвых горах

⚡ АВТОМАТИЧЕСКИ бронируется:
🏂 Сноуборд
⛷️ Горные лыжи

📝 С ФОРМОЙ (вы выбираете):
🥌 Кёрлинг

📊 /status - ваш статус
📋 /slots - актуальное расписание
🔄 /refresh - обновить данные

⏸️ /pause - приостановить уведомления
▶️ /resume - возобновить

❌ /cancel - отменить форму
💬 /help - эта справка"""
    tg_send(chat_id, help_text)
    return True

  elif text in ["/status", "статус"]:
    status = get_current_status(chat_id)
    tg_send(chat_id, status)
    return True

  elif text in ["/slots", "слоты", "расписание"]:
    tg_send(chat_id, "⏳ Загружаю расписание...")
    slots = get_current_slots()
    tg_send(chat_id, slots)
    return True

  elif text in ["/refresh", "обновить"]:
    tg_send(chat_id, "🔄 Обновляю...")
    slots = get_current_slots()
    tg_send(chat_id, slots)
    return True

  elif text in ["/pause", "пауза"]:
    udata["paused"] = True
    tg_send(chat_id, "⏸️ Уведомления приостановлены\n/resume для возобновления")
    return True

  elif text in ["/resume", "запуск"]:
    udata["paused"] = False
    tg_send(chat_id, "▶️ Уведомления возобновлены")
    return True

  elif text in ["/cancel", "отмена"]:
    if udata["waiting_form"]:
      udata["waiting_form"].clear()
      tg_send(chat_id, "❌ Форма отменена")
    else:
      tg_send(chat_id, "ℹ️ Нет активных форм")
    return True

  return False

def process_telegram_updates():
  updates = tg_get_updates()

  for update in updates:
    # Текстовые сообщения
    if "message" in update and "text" in update["message"]:
      chat_id = update["message"]["chat"]["id"]
      text = update["message"]["text"].strip()

      # Регистрируем нового пользователя
      udata = get_user_data(chat_id)

      # Команды
      if handle_command(chat_id, text):
        continue

      # Заполнение формы
      active_form = None
      for slot_key, form_data in list(udata["waiting_form"].items()):
        active_form = slot_key
        break

      if active_form:
        if text.lower() in ["отмена", "cancel"]:
          del udata["waiting_form"][active_form]
          tg_send(chat_id, "❌ Форма отменена")
          continue

        if text.lower() in ["назад", "back"]:
          current_step = udata["waiting_form"][active_form]["waiting_for"]
          steps = ["name", "email", "phone", "age", "gender"]
          current_idx = steps.index(current_step)
          if current_idx > 0:
            udata["waiting_form"][active_form]["waiting_for"] = steps[current_idx - 1]
            prompts = {"name":"👤 ФИО:", "email":"📧 Email:", "phone":"📱 Телефон:", "age":"🎂 Возраст:", "gender":"👤 Пол:"}
            tg_send(chat_id, f"⬅️ {prompts[steps[current_idx - 1]]}")
          continue

        form_data = udata["waiting_form"][active_form]

        if form_data["waiting_for"] == "name":
          form_data["data"]["name"] = text
          form_data["waiting_for"] = "email"
          tg_send(chat_id, f"✅ Имя: {text}\n\n📧 Email:")

        elif form_data["waiting_for"] == "email":
          form_data["data"]["email"] = text
          form_data["waiting_for"] = "phone"
          tg_send(chat_id, f"✅ Email: {text}\n\n📱 Телефон:")

        elif form_data["waiting_for"] == "phone":
          form_data["data"]["phone"] = text
          form_data["waiting_for"] = "age"
          tg_send(chat_id, f"✅ Телефон: {text}\n\n🎂 Возраст:")

        elif form_data["waiting_for"] == "age":
          try:
            age = int(text)
            form_data["data"]["age"] = age
            form_data["waiting_for"] = "gender"
            tg_send(chat_id, f"✅ Возраст: {age}\n\n👤 Пол (Мужской/Женский):")
          except:
            tg_send(chat_id, "⚠️ Введите число")

        elif form_data["waiting_for"] == "gender":
          form_data["data"]["gender"] = text

          cfg = form_data["cfg"]
          date = form_data["date"]
          time = form_data["time"]

          tg_send(chat_id, f"✅ Пол: {text}\n\n⏳ Бронирую...")

          success = book_slot(chat_id, cfg, date, time, booking_data=form_data["data"])

          del udata["waiting_form"][active_form]
          if success and active_form in udata["pending_slots"]:
            del udata["pending_slots"][active_form]

        continue

    # Кнопки
    if "callback_query" not in update:
      continue

    callback = update["callback_query"]
    callback_id = callback["id"]
    chat_id = callback["message"]["chat"]["id"]
    data = callback.get("data", "")

    udata = get_user_data(chat_id)

    if data.startswith("form:"):
      slot_key = data.replace("form:", "")

      if slot_key not in udata["pending_slots"]:
        tg_answer_callback(callback_id, "⚠️ Устарел")
        continue

      cfg, date, time, available = udata["pending_slots"][slot_key]
      tg_answer_callback(callback_id, "📝 Заполните")

      udata["waiting_form"][slot_key] = {
        "cfg": cfg,
        "date": date,
        "time": time,
        "waiting_for": "name",
        "data": {}
      }

      tg_send(chat_id, f"📝 ФОРМА\n\n🥌 {cfg['name']}\n📅 {date}\n🕐 {time}\n\n👤 Введите ФИО:")

    elif data.startswith("quick:"):
      slot_key = data.replace("quick:", "")

      if slot_key not in udata["pending_slots"]:
        tg_answer_callback(callback_id, "⚠️ Устарел")
        continue

      cfg, date, time, available = udata["pending_slots"][slot_key]
      tg_answer_callback(callback_id, "⏳ Бронирую...")

      success = book_slot(chat_id, cfg, date, time)

      if success and slot_key in udata["pending_slots"]:
        del udata["pending_slots"][slot_key]

    elif data.startswith("skip:"):
      slot_key = data.replace("skip:", "")
      tg_answer_callback(callback_id, "Пропущено")

      if slot_key in udata["pending_slots"]:
        del udata["pending_slots"][slot_key]

print("🤖 Запуск ПУБЛИЧНОГО бота...")
print(f"⏱️  Проверка каждые {CHECK_EVERY} сек")
print(f"🌍 Режим: ПУБЛИЧНЫЙ (работает для всех)")
print(f"\n⚡ АВТОБРОНИРОВАНИЕ: Сноуборд, Горные лыжи")
print(f"📝 С ФОРМОЙ: Кёрлинг")
print(f"\n⚡ Кнопка \"Быстро\" использует данные Бехруза по умолчанию\n")

while True:
  try:
    process_telegram_updates()
  except Exception as e:
    print(f"⚠️ Ошибка Telegram: {repr(e)}")

  try:
    for cfg in TRACK:
      check_one(cfg)
  except Exception as e:
    print(f"❌ Ошибка проверки: {repr(e)}")

  time.sleep(CHECK_EVERY)
