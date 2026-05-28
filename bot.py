import asyncio
import sqlite3
from typing import Optional, Dict, Any, List

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, BotCommand, ReplyKeyboardMarkup, KeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = "8874167673:AAFqQ8QFbqZAYK-j0uhDCfywmuuIlTSrZdk"
ADMIN_ID = 644509266
MAX_PLATES_PER_USER = 3  # Максимум номеров на одного пользователя
ANTISPAM_SECONDS = 60     # Антиспам 1 минута

# ==================== НОРМАЛИЗАЦИЯ НОМЕРА ====================
REPLACEMENTS = {
    'А': 'A', 'а': 'A',
    'В': 'B', 'в': 'B',
    'Е': 'E', 'е': 'E',
    'К': 'K', 'к': 'K',
    'М': 'M', 'м': 'M',
    'Н': 'H', 'н': 'H',
    'О': 'O', 'о': 'O',
    'Р': 'P', 'р': 'P',
    'С': 'C', 'с': 'C',
    'Т': 'T', 'т': 'T',
    'У': 'Y', 'у': 'Y',
    'Х': 'X', 'х': 'X',
}

def normalize_plate(plate: str) -> str:
    if not plate:
        return ""
    plate = plate.strip().upper()
    result = []
    for ch in plate:
        if ch in REPLACEMENTS:
            result.append(REPLACEMENTS[ch])
        elif ch.isdigit() or ('A' <= ch <= 'Z'):
            result.append(ch)
    return ''.join(result)

def validate_plate_format(plate: str) -> bool:
    normalized = normalize_plate(plate)
    if len(normalized) != 4:
        return False
    if normalized.isdigit():
        return True
    if len(normalized) == 4 and normalized[0].isalpha() and normalized[1:].isdigit():
        return True
    return False

# ==================== БАЗА ДАННЫХ ====================
DB_PATH = "road_alert.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_plates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            full_name TEXT,
            plate_normalized TEXT NOT NULL UNIQUE,
            plate_raw TEXT NOT NULL,
            is_blocked INTEGER DEFAULT 0,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_plate ON user_plates(plate_normalized)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_user ON user_plates(user_id)')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS message_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user_id INTEGER,
            to_user_id INTEGER,
            to_plate TEXT,
            message_text TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

