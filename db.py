import sqlite3
import datetime
import os
import json

DB_FILE = os.getenv("DB_FILE_PATH", "bot.db")

# Часовой пояс ресторана: UTC+8 (Иркутск)
LOCAL_TZ_OFFSET = datetime.timedelta(hours=8)

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute('''CREATE TABLE IF NOT EXISTS users
                   (user_id TEXT PRIMARY KEY, phone TEXT, addresses TEXT)''')  # Добавлена колонка addresses

    cur.execute('''CREATE TABLE IF NOT EXISTS orders
                   (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_text TEXT,
                    order_time TEXT,
                    phone TEXT,
                    address TEXT,
                    username TEXT,
                    comment TEXT,
                    delivery_type TEXT,
                    delivery_address TEXT)''')

    def column_exists(table, column):
        cur.execute(f"PRAGMA table_info({table})")
        return any(c[1] == column for c in cur.fetchall())

    if not column_exists('orders', 'prep_time'):
        cur.execute("ALTER TABLE orders ADD COLUMN prep_time TEXT")

    if not column_exists('orders', 'delivery_cost'):
        cur.execute("ALTER TABLE orders ADD COLUMN delivery_cost INTEGER DEFAULT 0")

    if not column_exists('orders', 'payment_method'):
        cur.execute("ALTER TABLE orders ADD COLUMN payment_method TEXT")

    if not column_exists('orders', 'cash_amount'):
        cur.execute("ALTER TABLE orders ADD COLUMN cash_amount INTEGER")

    if not column_exists('users', 'addresses'):
        cur.execute("ALTER TABLE users ADD COLUMN addresses TEXT DEFAULT '[]'")

    cur.execute('''CREATE TABLE IF NOT EXISTS categories
                   (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE)''')

    cur.execute('''CREATE TABLE IF NOT EXISTS menu_items
                   (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category_id INTEGER,
                    name TEXT,
                    price TEXT,
                    desc TEXT,
                    FOREIGN KEY (category_id) REFERENCES categories (id))''')

    conn.commit()
    conn.close()


def get_user_addresses(user_id: str):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT addresses FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if row and row[0]:
        return json.loads(row[0])
    return []


