from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

from db import read_menu


# Клавиатура запроса номера телефона
phone_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📱 Отправить номер телефона", request_contact=True)]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)


# Клавиатура категорий (без эмодзи)
def categories_kb(cart_count: int = 0):
    menu = read_menu()
    kb = []

    row = []
    for cat_dict in menu:
        category = cat_dict["category"]
        button = InlineKeyboardButton(text=category, callback_data=f"user_cat_{category}")
        row.append(button)

        if len(row) == 2:
            kb.append(row)
            row = []

    if row:
        kb.append(row)

    cart_text = f"🛒 Корзина ({cart_count})" if cart_count > 0 else "🛒 Корзина"
    kb.append([InlineKeyboardButton(text=cart_text, callback_data="user_cart")])

    return InlineKeyboardMarkup(inline_keyboard=kb)


# Клавиатура блюд в категории
def category_kb(items: list):
    kb = []

    for idx, item in enumerate(items):
        text = f"{item['name']} — {item['price']} ₽"
        button = InlineKeyboardButton(text=text, callback_data=f"user_add_{idx}")
        kb.append([button])

    kb.append([
        InlineKeyboardButton(text="← Назад к категориям", callback_data="user_back_to_categories"),
        InlineKeyboardButton(text="🛒 Корзина", callback_data="user_cart")
    ])

    return InlineKeyboardMarkup(inline_keyboard=kb)


# Клавиатура корзины
def cart_kb():
    kb = [
        [
            InlineKeyboardButton(text="✅ Оформить заказ", callback_data="user_checkout"),
            InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="user_clear_cart")
        ],
        [InlineKeyboardButton(text="← К меню", callback_data="user_back_to_categories")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


# Админ-клавиатуры
def admin_main_kb():
    kb = [
        [InlineKeyboardButton(text="📋 Просмотреть меню", callback_data="admin_view_menu")],
        [InlineKeyboardButton(text="➕ Добавить категорию", callback_data="admin_add_category")],
        [InlineKeyboardButton(text="➖ Удалить категорию", callback_data="admin_delete_category")],
        [InlineKeyboardButton(text="➕ Добавить блюдо", callback_data="admin_add_dish")],
        [InlineKeyboardButton(text="➖ Удалить блюдо", callback_data="admin_delete_dish")],
        [InlineKeyboardButton(text="📦 Просмотреть заказы", callback_data="admin_view_orders")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_categories_kb(action_prefix: str, include_new: bool = False):
    menu = read_menu()
    kb = []
    row = []

    for cat_dict in menu:
        cat_name = cat_dict["category"]
        button = InlineKeyboardButton(text=cat_name, callback_data=f"admin_{action_prefix}{cat_name}")
        row.append(button)

        if len(row) == 2:
            kb.append(row)
            row = []

    if row:
        kb.append(row)

    if include_new:
        kb.append([InlineKeyboardButton(text="➕ Новая категория", callback_data=f"admin_{action_prefix}new")])

    kb.append([InlineKeyboardButton(text="⬅ Назад в админ-панель", callback_data="admin_back")])

    return InlineKeyboardMarkup(inline_keyboard=kb)