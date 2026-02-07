#-------------------------------------------------------------------------------------------------------------------ИМПОРТЫ
import asyncio
import logging
import re
import os
import random
import json
import sqlite3
import pytz
from aiogram.utils.text_decorations import html_decoration as hd
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.types import LinkPreviewOptions, FSInputFile
from datetime import datetime, timedelta
from aiogram.filters import CommandObject, Command
from aiogram.types import ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton, ReactionTypeEmoji

#-------------------------------------------------------------------------------------------------------------------ПЕРЕМЕННЫЕ И НАСТРОЙКИ

BOT_TOKEN = "8260911545:AAGAw3r03Q_MW4-I2x2egqJ2FhVcZNIkxPo"

OWNER_ID = 832840031
USER_STATS = {}
CHAT_HISTORY = {}
dp = Dispatcher()
ACTIVE_DUELS = {}

#-------------------------------------------------------------------------------------------------------------------БАЗА ДАННЫХ (SQLite + WAL)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "database.db")
VOICE_FILE_PATH = os.path.join(BASE_DIR, "ghost.mp3")

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("PRAGMA journal_mode=WAL;")
cursor.execute("PRAGMA synchronous=NORMAL;")
conn.commit()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        points INTEGER DEFAULT 0
    )
''')
conn.commit()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS allowed_tags (
        tag_name TEXT PRIMARY KEY
    )
''')
# Таблица подписок остается старой
cursor.execute('''
    CREATE TABLE IF NOT EXISTS tags (
        tag_name TEXT,
        user_id INTEGER,
        PRIMARY KEY (tag_name, user_id)
    )
''')
conn.commit()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS chats (
        chat_id INTEGER PRIMARY KEY,
        title TEXT
    )
''')
conn.commit()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS twitter_state (
        account TEXT PRIMARY KEY,
        last_post_id TEXT
    )
''')
conn.commit()

#-------------------------------------------------------------------------------------------------------------------ФУНКЦИИ БД

