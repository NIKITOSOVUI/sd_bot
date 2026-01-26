import sqlite3
import datetime

DB_FILE = "bot.db"


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute('''CREATE TABLE IF NOT EXISTS users
                   (user_id TEXT PRIMARY KEY, phone TEXT)''')

    cur.execute('''CREATE TABLE IF NOT EXISTS orders
                   (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_text TEXT,
                    order_time TEXT,
                    phone TEXT,
                    address TEXT,
                    username TEXT,
                    comment TEXT)''')

    # Добавляем новые колонки, если их нет
    def column_exists(table, column):
        cur.execute(f"PRAGMA table_info({table})")
        return any(c[1] == column for c in cur.fetchall())

    if not column_exists('orders', 'delivery_type'):
        cur.execute("ALTER TABLE orders ADD COLUMN delivery_type TEXT")

    if not column_exists('orders', 'delivery_address'):
        cur.execute("ALTER TABLE orders ADD COLUMN delivery_address TEXT")

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


def append_order(order_text, phone=None, delivery_type=None, delivery_address=None, comment=None, username=None):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    cur.execute("""INSERT INTO orders 
                   (order_text, order_time, phone, delivery_type, delivery_address, comment, username) 
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (order_text, now, phone, delivery_type, delivery_address, comment, username))
    conn.commit()
    conn.close()


def read_users():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT user_id, phone FROM users")
    users = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()
    return users


def write_users(users_dict):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("DELETE FROM users")
    for user_id, phone in users_dict.items():
        cur.execute("INSERT INTO users (user_id, phone) VALUES (?, ?)", (user_id, phone))
    conn.commit()
    conn.close()


def get_orders_filtered(period=None, date_from=None, date_to=None, limit=1000):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    query = """SELECT order_text, order_time, phone, delivery_address, username, comment, delivery_type 
               FROM orders"""
    params = []

    where_clauses = []

    if period == "today":
        today = datetime.date.today().strftime("%d.%m.%Y")
        where_clauses.append("order_time LIKE ?")
        params.append(f"{today}%")
    elif period == "3days":
        since = (datetime.date.today() - datetime.timedelta(days=3)).strftime("%d.%m.%Y")
        where_clauses.append("order_time >= ?")
        params.append(f"{since} 00:00")
    elif period == "week":
        since = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%d.%m.%Y")
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

    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    cur.execute(query, params)
    rows = cur.fetchall()

    orders = []
    for row in rows:
        text, time_str, phone, delivery_address, username, comment, delivery_type = row
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
            "delivery_type": delivery_type
        })

    conn.close()
    return orders