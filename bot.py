import asyncio
import os
import threading
from typing import Optional, Dict, Any, List
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, BotCommand, ReplyKeyboardMarkup, KeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from supabase import create_client, Client
from flask import Flask

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8874167673:AAFqQ8QFbqZAYK-j0uhDCfywmuuIlTSrZdk")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")  # Вставь свой URL
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")  # Вставь свой anon key
ADMIN_ID = 644509266
MAX_PLATES_PER_USER = 3
ANTISPAM_SECONDS = 60

# ==================== ИНИЦИАЛИЗАЦИЯ SUPABASE ====================
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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

# ==================== ФУНКЦИИ РАБОТЫ С БАЗОЙ ====================
def register_plate(user_id: int, username: str, full_name: str, plate_raw: str) -> tuple[bool, str]:
    normalized = normalize_plate(plate_raw)
    
    if not validate_plate_format(normalized):
        return False, "❌ Неверный формат. Нужно 4 символа: 4 цифры (0496) или буква+3 цифры (Е617)"
    
    # Проверка лимита номеров
    user_plates = supabase.table("user_plates").select("*", count="exact").eq("user_id", user_id).execute()
    if len(user_plates.data) >= MAX_PLATES_PER_USER:
        return False, f"⚠️ У вас уже зарегистрировано {MAX_PLATES_PER_USER} номеров."
    
    # Проверка, не занят ли номер
    existing = supabase.table("user_plates").select("*").eq("plate_normalized", normalized).execute()
    if existing.data:
        return False, "⚠️ Этот номер уже зарегистрирован другим водителем."
    
    # Проверка, не регистрировал ли пользователь уже этот номер
    user_has = supabase.table("user_plates").select("*").eq("user_id", user_id).eq("plate_normalized", normalized).execute()
    if user_has.data:
        return False, "⚠️ Вы уже регистрировали этот номер."
    
    try:
        supabase.table("user_plates").insert({
            "user_id": user_id,
            "username": username,
            "full_name": full_name,
            "plate_normalized": normalized,
            "plate_raw": plate_raw.upper(),
            "is_blocked": 0
        }).execute()
        return True, normalized
    except Exception as e:
        return False, f"❌ Ошибка при регистрации: {e}"

def get_plate_record(plate: str) -> Optional[Dict[str, Any]]:
    normalized = normalize_plate(plate)
    if len(normalized) != 4:
        return None
    
    result = supabase.table("user_plates").select("user_id, plate_raw, is_blocked").eq("plate_normalized", normalized).execute()
    if result.data:
        return result.data[0]
    return None

def get_user_plates(user_id: int) -> List[Dict[str, Any]]:
    result = supabase.table("user_plates").select("*").eq("user_id", user_id).order("registered_at").execute()
    return result.data

def delete_plate(user_id: int, plate_normalized: str) -> bool:
    result = supabase.table("user_plates").delete().eq("user_id", user_id).eq("plate_normalized", plate_normalized).execute()
    return len(result.data) > 0

def block_all_notifications(user_id: int) -> bool:
    result = supabase.table("user_plates").update({"is_blocked": 1}).eq("user_id", user_id).execute()
    return len(result.data) > 0

def unblock_all_notifications(user_id: int) -> bool:
    result = supabase.table("user_plates").update({"is_blocked": 0}).eq("user_id", user_id).execute()
    return len(result.data) > 0

def can_send_message(from_user_id: int, to_user_id: int, cooldown_seconds: int = ANTISPAM_SECONDS) -> bool:
    result = supabase.table("message_log").select("*", count="exact").eq("from_user_id", from_user_id).eq("to_user_id", to_user_id).execute()
    
    if not result.data:
        return True
    
    # Проверяем время последнего сообщения
    last_message = result.data[-1]
    last_time = datetime.fromisoformat(last_message['sent_at'].replace('Z', '+00:00'))
    now = datetime.now(last_time.tzinfo)
    diff = (now - last_time).total_seconds()
    
    return diff >= cooldown_seconds

def log_message(from_user_id: int, to_user_id: int, to_plate: str, message_text: str):
    supabase.table("message_log").insert({
        "from_user_id": from_user_id,
        "to_user_id": to_user_id,
        "to_plate": to_plate,
        "message_text": message_text[:500]
    }).execute()

def get_stats() -> Dict[str, int]:
    users_result = supabase.table("user_plates").select("user_id", count="exact").execute()
    plates_result = supabase.table("user_plates").select("*", count="exact").execute()
    messages_result = supabase.table("message_log").select("*", count="exact").execute()
    
    # Уникальные пользователи
    unique_users = len(set([u['user_id'] for u in users_result.data]))
    
    return {
        "users": unique_users,
        "plates": plates_result.count,
        "messages": messages_result.count
    }

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

# Flask для health check
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    return "Бот работает!", 200

def run_flask():
    flask_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

async def set_main_menu():
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
            f"📌 Вы можете зарегистрировать до {MAX_PLATES_PER_USER} номеров.\n\n"
            f"👇 Нажмите «➕ Зарегистрировать номер»",
            reply_markup=get_main_keyboard(False)
        )