def register_plate(user_id: int, username: str, full_name: str, plate_raw: str) -> tuple[bool, str]:
    normalized = normalize_plate(plate_raw)
    
    if not validate_plate_format(normalized):
        return False, "❌ Неверный формат. Нужно 4 символа: 4 цифры (0496) или буква+3 цифры (Е617)"
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Проверка лимита номеров
    cursor.execute('SELECT COUNT(*) FROM user_plates WHERE user_id = ?', (user_id,))
    count = cursor.fetchone()[0]
    if count >= MAX_PLATES_PER_USER:
        conn.close()
        return False, f"⚠️ У вас уже зарегистрировано {MAX_PLATES_PER_USER} номеров. Удалите один, чтобы добавить новый."
    
    cursor.execute('SELECT user_id FROM user_plates WHERE plate_normalized = ?', (normalized,))
    row = cursor.fetchone()
    if row:
        conn.close()
        return False, "⚠️ Этот номер уже зарегистрирован другим водителем."
    
    cursor.execute('SELECT id FROM user_plates WHERE user_id = ? AND plate_normalized = ?', 
                   (user_id, normalized))
    if cursor.fetchone():
        conn.close()
        return False, "⚠️ Вы уже регистрировали этот номер."
    
    try:
        cursor.execute('''
            INSERT INTO user_plates (user_id, username, full_name, plate_normalized, plate_raw)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, username, full_name, normalized, plate_raw.upper()))
        conn.commit()
        return True, normalized
    except Exception as e:
        return False, f"❌ Ошибка при регистрации"
    finally:
        conn.close()

def get_plate_record(plate: str) -> Optional[Dict[str, Any]]:
    normalized = normalize_plate(plate)
    if len(normalized) != 4:
        return None
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, plate_raw, is_blocked
        FROM user_plates
        WHERE plate_normalized = ?
    ''', (normalized,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_plates(user_id: int) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT plate_normalized, plate_raw, is_blocked, registered_at
        FROM user_plates 
        WHERE user_id = ?
        ORDER BY registered_at ASC
    ''', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def delete_plate(user_id: int, plate_normalized: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM user_plates 
        WHERE user_id = ? AND plate_normalized = ?
    ''', (user_id, plate_normalized))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def block_all_notifications(user_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('UPDATE user_plates SET is_blocked = 1 WHERE user_id = ?', (user_id,))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def unblock_all_notifications(user_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('UPDATE user_plates SET is_blocked = 0 WHERE user_id = ?', (user_id,))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def can_send_message(from_user_id: int, to_user_id: int, cooldown_seconds: int = ANTISPAM_SECONDS) -> bool:
    """Антиспам: 1 минута"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT COUNT(*) FROM message_log
        WHERE from_user_id = ? AND to_user_id = ?
        AND sent_at > datetime('now', '-' || ? || ' seconds')
    ''', (from_user_id, to_user_id, cooldown_seconds))
    count = cursor.fetchone()[0]
    conn.close()
    return count == 0

def log_message(from_user_id: int, to_user_id: int, to_plate: str, message_text: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO message_log (from_user_id, to_user_id, to_plate, message_text)
        VALUES (?, ?, ?, ?)
    ''', (from_user_id, to_user_id, to_plate, message_text[:500]))
    conn.commit()
    conn.close()

def get_stats() -> Dict[str, int]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM user_plates")
    users_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM user_plates")
    plates_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM message_log")
    messages_count = cursor.fetchone()[0]
    conn.close()
    return {"users": users_count, "plates": plates_count, "messages": messages_count}

# ==================== FSM ====================
class RegisterStates(StatesGroup):
    waiting_for_plate = State()

class DeleteStates(StatesGroup):
    waiting_selection = State()

# ==================== КЛАВИАТУРЫ ====================
def get_main_keyboard(has_plates: bool) -> ReplyKeyboardMarkup:
    if not has_plates:
        return ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="➕ Зарегистрировать номер")]],
            resize_keyboard=True
        )
    
    buttons = [
        [KeyboardButton(text="🚗 Мои номера"), KeyboardButton(text="➕ Добавить номер")],
        [KeyboardButton(text="🔇 Отключить уведомления"), KeyboardButton(text="🔔 Включить уведомления")],
        [KeyboardButton(text="❌ Удалить номер"), KeyboardButton(text="❓ Помощь")]
    ]
    
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

async def set_main_menu():
    # Только команда start в меню (stats скрыт от обычных пользователей)
    commands = [
        BotCommand(command="start", description="🏠 Главное меню"),
    ]
    await bot.set_my_commands(commands)

# ==================== ОБРАБОТЧИКИ ====================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    plates = get_user_plates(message.from_user.id)
    has_plates = len(plates) > 0
    
    if has_plates:
        plates_list = "\n".join([f"• {p['plate_raw']} {'🔇' if p['is_blocked'] else '🔔'}" for p in plates])
        await message.answer(
            f"🚗 С возвращением, {message.from_user.first_name}!\n\n"
            f"📋 Ваши номера:\n{plates_list}\n\n"
            f"✏️ Чтобы предупредить водителя, просто напишите его номер (4 символа) и сообщение.\n"
            f"Пример: `е617 не закрыт люк`\n\n"
            f"⚠️ Если получите чужое сообщение — просто проигнорируйте.",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(True)
        )
    else:
        await message.answer(
            f"👋 Привет, {message.from_user.first_name}!\n\n"
            f"🚦 Это бот для помощи водителям на дороге.\n\n"
            f"📌 Вы можете зарегистрировать до {MAX_PLATES_PER_USER} номеров (машина, мотоцикл и т.д.).\n\n"
            f"👇 Нажмите «➕ Зарегистрировать номер»",
            reply_markup=get_main_keyboard(False)
        )

@dp.message(Command("stats"))
async def show_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return  # Молча игнорируем
    stats = get_stats()
    await message.answer(
        f"📊 *Статистика бота*\n\n"
        f"👥 Пользователей: {stats['users']}\n"
        f"🚗 Номеров: {stats['plates']}\n"
        f"💬 Сообщений: {stats['messages']}",
        parse_mode="Markdown"
    )

@dp.message(F.text == "➕ Зарегистрировать номер")
async def start_registration(message: Message, state: FSMContext):
    await state.set_state(RegisterStates.waiting_for_plate)
    await message.answer(
        "🚗 *Регистрация автомобиля*\n\n"
        "Введите *первые 4 символа* вашего номера.\n\n"
        f"📌 Максимум номеров на пользователя: {MAX_PLATES_PER_USER}\n\n"
        "📌 Варианты:\n"
        "• 4 цифры, например `0496`\n"
        "• 1 буква + 3 цифры, например `Е617`\n\n"
        "➡️ *Отправьте 4 символа:*",
        parse_mode="Markdown"
    )

@dp.message(F.text == "➕ Добавить номер")
async def add_another_plate(message: Message, state: FSMContext):
    plates = get_user_plates(message.from_user.id)
    if len(plates) >= MAX_PLATES_PER_USER:
        await message.answer(
            f"⚠️ У вас уже зарегистрировано максимальное количество номеров ({MAX_PLATES_PER_USER}).\n\n"
            f"Удалите один, чтобы добавить новый.",
            reply_markup=get_main_keyboard(True)
        )
        return
    
    await state.set_state(RegisterStates.waiting_for_plate)
    await message.answer(
        "🚗 *Добавление ещё одного номера*\n\n"
        "Введите *первые 4 символа* номера другого автомобиля.\n\n"
        "➡️ *Отправьте 4 символа:*",
        parse_mode="Markdown"
    )

@dp.message(RegisterStates.waiting_for_plate)
async def process_plate_registration(message: Message, state: FSMContext):
    plate_raw = message.text.strip()
    
    success, result = register_plate(
        user_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        plate_raw=plate_raw
    )
    
    if success:
        plates = get_user_plates(message.from_user.id)
        await message.answer(
            f"✅ *Номер {plate_raw.upper()} зарегистрирован!*\n\n"
            f"📋 Всего номеров: {len(plates)}/{MAX_PLATES_PER_USER}",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(True)
        )
    else:
        await message.answer(
            f"❌ {result}",
            reply_markup=get_main_keyboard(len(get_user_plates(message.from_user.id)) > 0)
        )
    
    await state.clear()

@dp.message(F.text == "🚗 Мои номера")
async def show_my_plates(message: Message):
    plates = get_user_plates(message.from_user.id)
    
    if not plates:
        await message.answer(
            "❌ У вас нет зарегистрированных номеров.\n\n"
            "Нажмите «➕ Зарегистрировать номер»",
            reply_markup=get_main_keyboard(False)
        )
        return
    
    plates_list = []
    for p in plates:
        status = "🔇 выкл" if p['is_blocked'] else "🔔 вкл"
        plates_list.append(f"• `{p['plate_raw']}` — {status}")
    
    await message.answer(
        f"🚗 *Ваши номера:*\n\n" + "\n".join(plates_list) + f"\n\n📌 Всего: {len(plates)}/{MAX_PLATES_PER_USER}",
        parse_mode="Markdown"
    )

@dp.message(F.text == "🔇 Отключить уведомления")
async def disable_notifications(message: Message):
    plates = get_user_plates(message.from_user.id)
    
    if not plates:
        await message.answer("❌ У вас нет зарегистрированных номеров.")
        return
    
    if block_all_notifications(message.from_user.id):
        await message.answer(
            "🔇 Уведомления для ВСЕХ ваших номеров отключены.\n\n"
            "Чтобы снова включить — нажмите «🔔 Включить уведомления»"
        )
    else:
        await message.answer("❌ Ошибка при отключении.")

@dp.message(F.text == "🔔 Включить уведомления")
async def enable_notifications(message: Message):
    plates = get_user_plates(message.from_user.id)
    
    if not plates:
        await message.answer("❌ У вас нет зарегистрированных номеров.")
        return
    
    if unblock_all_notifications(message.from_user.id):
        await message.answer(
            "🔔 Уведомления для ВСЕХ ваших номеров включены.\n\n"
            "Теперь вы снова будете получать сообщения от других водителей."
        )
    else:
        await message.answer("❌ Ошибка при включении.")

@dp.message(F.text == "❌ Удалить номер")
async def delete_plate_menu(message: Message, state: FSMContext):
    plates = get_user_plates(message.from_user.id)
    
    if not plates:
        await message.answer("❌ У вас нет зарегистрированных номеров.")
        return
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=f"🗑 {p['plate_raw']}")] for p in plates] +
                 [[KeyboardButton(text="◀️ Назад")]],
        resize_keyboard=True
    )
    
    await message.answer(
        "🗑 *Выберите номер для удаления:*",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    await DeleteStates.waiting_selection.set()

@dp.message(DeleteStates.waiting_selection, F.text.startswith("🗑 "))
async def process_delete_plate(message: Message, state: FSMContext):
    plate_raw = message.text.replace("🗑 ", "").strip()
    normalized = normalize_plate(plate_raw)
    
    if delete_plate(message.from_user.id, normalized):
        plates_left = get_user_plates(message.from_user.id)
        
        if not plates_left:
            await message.answer(
                f"🗑 Номер {plate_raw} удалён.\n\n"
                f"У вас не осталось зарегистрированных номеров.",
                reply_markup=get_main_keyboard(False)
            )
        else:
            await message.answer(
                f"🗑 Номер {plate_raw} удалён.\n\n"
                f"У вас осталось {len(plates_left)}/{MAX_PLATES_PER_USER} номер(ов).",
                reply_markup=get_main_keyboard(True)
            )
    else:
        await message.answer("❌ Ошибка при удалении.")
    
    await state.clear()

@dp.message(DeleteStates.waiting_selection, F.text == "◀️ Назад")
async def cancel_delete(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🗑 Удаление отменено.",
        reply_markup=get_main_keyboard(True)
    )

@dp.message(DeleteStates.waiting_selection)
async def invalid_delete_input(message: Message):
    await message.answer("❌ Пожалуйста, выберите номер из списка кнопок.")

@dp.message(F.text == "❓ Помощь")
async def show_help(message: Message):
    minutes = ANTISPAM_SECONDS // 60
    help_text = f"""
📖 *Помощь*

🔹 *Как отправить сообщение водителю:*
Просто напишите *4 символа* его номера и через пробел — текст.
Пример: `е617 не закрыт люк`

🔹 *Форматы номеров:*
• 4 цифры: `0496`
• 1 буква + 3 цифры: `Е617`

🔹 *Несколько номеров:*
Можно зарегистрировать до {MAX_PLATES_PER_USER} номеров.

🔹 *Антиспам:*
Одному водителю можно писать не чаще 1 раза в {minutes} минуту.

🔹 *Если получили чужое сообщение:*
⚠️ Просто проигнорируйте — кто-то ошибся номером.
    """
    await message.answer(help_text, parse_mode="Markdown")

# ==================== ОСНОВНАЯ ЛОГИКА ОТПРАВКИ ====================
@dp.message(F.text)
async def handle_message(message: Message):
    ignore_list = [
        "➕ Зарегистрировать номер", "➕ Добавить номер", "🚗 Мои номера",
        "🔇 Отключить уведомления", "🔔 Включить уведомления", 
        "❌ Удалить номер", "❓ Помощь", "◀️ Назад"
    ]
    
    if message.text.startswith('🗑'):
        return
    
    if message.text.startswith('/') or message.text in ignore_list:
        return
    
    text = message.text.strip()
    words = text.split(maxsplit=1)
    
    if len(words) < 2:
        return
    
    plate_part = words[0]
    user_message = words[1]
    
    # Проверка на пустое сообщение
    if not user_message.strip():
        await message.answer("❌ Вы не написали текст сообщения.")
        return
    
    # Проверка длины номера
    if len(plate_part) != 4:
        await message.answer(
            f"⚠️ *Неверный формат номера*\n\n"
            f"Номер должен быть ровно 4 символа.\n"
            f"Вы ввели: `{plate_part}` (длина {len(plate_part)})\n\n"
            f"Примеры: `е617` или `0496`",
            parse_mode="Markdown"
        )
        return
    
    if len(user_message) > 500:
        await message.answer("❌ Сообщение слишком длинное (максимум 500 символов).")
        return
    
    # ШАГ 1: Проверяем, существует ли номер в базе
    plate_record = get_plate_record(plate_part)
    
    if not plate_record:
        await message.answer(
            f"🔍 *Номер не зарегистрирован*\n\n"
            f"Водитель с номером `{plate_part.upper()}` не найден в базе бота.\n\n"
            f"📌 Возможные причины:\n"
            f"• Вы ошиблись в номере\n"
            f"• Водитель ещё не зарегистрировал номер в боте\n\n"
            f"💡 Проверьте номер и попробуйте снова.",
            parse_mode="Markdown"
        )
        return
    
    target_user_id = plate_record['user_id']
    
    # ШАГ 2: Проверяем, отключил ли пользователь уведомления ВНУТРИ БОТА
    if plate_record['is_blocked']:
        await message.answer(
            f"🔇 *Уведомления отключены*\n\n"
            f"Водитель с номером `{plate_part.upper()}` отключил уведомления в настройках бота.\n\n"
            f"📌 Ваше сообщение не будет доставлено.\n\n"
            f"💡 Единственный способ — лично попросить водителя включить уведомления.",
            parse_mode="Markdown"
        )
        return
    
    # ШАГ 3: Проверяем, не пишет ли пользователь сам себе
    if target_user_id == message.from_user.id:
        await message.answer(
            "😅 *Нельзя отправить сообщение самому себе*\n\n"
            "Вы попытались написать на свой собственный номер.\n\n"
            "💡 Если хотите проверить бота — попросите друга зарегистрироваться.",
            parse_mode="Markdown"
        )
        return
    
    # ШАГ 4: АНТИСПАМ
    if not can_send_message(message.from_user.id, target_user_id, cooldown_seconds=ANTISPAM_SECONDS):
        minutes = ANTISPAM_SECONDS // 60
        await message.answer(
            f"⏳ *Антиспам*\n\n"
            f"Вы уже отправляли сообщение водителю `{plate_part.upper()}` за последние {minutes} минуту.\n\n"
            f"📌 Пожалуйста, подождите перед следующей отправкой.",
            parse_mode="Markdown"
        )
        return
    
    # ШАГ 5: Отправляем сообщение
    notification_text = (
        f"⚠️ *Вам сообщение*\n\n"
        f"📝 {user_message}\n\n"
        f"—\n"
        f"ℹ️ Если вы не владелец номера {plate_part.upper()} — просто проигнорируйте."
    )
    
    try:
        await bot.send_message(
            chat_id=target_user_id,
            text=notification_text,
            parse_mode="Markdown"
        )
        
        log_message(
            from_user_id=message.from_user.id,
            to_user_id=target_user_id,
            to_plate=plate_part.upper(),
            message_text=user_message
        )
        
        minutes = ANTISPAM_SECONDS // 60
        await message.answer(
            f"✅ *Сообщение отправлено!*\n\n"
            f"Владельцу номера `{plate_part.upper()}` передано:\n"
            f"«{user_message[:100]}»\n\n"
            f"ℹ️ Следующее сообщение этому водителю можно отправить через {minutes} минуту.",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        await message.answer(
            f"❌ *Не удалось доставить сообщение*\n\n"
            f"Водитель с номером `{plate_part.upper()}` зарегистрирован, но сообщение не доставлено.\n\n"
            f"📌 *Что известно:*\n"
            f"✅ Номер зарегистрирован в боте\n"
            f"✅ Уведомления в боте включены\n\n"
            f"🤔 Возможно, пользователь заблокировал бота в настройках Telegram\n\n"
            f"💡 Попробуйте связаться с водителем другим способом.",
            parse_mode="Markdown"
        )
        print(f"Ошибка отправки пользователю {target_user_id}: {e}")

# ==================== ЗАПУСК ====================
async def main():
    init_db()
    await set_main_menu()
    print("✅ Бот запущен")
    print(f"👤 Администратор: {ADMIN_ID}")
    print(f"📌 Максимум номеров на пользователя: {MAX_PLATES_PER_USER}")
    print(f"⏱️ Антиспам: {ANTISPAM_SECONDS} секунд")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())