DUELS_FILE = os.path.join(DATA_DIR, "duels.json")
def load_duels():
    """Загружает игры и восстанавливает asyncio.Lock"""
    if os.path.exists(DUELS_FILE):
        try:
            with open(DUELS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                duels = {}
                for k, v in data.items():
                    game_id = int(k)
                    v["lock"] = asyncio.Lock()
                    duels[game_id] = v
                return duels
        except Exception as e:
            print(f"Ошибка загрузки дуэлей: {e}")
            return {}
    return {}

def register_chat(chat_id, title):
    """Сохраняет ID и название чата в базу"""
    try:
        cursor.execute("INSERT OR REPLACE INTO chats (chat_id, title) VALUES (?, ?)", (chat_id, title))
        conn.commit()
    except: pass

def get_user_by_username(username_text):
    """Ищет ID и Имя пользователя в базе по нику"""
    clean_name = username_text.replace("@", "").lower()
    try:
        cursor.execute("SELECT user_id, name FROM users WHERE username = ?", (clean_name,))
        row = cursor.fetchone()
        if row:
            return {"id": row[0], "name": row[1]}
    except: pass
    return None

def get_user_data(user_id):
    """Получает ВСЮ статистику игрока"""
    try:
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        else:
            return {'wins': 0, 'losses': 0, 'points': 0}
    except Exception as e:
        print(f"Ошибка БД (get): {e}") 
        return {'wins': 0, 'losses': 0, 'points': 0}

def update_usage(user_id, field):
    """Увеличивает счетчик использования класса или оружия"""
    try:
        cursor.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
        cursor.execute(f'UPDATE users SET {field} = {field} + 1 WHERE user_id = ?', (user_id,))
        conn.commit()
    except Exception as e:
        print(f"Ошибка обновления статы использования: {e}")

def update_duel_stats(user_id, is_winner):
    """Обновляет очки после дуэли"""
    try:
        cursor.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
        
        if is_winner:
            cursor.execute('UPDATE users SET wins = wins + 1, points = points + 25 WHERE user_id = ?', (user_id,))
        else:
            cursor.execute('UPDATE users SET losses = losses + 1, points = MAX(0, points - 10) WHERE user_id = ?', (user_id,))
        
        conn.commit()
    except Exception as e:
        print(f"Ошибка БД (get): {e}")

def update_stat(user_id, stat_type):
    """
    Эта функция нужна, чтобы старый код модерации не выдавал ошибку.
    Но в БД мы ничего не пишем.
    """
    pass 

def get_rank_info(points):
    """Функция расчета ранга"""
    tiers = [
        (50, "Страж"),
        (150, "Удаль"),
        (350, "Отвага"),
        (700, "Героизм"),
        (1500, "Величие"),
        (3500, "Легенда"),
        (float('inf'), "PVPGOD Барахолки")
    ]
    
    for threshold, title in tiers:
        if points < threshold:
            if threshold == float('inf'):
                return "PVPGOD Барахолки", 0
            
            needed = int(threshold - points)
            return title, needed
            
    return "PVPGOD Барахолки", 0

def save_duels():
    """Сохраняет игры в файл"""
    try:
        data_to_save = {}
        for k, v in ACTIVE_DUELS.items():
            game_copy = v.copy()
            
            if "lock" in game_copy: del game_copy["lock"]
            if "last_update" in game_copy: del game_copy["last_update"]
            
            data_to_save[k] = game_copy
            
        with open(DUELS_FILE, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Ошибка сохранения дуэлей: {e}")

def get_top_users():
    """Возвращает топ-5 по сообщениям и топ-5 Рейтинга (с играми)"""
    try:
        # 1. Топ болтунов
        cursor.execute('SELECT user_id, msg_count FROM users ORDER BY msg_count DESC LIMIT 10')
        top_chatters = cursor.fetchall()

        # 2. Топ рейтинга (ID, Очки, Игры)
        cursor.execute('SELECT user_id, points, (wins + losses) as games FROM users ORDER BY points DESC LIMIT 5')
        top_rating = cursor.fetchall()
        
        return top_chatters, top_rating
    except Exception:
        return [], []

ACTIVE_DUELS = load_duels()

#-------------------------------------------------------------------------------------------------------------------ОБЩИЕ ФУНКЦИИ

async def log_to_owner(text):
    """Отправляет лог владельцу (с защитой от HTML-ошибок)"""
    print(f"LOG: {text}")
    try:
        safe_text = hd.quote(str(text))
        await bot.send_message(OWNER_ID, f"🤖 <b>SYSTEM LOG:</b>\n{safe_text}")
    except Exception as e:
        print(f"⚠️ Не удалось отправить лог: {e}")

async def delete_later(message: types.Message, delay: int):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass

def update_msg_stats(user_id):
    """Увеличивает счетчик сообщений пользователя"""
    try:
        cursor.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
        cursor.execute('UPDATE users SET msg_count = msg_count + 1 WHERE user_id = ?', (user_id,))
        conn.commit()
    except Exception:
        pass

#-------------------------------------------------------------------------------------------------------------------ХЕНДЛЕРЫ

#-------------------------------------------------------------------------------------------------------------------ОБНОВЛЕНИЕ БД (ЛС БОТА)
@dp.message(F.document)
async def upload_db_handler(message: types.Message):
    if message.from_user.id != OWNER_ID: return
    
    if message.document.file_name != "database.db":
        return

    await bot.download(message.document, destination=DB_PATH)
    await message.reply("✅ База данных успешно обновлена! Перезагружаю...", reply_markup=None)

#-------------------------------------------------------------------------------------------------------------------СТАТА В ДУЭЛЯХ
@dp.message(Command("stats"))
async def stats_command(message: types.Message):
    target = message.reply_to_message.from_user if message.reply_to_message else message.from_user
    user_id = target.id
    name = target.first_name

    data = get_user_data(user_id)
    
    wins = data.get('wins', 0)
    losses = data.get('losses', 0)
    points = data.get('points', 0)
    total_games = wins + losses
    winrate = round((wins / total_games) * 100, 1) if total_games > 0 else 0.0
    rank_title, points_needed = get_rank_info(points)
    
    classes = {
        "<tg-emoji emoji-id='5330515960111583947'>🐍</tg-emoji> Хантер": data.get('class_hunter', 0),
        "<tg-emoji emoji-id='5330564987163267533'>🦅</tg-emoji> Варлок": data.get('class_warlock', 0),
        "<tg-emoji emoji-id='5330353116426551101'>🦁</tg-emoji> Титан": data.get('class_titan', 0)
    }
    fav_class = max(classes, key=classes.get)
    if classes[fav_class] == 0: fav_class = "Не определен"

    weapons = {
        "<tg-emoji emoji-id='5244894167863166109'>🃏</tg-emoji> Ace of Spades": data.get('w_ace', 0),
        "<tg-emoji emoji-id='5472003139303409777'>🤠</tg-emoji> Last Word": data.get('w_lw', 0),
        "<tg-emoji emoji-id='5199852661146422050'>🧪</tg-emoji> Thorn": data.get('w_thorns', 0),
        "<tg-emoji emoji-id='5471959145953396609'>🔥</tg-emoji> Golden Gun": data.get('w_gg', 0),
        "<tg-emoji emoji-id='5469821755478547431'>🔮</tg-emoji> Nova Bomb": data.get('w_nova', 0),
        "<tg-emoji emoji-id='5472214494644045946'>⚡️</tg-emoji> ThunderCrash": data.get('w_crash', 0)
    }
    fav_weapon = max(weapons, key=weapons.get)
    if weapons[fav_weapon] == 0: fav_weapon = "Кулаки"

    if points_needed > 0:
        next_rank_str = f"<tg-emoji emoji-id='5416117059207572332'>➡️</tg-emoji> <b>До повышения:</b> {points_needed} очков"
    else:
        next_rank_str = "<tg-emoji emoji-id='5357107601584693888'>👑</tg-emoji> <b>Максимальный ранг</b>"

    d = message.from_user
    du = f"@{d.username}"
    
    text = (
        f"<tg-emoji emoji-id='5434144690511290129'>📰</tg-emoji> <b>ДОСЬЕ ГОРНИЛА:</b> {du}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"<tg-emoji emoji-id='5238027455754680851'>🎖</tg-emoji> <b>Ранг:</b> {rank_title} ({points} очков)\n"
        f"{next_rank_str}\n"
        f"<tg-emoji emoji-id='5408935401442267103'>⚔️</tg-emoji> <b>Матчей:</b> {total_games}\n"
        f"✅ <b>Побед:</b> {wins}\n"
        f"❌ <b>Поражений:</b> {losses}\n"
        f"<tg-emoji emoji-id='5244837092042750681'>📈</tg-emoji> <b>Винрейт:</b> {winrate}%\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"<tg-emoji emoji-id='5312138559556164615'>❤️</tg-emoji> <b>Класс:</b> {fav_class}\n"
        f"<tg-emoji emoji-id='5312138559556164615'>❤️</tg-emoji> <b>Револьвер:</b> {fav_weapon}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"<i>Шакс наблюдает за тобой.</i>"
    )
    
    msg = await message.reply(text)
    asyncio.create_task(delete_later(msg, 60))

#-------------------------------------------------------------------------------------------------------------------DUEL RPG
@dp.message(Command("duel"))
async def duel_command(message: types.Message, command: CommandObject):
    # Инициализация переменных
    attacker_id = 0
    defender_id = 0
    att_name = ""
    def_name = ""
    
    # 1. Сценарий АДМИНА: /duel @p1 @p2
    args = command.args
    admin_mode = False
    
    user_status = await bot.get_chat_member(message.chat.id, message.from_user.id)
    if args and user_status.status in ["administrator", "creator"]:
        usernames = args.split()
        if len(usernames) >= 2:
            u1 = get_user_by_username(usernames[0])
            u2 = get_user_by_username(usernames[1])
            
            if u1 and u2:
                attacker_id = u1["id"]
                att_name = f"@{usernames[0].replace('@','').replace(',','')}" # Чистим от @ и запятых
                
                defender_id = u2["id"]
                def_name = f"@{usernames[1].replace('@','').replace(',','')}"
                
                admin_mode = True
            else:
                await message.reply("<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Кого-то из них нет в моей базе (пусть напишут что-нибудь в чат).")
                return
    
    # 2. Сценарий ОБЫЧНЫЙ: Ответ на сообщение
    if not admin_mode:
        if not message.reply_to_message:
            msg = await message.reply("<tg-emoji emoji-id='5408935401442267103'>⚔️</tg-emoji> Чтобы вызвать на дуэль, ответь на сообщение соперника командой <code>/duel</code>.")
            asyncio.create_task(delete_later(msg, 5))
            return

        attacker = message.from_user
        defender = message.reply_to_message.from_user
        
        # Проверка на ботов (только в обычном режиме, т.к. есть объект User)
        if defender.is_bot or defender.id == 777000:
            msg = await message.reply("<tg-emoji emoji-id='5318773107207447403'>😱</tg-emoji> Ты вызываешь на бой саму Пустоту? Найди живого соперника.")
            asyncio.create_task(delete_later(msg, 5))
            return

        attacker_id = attacker.id
        defender_id = defender.id
        
        att_name = f"@{attacker.username}" if attacker.username else attacker.first_name
        def_name = f"@{defender.username}" if defender.username else defender.first_name

    # Общие проверки ID
    if defender_id == attacker_id:
        msg = await message.reply("Найди себе достойного противника (не себя) <tg-emoji emoji-id='5316850074255367258'>🤬</tg-emoji>.")
        asyncio.create_task(delete_later(msg, 5))
        return
    
    buttons = [
        [
            InlineKeyboardButton(text="✅ Принять вызов", callback_data=f"duel_start|{attacker_id}|{defender_id}"),
            InlineKeyboardButton(text="❌ Отказаться", callback_data=f"duel_decline|{attacker_id}|{defender_id}")
        ]
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    intro = f"<tg-emoji emoji-id='5408935401442267103'>⚔️</tg-emoji> <b>ТУРНИРНЫЙ МАТЧ!</b> <tg-emoji emoji-id='5319018096436977294'>🔫</tg-emoji><tg-emoji emoji-id='5319002780583600195'>🔫</tg-emoji>\n\n" if admin_mode else f"<tg-emoji emoji-id='5408935401442267103'>⚔️</tg-emoji> <b>ГОРНИЛО: ДУЭЛЬ!</b> <tg-emoji emoji-id='5319018096436977294'>🔫</tg-emoji><tg-emoji emoji-id='5319002780583600195'>🔫</tg-emoji>\n\n"
    
    await message.answer(
        f"{intro}"
        f"<b><tg-emoji emoji-id='5469797093776332017'>👤</tg-emoji> Страж №1:</b> {att_name}\n"
        f"<b><tg-emoji emoji-id='5469982881176653032'>👤</tg-emoji> Страж №2:</b> {def_name}\n\n"
        f"<b><tg-emoji emoji-id='5334544901428229844'>ℹ️</tg-emoji> Сетапы классов:</b>\n"
        f"<tg-emoji emoji-id='5330515960111583947'>🐍</tg-emoji> - Ханты: ГГ & Сияние;\n"
        f"<tg-emoji emoji-id='5330564987163267533'>🦅</tg-emoji> - Варлоки: Нова & Пожирание;\n"
        f"<tg-emoji emoji-id='5330353116426551101'>🦁</tg-emoji> - Титаны: ТКраш & Усиление.\n"
        f"<b><tg-emoji emoji-id='5334544901428229844'>ℹ️</tg-emoji> Оружие на выбор:</b>\n"
        f"<tg-emoji emoji-id='5244894167863166109'>🃏</tg-emoji> - Пиковый Туз;\n"
        f"<tg-emoji emoji-id='5472003139303409777'>🤠</tg-emoji> - Ластворд;\n"
        f"<tg-emoji emoji-id='5199852661146422050'>🧪</tg-emoji> - Шип.\n\n"
        f"<b>{def_name}</b>, ты принимаешь бой?",
        reply_markup=keyboard
    )

async def update_duel_message(callback: types.CallbackQuery, game_id):
    if game_id not in ACTIVE_DUELS:
        try: await callback.message.edit_reply_markup(reply_markup=None)
        except: pass; return

    game = ACTIVE_DUELS[game_id]
    
    # Анти-спам таймер
    now = datetime.now()
    last = game.get("last_update", datetime.min)
    if (now - last).total_seconds() < 0.5: return
    game["last_update"] = now
    
    def get_hp_bar(hp):
        blocks = int(hp / 10) 
        return "▓" * blocks + "░" * (10 - blocks)

    p1 = game["p1"]
    p2 = game["p2"]
    current_player = p1 if game["turn"] == p1["id"] else p2
    
    # Статусы и текст (как раньше)
    ru_classes = {"hunter": "<tg-emoji emoji-id='5330515960111583947'>🐍</tg-emoji>", "warlock": "<tg-emoji emoji-id='5330564987163267533'>🦅</tg-emoji>", "titan": "<tg-emoji emoji-id='5330353116426551101'>🦁</tg-emoji>"}
    
    warning_msg = ""
    if game["pending_attack"]:
        atk_name = game["pending_attack"]["name"]
        warning_msg = f"\n\n<tg-emoji emoji-id='5440660757194744323'>⚠️</tg-emoji> <b>ВНИМАНИЕ!</b> В тебя летит <b>{atk_name}</b>!\nУгадай направление атаки и сделай стрейф!"
    
    aiming_msg = ""
    if game["pending_aim"] and not game["pending_attack"]:
        aiming_msg = "\n\n<tg-emoji emoji-id='5472003139303409777'>🤠</tg-emoji> <b>ПРИЦЕЛИВАНИЕ:</b> Куда стрелять?"

    text = (
        f"<tg-emoji emoji-id='5408935401442267103'>⚔️</tg-emoji> <b>{ru_classes[p1['class']]} vs {ru_classes[p2['class']]}</b>\n\n"
        f"<tg-emoji emoji-id='5469797093776332017'>👤</tg-emoji> <b>{p1['name']}</b>: {p1['hp']} HP\n[{get_hp_bar(p1['hp'])}]\n\n"
        f"<tg-emoji emoji-id='5469982881176653032'>👤</tg-emoji> <b>{p2['name']}</b>: {p2['hp']} HP\n[{get_hp_bar(p2['hp'])}]\n\n"
        f"<i>Лог: {game['log']}</i>{warning_msg}{aiming_msg}\n\n"
        f"<b>— Ход:</b> {current_player['name']}"
    )

    buttons = []

    # === ЛОГИКА ОТОБРАЖЕНИЯ КНОПОК ===
    
    # 1. СЦЕНАРИЙ ЗАЩИТЫ (В меня стреляют -> Стрейф)
    if game["pending_attack"]:
        buttons = [
            [
                InlineKeyboardButton(text="⬅️ СТРЕЙФ ВЛЕВО", callback_data="duel_strafe_left"),
                InlineKeyboardButton(text="➡️ СТРЕЙФ ВПРАВО", callback_data="duel_strafe_right")
            ]
        ]
        
    # 2. СЦЕНАРИЙ АТАКИ (Я выбрал пушку -> Выбираю сторону)
    elif game["pending_aim"] is not None:
        action_name = game["pending_aim"]["name"] # Например "Ace"
        buttons = [
            [
                InlineKeyboardButton(text=f"⬅️ {action_name} ВЛЕВО", callback_data="duel_fire_left"),
                InlineKeyboardButton(text=f"➡️ {action_name} ВПРАВО", callback_data="duel_fire_right")
            ],
            [InlineKeyboardButton(text="🔙 Отмена", callback_data="duel_aim_cancel")]
        ]

    # 3. СЦЕНАРИЙ ОБЫЧНЫЙ (Выбор действия)
    else:
        cw = current_player["weapon"]
        cc = current_player["class"]
        
        # Кнопка оружия
        w_text = "Огонь"
        if cw == "ace": w_text = "♠️ Ace (Crit)"
        elif cw == "lw": w_text = "🤠 Last Word (Burst)"
        elif cw == "thorn": w_text = "🧪 Thorn (DoT)"
        
        weapon_btn = InlineKeyboardButton(text=w_text, callback_data="duel_prep_primary") # PREP!

        if cc == "hunter":
            buttons = [
                [weapon_btn, InlineKeyboardButton(text="✨ Сияние (+Dmg)", callback_data="duel_buff_radiant")],
                [InlineKeyboardButton(text="🔥 Golden Gun (9%)", callback_data="duel_prep_gg")]
            ]
        elif cc == "warlock":
            buttons = [
                [weapon_btn, InlineKeyboardButton(text="🩸 Пожирание (+Heal)", callback_data="duel_buff_devour")],
                [InlineKeyboardButton(text="🔮 Nova Bomb (14%)", callback_data="duel_prep_nova")]
            ]
        elif cc == "titan":
            buttons = [
                [weapon_btn, InlineKeyboardButton(text="🛡 Усиление (Щит)", callback_data="duel_buff_amplify")],
                [InlineKeyboardButton(text="⚡️ Thundercrash (22%)", callback_data="duel_prep_crash")]
            ]

    buttons.append([InlineKeyboardButton(text="🔄", callback_data="duel_refresh")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    try: await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception as e:
        if "Flood control" in str(e):
            await asyncio.sleep(1)
            try:
                await callback.message.edit_text(text, reply_markup=keyboard)
            except: pass
        pass

#-------------------------------------------------------------------------------------------------------------------ОБРАБОТКА ВЫБОРА (КЛАСС + ОРУЖИЕ)
@dp.callback_query(F.data.startswith("pick_"))
async def duel_class_handler(callback: types.CallbackQuery):
    game_id = callback.message.message_id
    if game_id not in ACTIVE_DUELS:
        await callback.answer("Матч устарел.", show_alert=True)
        return

    game = ACTIVE_DUELS[game_id]
    user_id = callback.from_user.id
    data = callback.data

    player_key = None
    if user_id == game["p1"]["id"]: player_key = "p1"
    elif user_id == game["p2"]["id"]: player_key = "p2"
    else:
        await callback.answer("Ты не участвуешь!", show_alert=True)
        return

    player = game[player_key]

#-------------------------------------------------------------------------------------------------------------------ЛОГИКА ВЫБОРА

    if data == "pick_full_random":
        if player["class"] and player["weapon"]:
            await callback.answer("Ты уже готов!", show_alert=True); return
        player["class"] = random.choice(["hunter", "warlock", "titan"])
        player["weapon"] = random.choice(["ace", "lw"])
        await callback.answer("Случайный билд выбран!")

    elif "pick_class" in data:
        cls = data.split("_")[2]
        player["class"] = cls
        await callback.answer(f"Класс: {cls.capitalize()}")

    elif "pick_weapon" in data:
        wpn = data.split("_")[2] # ace/lw
        if not player["class"]:
            await callback.answer("Сначала выбери класс!", show_alert=True)
            return
        player["weapon"] = wpn
        await callback.answer(f"Оружие: {wpn.capitalize()}")

#-------------------------------------------------------------------------------------------------------------------ОБНОВЛЕНИЕ СТАТУСА
    
    def get_status(p):
        if not p["class"]: return "Выбирает класс..."
        if not p["weapon"]: return f"{p['class'].capitalize()} (Выбирает оружие...)"
        return "<tg-emoji emoji-id='5206607081334906820'>✔️</tg-emoji> ГОТОВ"

    if game["p1"]["class"] and game["p1"]["weapon"] and \
       game["p2"]["class"] and game["p2"]["weapon"]:
        
        game["state"] = "fighting"
        game["turn"] = random.choice([game["p1"]["id"], game["p2"]["id"]])

        update_usage(game["p1"]["id"], f"class_{game['p1']['class']}")
        update_usage(game["p2"]["id"], f"class_{game['p2']['class']}")

        ru_classes = {"hunter": "Хантер", "warlock": "Варлок", "titan": "Титан"}
           
        c1 = game["p1"]["class"]
        c2 = game["p2"]["class"]
        game["log"] = f"<tg-emoji emoji-id='5408935401442267103'>⚔️</tg-emoji> {c1.upper()} vs {c2.upper()}! Бой начинается!"
        
        await update_duel_message(callback, game_id)
    else:
        text = (
            f"<tg-emoji emoji-id='5442864698187856287'>👜</tg-emoji> <b>ВЫБОР СНАРЯЖЕНИЯ</b>\n\n"
            f"<tg-emoji emoji-id='5469797093776332017'>👤</tg-emoji> <b>{game['p1']['name']}:</b> {get_status(game['p1'])}\n"
            f"<tg-emoji emoji-id='5469982881176653032'>👤</tg-emoji> <b>{game['p2']['name']}:</b> {get_status(game['p2'])}\n\n"
            f"1. Выбери Класс\n2. Выбери Оружие"
        )
        try: await callback.message.edit_text(text, reply_markup=callback.message.reply_markup)
        except: pass
        
    await callback.answer()

@dp.callback_query(F.data == "duel_refresh")
async def duel_refresh_handler(callback: types.CallbackQuery):
    game_id = callback.message.message_id
    if game_id not in ACTIVE_DUELS:
        await callback.answer("Попытка восстановить...", show_alert=True)
        return
        
    await update_duel_message(callback, game_id)
    await callback.answer("Интерфейс обновлен.")

@dp.callback_query(F.data.startswith("duel_"))
async def duel_handler(callback: types.CallbackQuery):
    data_parts = callback.data.split("|")
    action = data_parts[0]

    if action == "duel_decline":
        attacker_id = int(data_parts[1])
        defender_id = int(data_parts[2])
        user_id = callback.from_user.id
        
        if user_id != defender_id and user_id != attacker_id:
            await callback.answer("Не лезь, это не твой бой!", show_alert=True)
            return

        if user_id == attacker_id:
            await callback.message.edit_text(f"<tg-emoji emoji-id='5445267414562389170'>🗑</tg-emoji> <b>Вызов отозван.</b> Дуэль удалена.")
            return

        if user_id == defender_id:
            await callback.message.edit_text(f"<tg-emoji emoji-id='5445267414562389170'>🗑</tg-emoji> <b>Вызов отклонён.</b> Дуэль удалена.")
            return
    
    game_id = callback.message.message_id
    
    if game_id not in ACTIVE_DUELS:
        try:
            saved_duels = load_duels()
            if game_id in saved_duels:
                ACTIVE_DUELS[game_id] = saved_duels[game_id]
                print(f"🔄 Игра {game_id} восстановлена из файла.")
        except: pass

    if action != "duel_start" and game_id not in ACTIVE_DUELS:
        await callback.answer("Игра не найдена (удалена или устарела).", show_alert=True)
        try: await callback.message.edit_text("<tg-emoji emoji-id='5445267414562389170'>🗑</tg-emoji> <b>Матч удалён.</b>", reply_markup=None)
        except: pass
        return

#-------------------------------------------------------------------------------------------------------------------СТАРТ
    if action == "duel_start":
        attacker_id = int(data_parts[1])
        defender_id = int(data_parts[2])
        if callback.from_user.id != defender_id:
            await callback.answer("Жди решения соперника!", show_alert=True)
            return

        game_id = callback.message.message_id
        
        try:
            att_m = await bot.get_chat_member(callback.message.chat.id, attacker_id)
            def_m = await bot.get_chat_member(callback.message.chat.id, defender_id)
            att_name = f"@{att_m.user.username}" if att_m.user.username else att_m.user.first_name
            def_name = f"@{def_m.user.username}" if def_m.user.username else def_m.user.first_name
        except:
            att_name, def_name = "Игрок 1", "Игрок 2"

        ACTIVE_DUELS[game_id] = {
            "p1": { "id": attacker_id, "name": att_name, "hp": 100, "class": None, "weapon": None, "ace_streak": 0, "poison_turns": 0, "buff_dmg": 0, "buff_heal": False, "buff_def": 0 },
            "p2": { "id": defender_id, "name": def_name, "hp": 100, "class": None, "weapon": None, "ace_streak": 0, "poison_turns": 0, "buff_dmg": 0, "buff_heal": False, "buff_def": 0 },
            "state": "choosing_class",
            "log": "<tg-emoji emoji-id='5442864698187856287'>👜</tg-emoji> Ожидание выбора снаряжения...",
            "pending_crash": None,
            "crash_turns": 0,
            "crash_direction": None, # <--- Куда полетел титан
            "pending_attack": None,  # <--- Летящая пуля
            "pending_aim": None,     # <--- Атакующий выбирает направление
            "lock": asyncio.Lock()
        }

        buttons = [
            [
                InlineKeyboardButton(text="🐍 Хантер", callback_data="pick_class_hunter"),
                InlineKeyboardButton(text="🔮 Варлок", callback_data="pick_class_warlock"),
                InlineKeyboardButton(text="🛡 Титан", callback_data="pick_class_titan")
            ],
            [
                InlineKeyboardButton(text="♠️ Ace of Spades", callback_data="pick_weapon_ace"),
                InlineKeyboardButton(text="🤠 Last Word", callback_data="pick_weapon_lw"),
                InlineKeyboardButton(text="🧪 Thorn", callback_data="pick_weapon_thorn")
            ],
            [InlineKeyboardButton(text="🎲 Случайный билд", callback_data="pick_full_random")]
        ]
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        text = (
            f"<tg-emoji emoji-id='5442864698187856287'>👜</tg-emoji> <b>ВЫБОР СНАРЯЖЕНИЯ</b>\n\n"
            f"<tg-emoji emoji-id='5469797093776332017'>👤</tg-emoji> <b>{att_name}:</b> Выбор...\n"
            f"<tg-emoji emoji-id='5469982881176653032'>👤</tg-emoji> <b>{def_name}:</b> Выбор...\n\n"
            f"1. Выбери Класс\n2. Выбери Оружие"
        )

        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer()
        return

#-------------------------------------------------------------------------------------------------------------------БАФФЫ (АБИЛКИ)
    if action in ["duel_buff_radiant", "duel_buff_devour", "duel_buff_amplify"]:
        game_id = callback.message.message_id
        if game_id not in ACTIVE_DUELS: return
        game = ACTIVE_DUELS[game_id]
        
        async with game["lock"]:
            if callback.from_user.id != game["turn"]:
                await callback.answer("Не твой ход!", show_alert=True)
                return

            if callback.from_user.id == game["p1"]["id"]:
                caster, enemy = game["p1"], game["p2"]
            else:
                caster, enemy = game["p2"], game["p1"]

            buff_name = ""
            log_msg = ""
            
            if action == "duel_buff_radiant" and caster["class"] == "hunter":
                # ЗАЩИТА: Если бафф уже висит
                if caster.get("buff_dmg", 0) > 0:
                    await callback.answer("Сияние уже активно!", show_alert=True); return
                
                caster["buff_dmg"] = 10
                buff_name = "💥 Сияние"
                log_msg = f"{caster['name']} активирует <tg-emoji emoji-id='5472158054478810637'>💥</tg-emoji> <b>Сияние</b>! След. выстрел +10 урона."
                save_duels()
            elif action == "duel_buff_devour" and caster["class"] == "warlock":
                # ЗАЩИТА
                if caster.get("buff_heal"):
                    await callback.answer("Пожирание уже активно!", show_alert=True); return
                
                caster["buff_heal"] = True
                buff_name = "🩸 Пожирание"
                log_msg = f"{caster['name']} активирует <tg-emoji emoji-id='5474317667114457231'>🩸</tg-emoji> <b>Пожирание</b>! След. попадание исцелит 10 HP."
                save_duels()
            elif action == "duel_buff_amplify" and caster["class"] == "titan":
                # ЗАЩИТА
                if caster.get("buff_def", 0) > 0:
                    await callback.answer("Усиление уже активно!", show_alert=True); return
                
                caster["buff_def"] = 10
                buff_name = "⚡️ Усиление"
                log_msg = f"{caster['name']} получает <tg-emoji emoji-id='5472175852823282918'>⚡️</tg-emoji> <b>Усиление</b>! След. урон по нему снижен на 10."
                save_duels()
            else:
                await callback.answer("Не твой класс!", show_alert=True)
                return

            # ТИК ЯДА + КОМБО С БАФФОМ
            if enemy["poison_turns"] > 0:
                poison_dmg = 12
                
                # 1. КОМБО С СИЯНИЕМ (Если только что включили или висело)
                if caster["buff_dmg"] > 0:
                    poison_dmg += caster["buff_dmg"]
                    caster["buff_dmg"] = 0 # Сгорает
                    log_msg += f"\n<tg-emoji emoji-id='5472158054478810637'>💥</tg-emoji> <b>СИЯЮЩИЙ ЯД!</b> ({poison_dmg} урона)"
                else:
                    log_msg += f"\n<tg-emoji emoji-id='5411138633765757782'>🧪</tg-emoji> Яд сжигает {enemy['name']} (-12 HP)!"

                # 2. КОМБО С ПОЖИРАНИЕМ
                if caster["buff_heal"]:
                    caster["hp"] += 10
                    if caster["hp"] > 100: caster["hp"] = 100
                    caster["buff_heal"] = False # Сгорает
                    log_msg += " (<tg-emoji emoji-id='5474317667114457231'>🩸</tg-emoji> +10 HP)"

                # Наносим урон
                enemy["hp"] -= poison_dmg
                enemy["poison_turns"] -= 1
                
                # Проверка смерти
                if enemy["hp"] <= 0:
                    enemy["hp"] = 0
                    update_duel_stats(caster['id'], True); update_duel_stats(enemy['id'], False)
                    del ACTIVE_DUELS[game_id]; save_duels()
                    await callback.message.edit_text(f"<tg-emoji emoji-id='5312315739842026755'>🏆</tg-emoji> <b>ПОБЕДА!</b>\n\n{log_msg}\n\n<tg-emoji emoji-id='5411138633765757782'>🧪</tg-emoji> {enemy['name']} погиб от яда!", reply_markup=None)
                    await callback.answer(); return
            
            flying_titan_id = game.get("pending_crash")
            if flying_titan_id:
                game["crash_turns"] -= 1
                
                if game["crash_turns"] == 1:
                    # ВРЕМЯ ПРИШЛО! Формируем атаку
                    titan_pl = game["p1"] if game["p1"]["id"] == flying_titan_id else game["p2"]
                    
                    # Достаем сохраненное направление или ставим рандом (на всякий случай)
                    direction = game.get("crash_direction", random.choice(["left", "right"]))
                    
                    game["pending_attack"] = {
                        "damage": 100,
                        "type": "crash",
                        "name": "⚡ THUNDERCRASH",
                        "aim": direction, # <--- ИСПОЛЬЗУЕМ ВЫБРАННОЕ НАПРАВЛЕНИЕ
                        "log_msg": f"<tg-emoji emoji-id='5456140674028019486'>⚡️</tg-emoji> <b>ПРИЗЕМЛЕНИЕ!</b> Титан падает {direction.upper()}!",
                        "shooter_id": flying_titan_id
                    }
                    game["log"] += "\n⚠️ ТИТАН ПАДАЕТ! УКЛОНЯЙСЯ!"
                    # Ход оставляем у защитника, чтобы он увидел кнопки стрейфа
                    game["turn"] = caster["id"]
                else:
                    # Просто сообщение
                    game["log"] += f"\n⚡️ Титан в воздухе (остался 1 ход)"
                    game["turn"] = caster["id"]
            else:
                game["turn"] = enemy["id"]

            game["log"] = log_msg
            save_duels()
            await update_duel_message(callback, game_id)
            await callback.answer(f"{buff_name} активировано!")
            return

#-------------------------------------------------------------------------------------------------------------------ВЫСТРЕЛ (ОСНОВНОЙ И УЛЬТА)
    # -----------------------------------------------------------------------------------------
# 1. ПОДГОТОВКА К ВЫСТРЕЛУ (ВЫБОР ОРУЖИЯ)
# -----------------------------------------------------------------------------------------
    if action in ["duel_prep_primary", "duel_prep_gg", "duel_prep_nova", "duel_prep_crash"]:
        game_id = callback.message.message_id
        if game_id not in ACTIVE_DUELS: return
        game = ACTIVE_DUELS[game_id]
        
        async with game["lock"]:
            if callback.from_user.id != game["turn"]:
                await callback.answer("Не твой ход!", show_alert=True); return
            if game.get("pending_crash"): # Если титан летит, стрелять нельзя (технически)
                await callback.answer("Враг в воздухе! Жди приземления.", show_alert=True); return
            
            # Определяем красивое имя для кнопок
            name_map = {
                "duel_prep_primary": "Огонь",
                "duel_prep_gg": "GG",
                "duel_prep_nova": "Nova",
                "duel_prep_crash": "Crash"
            }
            
            game["pending_aim"] = {
                "action": action, # Сохраняем, что именно мы хотим использовать
                "name": name_map.get(action, "Атака")
            }
            
            await update_duel_message(callback, game_id)
            await callback.answer()
            return

    if action == "duel_aim_cancel":
        game = ACTIVE_DUELS.get(callback.message.message_id)
        if game:
            game["pending_aim"] = None
            await update_duel_message(callback, callback.message.message_id)
            await callback.answer()
        return

# -----------------------------------------------------------------------------------------
# 2. ВЫПОЛНЕНИЕ ВЫСТРЕЛА (ВЫБОР СТОРОНЫ) -> СОЗДАНИЕ PENDING_ATTACK
# -----------------------------------------------------------------------------------------
    if action in ["duel_fire_left", "duel_fire_right"]:
        game_id = callback.message.message_id
        if game_id not in ACTIVE_DUELS: return
        game = ACTIVE_DUELS[game_id]
        
        async with game["lock"]:
            shooter_id = callback.from_user.id
            if shooter_id != game["turn"]: return
            if not game["pending_aim"]: return # Если отменили и нажали старую кнопку
            
            # Получаем данные о том, что это было за оружие
            prep_data = game["pending_aim"]
            original_action = prep_data["action"] # duel_prep_primary и т.д.
            aim_direction = "left" if action == "duel_fire_left" else "right"
            
            game["pending_aim"] = None # Сброс прицеливания
            
            # Определяем участников
            if shooter_id == game["p1"]["id"]: shooter, target = game["p1"], game["p2"]
            else: shooter, target = game["p2"], game["p1"]

            # --- ТИК ЯДА ПЕРЕД АТАКОЙ ---
            if target["poison_turns"] > 0:
                target["hp"] -= 12
                target["poison_turns"] -= 1
                if target["hp"] <= 0:
                     # (код смерти от яда - скопируй из старого, для краткости опускаю)
                    return

            damage = 0
            log_msg = ""
            atk_name = "Атака"
            apply_poison = False
            apply_heal = False
            is_crash_start = False

            # === РАСЧЕТ УРОНА (БЕЗ НАНЕСЕНИЯ) ===
            
            if original_action == "duel_prep_primary":
                weapon_type = shooter["weapon"]
                if weapon_type == "ace":
                    update_usage(shooter_id, "w_ace")
                    atk_name = "Ace of Spades"
                    shooter["ace_streak"] = shooter.get("ace_streak", 0)
                    crit_chance = 28 if shooter["ace_streak"] == 1 else 0
                    if random.randint(1, 100) <= (crit_chance + 30):
                        damage = 50
                        shooter["ace_streak"] = 0
                        log_msg = f"<tg-emoji emoji-id='5276032951342088188'>💥</tg-emoji> <b>MEMENTO MORI!</b> Критический выстрел!"
                    else:
                        damage = 25
                        shooter["ace_streak"] = 1
                        log_msg = f"<tg-emoji emoji-id='5379748062124056162'>❗️</tg-emoji> Выстрел с Туза!"

                elif weapon_type == "lw":
                    update_usage(shooter_id, "w_lw")
                    atk_name = "Last Word"
                    shooter["ace_streak"] = 0
                    damage = 45 
                    log_msg = f"<tg-emoji emoji-id='5472003139303409777'>🤠</tg-emoji> Очередь с Last Word!"

                elif weapon_type == "thorn":
                    update_usage(shooter_id, "w_thorns")
                    atk_name = "Thorn"
                    shooter["ace_streak"] = 0
                    damage = 22
                    apply_poison = True
                    log_msg = f"<tg-emoji emoji-id='5199852661146422050'>🧪</tg-emoji> Выстрел с Шипа!"

            elif original_action == "duel_prep_gg":
                damage = 100
                atk_name = "Golden Gun"
                log_msg = f"<tg-emoji emoji-id='5312241539987020022'>🔥</tg-emoji> <b>GOLDEN GUN!</b>"
                
            elif original_action == "duel_prep_nova":
                damage = 85
                atk_name = "Nova Bomb"
                log_msg = f"<tg-emoji emoji-id='5330564987163267533'>🦅</tg-emoji> <b>NOVA BOMB!</b>"

            elif original_action == "duel_prep_crash":
                # ТИТАН: Не стреляем сейчас, а запоминаем направление и улетаем
                game["pending_crash"] = shooter_id 
                game["crash_turns"] = 2            
                game["crash_direction"] = aim_direction # <--- ЗАПОМИНАЕМ КУДА ПРИЗЕМЛИТСЯ
                game["turn"] = target["id"]        
                game["log"] = f"<tg-emoji emoji-id='5456140674028019486'>⚡️</tg-emoji> <b>ГРОМ!</b> {shooter['name']} выбирает цель ({aim_direction.upper()}) и взлетает!"
                is_crash_start = True

            # БАФФЫ К УРОНУ
            if damage > 0 and shooter["buff_dmg"] > 0:
                damage += shooter["buff_dmg"]
                shooter["buff_dmg"] = 0 

            if shooter["buff_heal"] and original_action == "duel_prep_primary":
                apply_heal = True
                shooter["buff_heal"] = False

            # ЕСЛИ ЭТО БЫЛ ЗАПУСК ТИТАНА - ПРЕРЫВАЕМСЯ, pending_attack НЕ СОЗДАЕМ
            if is_crash_start:
                save_duels()
                await update_duel_message(callback, game_id)
                await callback.answer()
                return

            # СОЗДАЕМ АТАКУ ДЛЯ ЗАЩИТНИКА
            game["pending_attack"] = {
                "damage": damage,
                "type": "super" if "gg" in original_action or "nova" in original_action else "primary",
                "name": atk_name,
                "aim": aim_direction, # <-- То, что выбрал стрелок
                "log_msg": log_msg,
                "shooter_id": shooter_id,
                "apply_poison": apply_poison,
                "apply_heal": apply_heal
            }
            
            game["turn"] = target["id"]
            save_duels()
            await update_duel_message(callback, game_id)
            await callback.answer("Атака отправлена!")
            return

# -----------------------------------------------------------------------------------------
# 3. СТРЕЙФ ЗАЩИТНИКА (ТОТ ЖЕ КОД, ЧТО В ПРОШЛОМ ОТВЕТЕ)
# -----------------------------------------------------------------------------------------
    if action in ["duel_strafe_left", "duel_strafe_right"]:
        game_id = callback.message.message_id
        if game_id not in ACTIVE_DUELS: return
        game = ACTIVE_DUELS[game_id]
        
        async with game["lock"]:
            if not game["pending_attack"]: return
            
            dodger_id = callback.from_user.id
            if dodger_id == game["p1"]["id"]: dodger, attacker = game["p1"], game["p2"]
            else: dodger, attacker = game["p2"], game["p1"]
                
            attack_info = game["pending_attack"]
            dodge_dir = "left" if action == "duel_strafe_left" else "right"
            
            # ГЛАВНАЯ ЛОГИКА: 
            # Атака Влево + Стрейф Влево = ПОПАДАНИЕ (Ты стрейфанул под пулю)
            # Атака Влево + Стрейф Вправо = УКЛОНЕНИЕ
            
            is_hit = (attack_info["aim"] == dodge_dir)
            
            final_dmg = attack_info["damage"] if is_hit else 0
            log_result = ""

            if is_hit:
                # Щит
                if final_dmg > 0 and final_dmg < 100 and dodger["buff_def"] > 0:
                    blocked = min(final_dmg, dodger["buff_def"])
                    final_dmg -= blocked
                    dodger["buff_def"] -= blocked
                    log_result = " [Щит]"

                dodger["hp"] -= final_dmg
                if dodger["hp"] < 0: dodger["hp"] = 0
                
                game["log"] = f"{attack_info['log_msg']}\n🔴 <b>ПОПАДАНИЕ!</b> Ты прыгнул под выстрел! (-{final_dmg}){log_result}"
                
                # Применение эффектов
                if attack_info.get("apply_poison"):
                    dodger["poison_turns"] = 3
                    game["log"] += " [Яд]"
                if attack_info.get("apply_heal"):
                    attacker["hp"] = min(100, attacker["hp"] + 10)
                    game["log"] += " [Heal]"
            else:
                game["log"] = f"<tg-emoji emoji-id='5206607081334906820'>✔️</tg-emoji> <b>УКЛОНЕНИЕ!</b> {dodger['name']} ушел от атаки ({attack_info['aim'].upper()})!"

            game["pending_attack"] = None

            # Смерть
            if dodger["hp"] <= 0:
                update_duel_stats(attacker['id'], True); update_duel_stats(dodger['id'], False)
                del ACTIVE_DUELS[game_id]
                await callback.message.edit_text(f"<tg-emoji emoji-id='5312315739842026755'>🏆</tg-emoji> <b>ПОБЕДА!</b>\n\n{game['log']}\n\n<tg-emoji emoji-id='5463186335948878489'>⚰️</tg-emoji> {dodger['name']} погиб.", reply_markup=None)
                await callback.answer(); return

            # Если это был Титан, ход возвращается Титану
            if attack_info["type"] == "crash":
                game["pending_crash"] = None
                game["crash_turns"] = 0
                game["turn"] = attacker["id"]
            else:
                game["turn"] = dodger["id"] # Иначе контратака

            save_duels()
            await update_duel_message(callback, game_id)
            await callback.answer()
            return

#-------------------------------------------------------------------------------------------------------------------ПРИМЕНЕНИЕ БАФФОВ И УРОНА
            if damage > 0 and shooter["buff_dmg"] > 0:
                damage += shooter["buff_dmg"]
                shooter["buff_dmg"] = 0
                log_msg += " (<tg-emoji emoji-id='5472158054478810637'>💥</tg-emoji> +10 DMG)"

            if damage > 0 and damage < 100 and target["buff_def"] > 0:
                blocked = min(damage, target["buff_def"]) 
                
                damage -= blocked
                target["buff_def"] -= blocked
                
                log_msg += f" (<tg-emoji emoji-id='5472175852823282918'>⚡️</tg-emoji> -{blocked})"
                if target["buff_def"] <= 0:
                    log_msg += " [Щит сломан]"

            if damage > 0 and shooter["buff_heal"] and action == "duel_shoot_primary":
                shooter["hp"] += 10
                if shooter["hp"] > 100: shooter["hp"] = 100
                shooter["buff_heal"] = False # Сгорает
                log_msg += " (<tg-emoji emoji-id='5474317667114457231'>🩸</tg-emoji> +10 HP)"

            # 1. Наносим урон врагу
            if damage > 0:
                target["hp"] -= damage
                if target["hp"] < 0: target["hp"] = 0

            # 2. ТИК ЯДА (У врага, в МОЙ ход)
            # Но есть нюанс: если мы ТОЛЬКО ЧТО попали Шипом, яд не должен тикнуть мгновенно.
            # (По твоим словам: "попадаю, противник ходит, Я делаю ход - дот срабатывает").
            
            is_new_poison = (action == "duel_shoot_primary" and shooter["weapon"] == "thorn" and hit)
            
            if target["poison_turns"] > 0 and not is_new_poison:
                target["hp"] -= 12
                target["poison_turns"] -= 1
                log_msg += f"\n<tg-emoji emoji-id='5411138633765757782'>🧪</tg-emoji> Яд сжигает {target['name']} (-12 HP)!"
                if target["hp"] < 0: target["hp"] = 0

            # 3. ПРОВЕРКА ПОБЕДЫ (От выстрела ИЛИ от яда)
            if target["hp"] <= 0:
                update_duel_stats(shooter['id'], True)
                update_duel_stats(target['id'], False)
                del ACTIVE_DUELS[game_id]
                
                # Если умер от яда, а не выстрела, можно поменять текст, но победа все равно моя
                await callback.message.edit_text(f"<tg-emoji emoji-id='5312315739842026755'>🏆</tg-emoji> <b>ПОБЕДА!</b>\n\n{log_msg}\n\n<tg-emoji emoji-id='5463186335948878489'>⚰️</tg-emoji> {target['name']} повержен.", reply_markup=None)
                await callback.answer()
                return

            # === ЛОГИКА ПРИЗЕМЛЕНИЯ ТИТАНА ===
            flying_titan_id = game.get("pending_crash")
            
            if flying_titan_id:
                if shooter_id != flying_titan_id: # Стреляет защитник
                    game["crash_turns"] -= 1
                    
                    if game["crash_turns"] <= 0:
                        # ВРЕМЯ ВЫШЛО -> ПРИЗЕМЛЕНИЕ
                        titan_id = flying_titan_id
                        titan = game["p1"] if game["p1"]["id"] == titan_id else game["p2"]
                        enemy_pl = game["p1"] if game["p1"]["id"] != titan_id else game["p2"]
                        game["pending_crash"] = None

                        # 1. ТИК ЯДА (У защитника/стрелка)
                        if shooter["poison_turns"] > 0:
                            shooter["hp"] -= 12
                            shooter["poison_turns"] -= 1
                            log_msg += f"\n<tg-emoji emoji-id='5411138633765757782'>🧪</tg-emoji> Яд (-12 HP)"
                            if shooter["hp"] <= 0:
                                shooter["hp"] = 0
                                update_duel_stats(titan['id'], True); update_duel_stats(shooter['id'], False)
                                del ACTIVE_DUELS[game_id]; save_duels()
                                await callback.message.edit_text(f"<tg-emoji emoji-id='5312315739842026755'>🏆</tg-emoji> <b>ПОБЕДА!</b>\n\n{log_msg}\n\n<tg-emoji emoji-id='5411138633765757782'>🧪</tg-emoji> {shooter['name']} погиб от яда!", reply_markup=None)
                                await callback.answer(); return
                        
                        # 2. УДАР ТИТАНА (Вне блока яда!)
                        if random.randint(1, 100) <= 17:
                            enemy_pl["hp"] = 0
                            update_duel_stats(titan['id'], True)
                            update_duel_stats(enemy_pl['id'], False)
                            del ACTIVE_DUELS[game_id]
                            msg = f"<tg-emoji emoji-id='5312315739842026755'>🏆</tg-emoji> <b>ПОБЕДА!</b>\n\n{log_msg}\n\n<tg-emoji emoji-id='5456140674028019486'>⚡️</tg-emoji> <b>БУУУМ!</b> {titan['name']} приземляется! (-100 HP)"
                            await callback.message.edit_text(msg, reply_markup=None)
                            await callback.answer()
                            return
                        else:
                            game["log"] = f"{log_msg}\n\n<tg-emoji emoji-id='5467538555158943525'>💭</tg-emoji> {titan['name']} промахивается тандеркрашем!"
                            game["turn"] = titan_id # Ход Титану
                    
                    else:
                        # ЕЩЕ ЛЕТИТ
                        game["log"] = f"{log_msg}\n<tg-emoji emoji-id='5440660757194744323'>‼️</tg-emoji> Титан летит! Осталось ходов: {game['crash_turns']}!"
                        game["turn"] = shooter_id # Ход стрелку
            else:
                # ОБЫЧНАЯ ПЕРЕДАЧА ХОДА
                game["turn"] = target["id"]
                game["log"] = log_msg

            save_duels()
            await update_duel_message(callback, game_id)
            await callback.answer()
            
#-------------------------------------------------------------------------------------------------------------------ЗАПУСК!!!

async def main():
    print(f"Бот запущен и готов к работе.")

    print(f"⏰ ВРЕМЯ СЕРВЕРА: {datetime.now()}")

    asyncio.create_task(check_silence_loop())
    
    await dp.start_polling(bot)

if __name__ == "__main__":

    asyncio.run(main())