@dp.message(Command("stats"))
async def show_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
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
        f"📌 Максимум номеров: {MAX_PLATES_PER_USER}\n\n"
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
            f"⚠️ У вас уже зарегистрировано максимальное количество номеров ({MAX_PLATES_PER_USER}).",
            reply_markup=get_main_keyboard(True)
        )
        return
    
    await state.set_state(RegisterStates.waiting_for_plate)
    await message.answer(
        "🚗 *Добавление номера*\n\n"
        "Введите *первые 4 символа* другого номера.\n\n"
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
            "Чтобы включить — нажмите «🔔 Включить уведомления»"
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
            "🔔 Уведомления для ВСЕХ ваших номеров включены."
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
                f"🗑 Номер {plate_raw} удалён.",
                reply_markup=get_main_keyboard(False)
            )
        else:
            await message.answer(
                f"🗑 Номер {plate_raw} удалён.\n\n"
                f"Осталось {len(plates_left)}/{MAX_PLATES_PER_USER} номеров.",
                reply_markup=get_main_keyboard(True)
            )
    else:
        await message.answer("❌ Ошибка при удалении.")
    
    await state.clear()

@dp.message(DeleteStates.waiting_selection, F.text == "◀️ Назад")
async def cancel_delete(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🗑 Удаление отменено.", reply_markup=get_main_keyboard(True))

@dp.message(DeleteStates.waiting_selection)
async def invalid_delete_input(message: Message):
    await message.answer("❌ Выберите номер из списка.")

@dp.message(F.text == "❓ Помощь")
async def show_help(message: Message):
    minutes = ANTISPAM_SECONDS // 60
    help_text = f"""
📖 *Помощь*

🔹 *Как отправить сообщение водителю:*
Напишите *4 символа* номера и через пробел — текст.
Пример: `е617 не закрыт люк`

🔹 *Форматы номеров:*
• 4 цифры: `0496`
• 1 буква + 3 цифры: `Е617`

🔹 *Максимум номеров:* {MAX_PLATES_PER_USER}

🔹 *Антиспам:* {minutes} минута между сообщениями одному водителю

🔹 *Если получили чужое сообщение:* Просто проигнорируйте.
    """
    await message.answer(help_text, parse_mode="Markdown")

# ==================== ОСНОВНАЯ ЛОГИКА ====================
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
    
    if not user_message.strip():
        await message.answer("❌ Вы не написали текст сообщения.")
        return
    
    if len(plate_part) != 4:
        await message.answer(
            f"⚠️ *Неверный формат номера*\n\n"
            f"Номер должен быть ровно 4 символа.\n"
            f"Вы ввели: `{plate_part}`\n\n"
            f"Примеры: `е617` или `0496`",
            parse_mode="Markdown"
        )
        return
    
    if len(user_message) > 500:
        await message.answer("❌ Сообщение слишком длинное (максимум 500 символов).")
        return
    
    plate_record = get_plate_record(plate_part)
    
    if not plate_record:
        await message.answer(
            f"🔍 *Номер не зарегистрирован*\n\n"
            f"Водитель с номером `{plate_part.upper()}` не найден.\n\n"
            f"📌 Возможные причины:\n"
            f"• Вы ошиблись в номере\n"
            f"• Водитель ещё не зарегистрировался",
            parse_mode="Markdown"
        )
        return
    
    target_user_id = plate_record['user_id']
    
    if plate_record['is_blocked']:
        await message.answer(
            f"🔇 *Уведомления отключены*\n\n"
            f"Водитель `{plate_part.upper()}` отключил уведомления.\n"
            f"Сообщение не будет доставлено.",
            parse_mode="Markdown"
        )
        return
    
    if target_user_id == message.from_user.id:
        await message.answer(
            "😅 *Нельзя отправить сообщение самому себе*",
            parse_mode="Markdown"
        )
        return
    
    if not can_send_message(message.from_user.id, target_user_id):
        minutes = ANTISPAM_SECONDS // 60
        await message.answer(
            f"⏳ *Антиспам*\n\n"
            f"Вы уже писали водителю `{plate_part.upper()}` за последние {minutes} минуту.\n"
            f"Подождите.",
            parse_mode="Markdown"
        )
        return
    
    try:
        await bot.send_message(
            chat_id=target_user_id,
            text=f"⚠️ *Вам сообщение*\n\n📝 {user_message}\n\n—\nℹ️ Если вы не владелец номера {plate_part.upper()} — проигнорируйте.",
            parse_mode="Markdown"
        )
        
        log_message(message.from_user.id, target_user_id, plate_part.upper(), user_message)
        
        minutes = ANTISPAM_SECONDS // 60
        await message.answer(
            f"✅ *Сообщение отправлено!*\n\n"
            f"Владельцу `{plate_part.upper()}` передано:\n"
            f"«{user_message[:100]}»\n\n"
            f"ℹ️ Следующее сообщение — через {minutes} минуту.",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        await message.answer(
            f"❌ *Не удалось доставить*\n\n"
            f"Водитель `{plate_part.upper()}` зарегистрирован, но не получает сообщения.\n"
            f"🤔 Возможно, он заблокировал бота.",
            parse_mode="Markdown"
        )
        print(f"Ошибка: {e}")

# ==================== ЗАПУСК ====================
async def main():
    await set_main_menu()
    print("✅ Бот запущен с Supabase")
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Запускаем Flask для health check
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())