def save_user_phone(user_id: str, phone: str):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users (user_id, phone) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET phone = excluded.phone
    """, (user_id, phone))
    conn.commit()
    conn.close()


def save_user_addresses(user_id: str, addresses: list):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    addresses_json = json.dumps(addresses, ensure_ascii=False)
    cur.execute("""
        INSERT INTO users (user_id, addresses) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET addresses = excluded.addresses
    """, (user_id, addresses_json))
    conn.commit()
    conn.close()


def read_menu():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    menu_list = []

    cur.execute("SELECT id, name FROM categories ORDER BY id")
    categories = cur.fetchall()

    for cat_id, cat_name in categories:
        cur.execute("SELECT name, price, desc FROM menu_items WHERE category_id = ? ORDER BY id", (cat_id,))
        items = [{"name": row[0], "price": row[1], "desc": row[2] if row[2] else ""} for row in cur.fetchall()]
        menu_list.append({"category": cat_name, "items": items})

    conn.close()
    return menu_list


def write_menu(menu_list):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("DELETE FROM menu_items")
    cur.execute("DELETE FROM categories")

    for cat_dict in menu_list:
        cur.execute("INSERT INTO categories (name) VALUES (?)", (cat_dict["category"],))
        cat_id = cur.lastrowid
        for item in cat_dict["items"]:
            cur.execute("INSERT INTO menu_items (category_id, name, price, desc) VALUES (?, ?, ?, ?)",
                        (cat_id, item["name"], item["price"], item.get("desc", "")))

    conn.commit()
    conn.close()


def append_order(order_text: str, phone: str, delivery_type: str, delivery_address: str,
                comment: str = "Без комментария", username: str = "Скрыт",
                prep_time: str = "Не указано", delivery_cost: int = 0,
                payment_method: str = "Не указано", cash_amount: int | None = None,
                user_id: str | None = None):  # новый параметр
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO orders 
        (order_text, phone, delivery_type, delivery_address, comment, username, 
         prep_time, delivery_cost, payment_method, cash_amount, user_id, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    ''', (
        order_text, phone, delivery_type, delivery_address, comment, username,
        prep_time, delivery_cost, payment_method, cash_amount, user_id
    ))
    
    conn.commit()
    conn.close()


def read_users():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT user_id, phone FROM users")
    users = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()
    return users


def get_orders_filtered(period=None, date_from=None, date_to=None, limit=1000):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    query = """SELECT order_text, order_time, phone, delivery_address, username, comment, delivery_type, prep_time, delivery_cost, payment_method, cash_amount 
               FROM orders"""
    params = []

    where_clauses = []

    if period == "today":
        today = (datetime.datetime.utcnow() + LOCAL_TZ_OFFSET).strftime("%d.%m.%Y")
        where_clauses.append("order_time LIKE ?")
        params.append(f"{today}%")
    elif period == "3days":
        since = (datetime.datetime.utcnow() + LOCAL_TZ_OFFSET - datetime.timedelta(days=3)).strftime("%d.%m.%Y")
        where_clauses.append("order_time >= ?")
        params.append(f"{since} 00:00")
    elif period == "week":
        since = (datetime.datetime.utcnow() + LOCAL_TZ_OFFSET - datetime.timedelta(days=7)).strftime("%d.%m.%Y")
        where_clauses.append("order_time >= ?")
        params.append(f"{since} 00:00")

    if date_from and date_to:
        where_clauses.append("order_time BETWEEN ? AND ?")
        params.extend([f"{date_from} 00:00", f"{date_to} 23:59"])
    elif date_from:
        where_clauses.append("order_time >= ?")
        params.append(f"{date_from} 00:00")
    elif date_to:
        where_clauses.append("order_time <= ?")
        params.append(f"{date_to} 23:59")

    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)

    query += " ORDER BY id ASC LIMIT ?"
    params.append(limit)

    cur.execute(query, params)
    rows = cur.fetchall()

    orders = []
    for row in rows:
        text, time_str, phone, delivery_address, username, comment, delivery_type, prep_time, delivery_cost, payment_method, cash_amount = row
        dt = None
        try:
            dt = datetime.datetime.strptime(time_str, "%d.%m.%Y %H:%M")
        except:
            pass
        orders.append({
            "text": text.strip() if text else "",
            "time": time_str,
            "datetime": dt,
            "phone": phone,
            "delivery_address": delivery_address,
            "username": username,
            "comment": comment,
            "delivery_type": delivery_type,
            "prep_time": prep_time or "Не указано",
            "delivery_cost": delivery_cost or 0,
            "payment_method": payment_method or "Не указано",
            "cash_amount": cash_amount
        })

    conn.close()
    return orders

def get_user_orders(user_id: str):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT prep_time, order_text, datetime(timestamp, '+8 hours') as local_time
        FROM orders
        WHERE user_id = ?
        ORDER BY timestamp DESC
        LIMIT 10
    """, (user_id,))
    rows = cursor.fetchall()
    orders = []
    for row in rows:
        timestamp_str = "Неизвестно"
        if row[2]:  # если timestamp не NULL
            try:
                # ← ИСПРАВЛЕНО: datetime.datetime.strptime
                timestamp_dt = datetime.datetime.strptime(row[2], "%Y-%m-%d %H:%M:%S")
                timestamp_str = timestamp_dt.strftime("%d.%m.%Y %H:%M")
            except ValueError:
                timestamp_str = "Ошибка формата"

        orders.append({
            'prep_time': row[0] or "Не указано",
            'order_text': row[1],
            'timestamp': timestamp_str
        })
    conn.close()
    return orders


def migrate_db():
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    
    # Получаем список существующих колонок
    cursor.execute("PRAGMA table_info(orders)")
    columns = [info[1] for info in cursor.fetchall()]
    
    # Добавляем timestamp БЕЗ дефолта (он будет заполняться явно в INSERT)
    if 'timestamp' not in columns:
        cursor.execute("ALTER TABLE orders ADD COLUMN timestamp DATETIME")
        print("Добавлена колонка timestamp в таблицу orders (без дефолта)")
    
    # Добавляем user_id БЕЗ дефолта
    if 'user_id' not in columns:
        cursor.execute("ALTER TABLE orders ADD COLUMN user_id TEXT")
        print("Добавлена колонка user_id в таблицу orders")
    
    conn.commit()
    conn.close()