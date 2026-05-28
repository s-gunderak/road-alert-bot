import sqlite3

DB_NAME = "drivers.db"

def init_db():
    """Создаёт таблицу пользователей, если её нет"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            plate_prefix TEXT NOT NULL UNIQUE
        )
    """)
    conn.commit()
    conn.close()

def register_user(telegram_id: int, plate_prefix: str):
    """Регистрирует пользователя с первыми 4 символами номера"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT OR REPLACE INTO users (telegram_id, plate_prefix) VALUES (?, ?)",
            (telegram_id, plate_prefix.upper())
        )
        conn.commit()
        success = True
    except Exception as e:
        print("DB error:", e)
        success = False
    finally:
        conn.close()
    return success

def get_telegram_id_by_plate(plate_prefix: str):
    """Ищет Telegram ID по первым 4 символам номера"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "SELECT telegram_id FROM users WHERE plate_prefix = ?",
        (plate_prefix.upper(),)
    )
    result = cur.fetchone()
    conn.close()
    return result[0] if result else None

def get_user_plate(telegram_id: int):
    """Узнать свой номер по Telegram ID"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "SELECT plate_prefix FROM users WHERE telegram_id = ?",
        (telegram_id,)
    )
    result = cur.fetchone()
    conn.close()
    return result[0] if result else None