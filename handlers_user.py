from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, Contact, ReplyKeyboardRemove, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.filters.logic import or_f
from aiogram.fsm.context import FSMContext
from db import read_menu, append_order, read_users, write_users, get_user_addresses, save_user_addresses
from keyboards import phone_kb, categories_kb, category_kb, cart_kb
from states import UserStates
from config import WELCOME_PHOTO_PATH
import datetime
from collections import defaultdict

router = Router()

PICKUP_ADDRESS = "Братск, Центральный р-н, ул. Коммунальная, 15Б"

# Часовой пояс ресторана: UTC+8 (Иркутск)
LOCAL_TZ_OFFSET = datetime.timedelta(hours=8)

# Рабочее время: 9:00 - 21:00 местного
RESTAURANT_OPEN = datetime.time(9, 0)
RESTAURANT_CLOSE = datetime.time(21, 0)

# Время для заказов: с 10:00 до 20:30 шаг 30 мин
ORDER_START_HOUR = 10
ORDER_END_HOUR = 20
TIME_STEP_MINUTES = 30

# Стоимость доставки
FREE_DELIVERY_MIN = 1500
DELIVERY_COST = 250
MIN_ORDER_FOR_DELIVERY = 300


def generate_time_options():
    utc_now = datetime.datetime.utcnow()
    local_now = utc_now + LOCAL_TZ_OFFSET
    local_time = local_now.time()
    local_date = local_now.date()

    # Определяем базу: если после 21:00 — завтра, иначе сегодня
    if local_time >= RESTAURANT_CLOSE:
        base_date = local_date + datetime.timedelta(days=1)
    else:
        base_date = local_date

    # Минимальное время: +30 мин от текущего, округление вверх до 30 мин
    min_time = local_now + datetime.timedelta(minutes=30)
    min_time = min_time.replace(second=0, microsecond=0)

    extra_minutes = min_time.minute % TIME_STEP_MINUTES
    if extra_minutes > 0:
        min_time += datetime.timedelta(minutes=TIME_STEP_MINUTES - extra_minutes)

    if min_time.minute == 60:
        min_time = min_time.replace(minute=0) + datetime.timedelta(hours=1)

    # Если min_time раньше 10:00 сегодня — 10:00 сегодня
    if base_date == local_date and min_time.time() < datetime.time(ORDER_START_HOUR, 0):
        min_time = min_time.replace(hour=ORDER_START_HOUR, minute=0)

    # Если min_time после 20:30 — переносим на завтра 10:00
    if min_time.time() > datetime.time(ORDER_END_HOUR, 30):
        min_time = datetime.datetime.combine(local_date + datetime.timedelta(days=1), datetime.time(ORDER_START_HOUR, 0))

    # Генерируем слоты начиная от min_time до 20:30
    options = []
    current = min_time
    end_time = datetime.datetime.combine(current.date(), datetime.time(ORDER_END_HOUR, 30))

    while current <= end_time:
        time_str = current.strftime("%d.%m.%Y %H:%M")
        label = current.strftime("%H:%M")
        if current.date() > local_date:
            label += " (завтра)"
        options.append((label, time_str))
        current += datetime.timedelta(minutes=TIME_STEP_MINUTES)

    return options


def get_restaurant_status_text():
    utc_now = datetime.datetime.utcnow()
    local_now = utc_now + LOCAL_TZ_OFFSET
    local_time = local_now.time()

    if RESTAURANT_OPEN <= local_time < RESTAURANT_CLOSE:
        return "🟢 Ресторан сейчас открыт (до 21:00)"
    else:
        next_date = (local_now + datetime.timedelta(days=1)).strftime("%d.%m") if local_time >= RESTAURANT_CLOSE else local_now.strftime("%d.%m")
        return f"🔴 Ресторан сейчас закрыт (откроется в 9:00). Заказ будет оформлен на {next_date}."


async def show_categories(msg_or_cb, state: FSMContext):
    data = await state.get_data()
    cart = data.get("cart", [])
    kb = categories_kb(len(cart))
    text = "🍲 <b>Сытный Дом</b>\n\nВыберите категорию меню:"

    if isinstance(msg_or_cb, CallbackQuery):
        await msg_or_cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await msg_or_cb.answer(text, reply_markup=kb, parse_mode="HTML")


# Глобальная блокировка любых команд (начинающихся с "/") во время оформления заказа
@router.message(F.text.startswith("/"), or_f(
    UserStates.waiting_delivery_type,
    UserStates.waiting_address_choice,
    UserStates.waiting_address,
    UserStates.waiting_prep_time,
    UserStates.waiting_payment_method,
    UserStates.waiting_cash_amount,
    UserStates.waiting_comment
))
async def block_commands_during_order(message: Message):
    await message.answer("Во время оформления заказа команды запрещены. Завершите заказ или введите «отмена» для отмены.")


# Глобальная отмена по слову "отмена" на всех этапах оформления
@router.message(or_f(
    UserStates.waiting_delivery_type,
    UserStates.waiting_address_choice,
    UserStates.waiting_address,
    UserStates.waiting_prep_time,
    UserStates.waiting_payment_method,
    UserStates.waiting_cash_amount,
    UserStates.waiting_comment
), F.text.lower() == "отмена")
async def cancel_by_text(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Оформление заказа отменено.", reply_markup=ReplyKeyboardRemove())
    await show_categories(message, state)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    current_state = await state.get_state()

    if current_state:
        await message.answer("Вы сейчас оформляете заказ. Завершите оформление или введите «отмена» для отмены.")
        return

    user_id = str(message.from_user.id)
    users = read_users()

    if WELCOME_PHOTO_PATH:
        try:
            if WELCOME_PHOTO_PATH.startswith(("http://", "https://")):
                if WELCOME_PHOTO_PATH.startswith("http://"):
                    print(f"Ошибка: http URL не поддерживается Telegram. Используйте https.")
                else:
                    await message.answer_photo(photo=WELCOME_PHOTO_PATH)
            else:
                photo = FSInputFile(WELCOME_PHOTO_PATH)
                await message.answer_photo(photo=photo)
        except FileNotFoundError:
            print(f"Файл фото не найден: {WELCOME_PHOTO_PATH}")
        except Exception as e:
            print(f"Ошибка отправки фото: {e}")

    if user_id in users:
        await state.update_data(phone=users[user_id], cart=[])
        await show_categories(message, state)
    else:
        await message.answer(
            "Добро пожаловать! 🍲\nДля заказа авторизуйтесь по номеру телефона.",
            reply_markup=phone_kb
        )
        await state.set_state(UserStates.waiting_phone)


@router.message(F.contact, UserStates.waiting_phone)
async def get_phone(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    user_id = str(message.from_user.id)
    
    users = read_users()
    users[user_id] = phone
    write_users(users)
    
    await state.update_data(phone=phone, cart=[])
    await message.answer(
        f"Спасибо! Номер сохранён: {phone}\nТеперь выбирайте блюда 👇",
        reply_markup=ReplyKeyboardRemove()
    )
    await show_categories(message, state)


@router.callback_query(F.data == "user_back_to_categories")
async def back_to_categories(callback: CallbackQuery, state: FSMContext):
    await show_categories(callback, state)


@router.callback_query(F.data.startswith("user_cat_"))
async def select_category(callback: CallbackQuery, state: FSMContext):
    category = callback.data[len("user_cat_"):]
    
    menu_list = read_menu()
    items = None
    for cat_dict in menu_list:
        if cat_dict["category"] == category:
            items = cat_dict["items"]
            break

    if not items:
        await callback.answer("Категория пустая")
        return

    await state.update_data(current_category=category, current_items=items)

    text = f"<b>{category}</b>\n\n\n"

    for num, item in enumerate(items, 1):
        desc = f"\n{item.get('desc', '')}" if item.get('desc') else ""
        text += f"{num}. <b>{item['name']}</b> — {item['price']} ₽{desc}\n\n"

    kb = category_kb(items)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("user_add_"))
async def add_to_cart(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    current_items = data.get("current_items")

    if not current_items:
        await callback.answer("Ошибка: категория не найдена.", show_alert=True)
        return

    try:
        index = int(callback.data[len("user_add_"):])
    except:
        await callback.answer("Ошибка добавления")
        return

    if index >= len(current_items):
        await callback.answer("Блюдо не найдено")
        return

    item = current_items[index]
    category = data.get("current_category", "Неизвестная категория")

    cart = data.get("cart", [])
    cart.append({**item, "category": category})
    await state.update_data(cart=cart)

    await callback.answer(f"Добавлено: {item['name']}")


@router.callback_query(F.data == "user_cart")
async def show_cart(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cart = data.get("cart", [])
    if not cart:
        await callback.answer("Корзина пуста", show_alert=True)
        return

    grouped = defaultdict(list)
    total = 0
    for citem in cart:
        grouped[citem["category"]].append(citem)
        total += int(citem["price"])

    text = "🛒 <b>Ваша корзина</b>\n\n"

    for cat, citems in grouped.items():
        text += f"<b>{cat}</b>\n"

        for item in citems:
            desc = item.get('desc', '').strip()
            text += f"• {item['name']} — {item['price']} ₽\n"
            if desc:
                text += f"  {desc}\n"

        text += "\n"

    text += f"<b>Итого: {total} ₽</b>\n\n"
    text += "<i>Доставка бесплатная от 1500 ₽ или 250 рублей при заказе от 300 до 1500 ₽</i>"

    await callback.message.edit_text(text, reply_markup=cart_kb(), parse_mode="HTML")


@router.callback_query(F.data == "user_checkout")
async def checkout(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cart = data.get("cart", [])
    total = sum(int(item["price"]) for item in cart)

    if total < MIN_ORDER_FOR_DELIVERY:
        await callback.answer(f"Минимальная сумма заказа для доставки — {MIN_ORDER_FOR_DELIVERY} ₽", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚚 Доставка", callback_data="delivery_type_delivery")],
        [InlineKeyboardButton(text="🏃 Самовывоз", callback_data="delivery_type_pickup")],
        [InlineKeyboardButton(text="← Назад", callback_data="user_cart")]
    ])
    await callback.message.edit_text("Выберите способ получения заказа:", reply_markup=kb)
    await state.set_state(UserStates.waiting_delivery_type)


@router.callback_query(F.data.startswith("delivery_type_"))
async def process_delivery_type(callback: CallbackQuery, state: FSMContext):
    delivery_type = callback.data[len("delivery_type_"):]

    data = await state.get_data()
    total = sum(int(item["price"]) for item in data.get("cart", []))

    delivery_cost = 0
    if delivery_type == "delivery":
        if total >= FREE_DELIVERY_MIN:
            delivery_cost = 0
        else:
            delivery_cost = DELIVERY_COST

    await state.update_data(delivery_type=delivery_type, delivery_cost=delivery_cost)

    if delivery_type == "delivery":
        user_id = str(callback.from_user.id)
        addresses = get_user_addresses(user_id)

        kb_rows = []
        for addr in addresses:
            kb_rows.append([InlineKeyboardButton(text=addr, callback_data=f"saved_address_{addr}")])
        kb_rows.append([InlineKeyboardButton(text="Новый адрес", callback_data="new_address")])
        kb_rows.append([InlineKeyboardButton(text="← Назад", callback_data="user_checkout")])
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

        await callback.message.edit_text("Выберите адрес доставки:", reply_markup=kb)
        await state.set_state(UserStates.waiting_address_choice)
    else:
        await state.update_data(delivery_address=PICKUP_ADDRESS)

        status_text = get_restaurant_status_text()
        time_options = generate_time_options()
        kb_rows = []
        row = []
        for label, time_str in time_options:
            row.append(InlineKeyboardButton(text=label, callback_data=f"prep_time_{time_str}"))
            if len(row) == 2:
                kb_rows.append(row)
                row = []
        if row:
            kb_rows.append(row)
        kb_rows.append([InlineKeyboardButton(text="← Назад", callback_data="user_checkout")])
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

        message_text = f"Выберите время готовности заказа:\n\n<i>{status_text}</i>"

        await callback.message.edit_text(message_text, reply_markup=kb, parse_mode="HTML")
        await state.set_state(UserStates.waiting_prep_time)


@router.callback_query(F.data.startswith("saved_address_"))
async def select_saved_address(callback: CallbackQuery, state: FSMContext):
    address = callback.data[len("saved_address_"):]

    await state.update_data(delivery_address=address)

    status_text = get_restaurant_status_text()
    time_options = generate_time_options()
    kb_rows = []
    row = []
    for label, time_str in time_options:
        row.append(InlineKeyboardButton(text=label, callback_data=f"prep_time_{time_str}"))
        if len(row) == 2:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)
    kb_rows.append([InlineKeyboardButton(text="← Назад", callback_data="user_checkout")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    message_text = f"Выберите время готовности заказа:\n\n<i>{status_text}</i>"

    await callback.message.edit_text(message_text, reply_markup=kb, parse_mode="HTML")
    await state.set_state(UserStates.waiting_prep_time)


@router.callback_query(F.data == "new_address")
async def new_address_input(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🏠 Укажите новый адрес доставки:")
    await state.set_state(UserStates.waiting_address)


@router.message(UserStates.waiting_address_choice)
async def address_choice_text(message: Message, state: FSMContext):
    await message.answer("Пожалуйста, выберите адрес из списка или нажмите «Новый адрес».")
    return


@router.message(UserStates.waiting_address)
async def get_address(message: Message, state: FSMContext):
    address = message.text.strip()
    if not address:
        await message.answer("Адрес не может быть пустым. Повторите ввод:")
        return

    user_id = str(message.from_user.id)
    addresses = get_user_addresses(user_id)
    if address not in addresses:
        addresses.append(address)
        save_user_addresses(user_id, addresses)

    await state.update_data(delivery_address=address)

    status_text = get_restaurant_status_text()
    time_options = generate_time_options()
    kb_rows = []
    row = []
    for label, time_str in time_options:
        row.append(InlineKeyboardButton(text=label, callback_data=f"prep_time_{time_str}"))
        if len(row) == 2:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)
    kb_rows.append([InlineKeyboardButton(text="← Назад", callback_data="user_checkout")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    message_text = f"Выберите время готовности заказа:\n\n<i>{status_text}</i>"

    await message.answer(message_text, reply_markup=kb, parse_mode="HTML")
    await state.set_state(UserStates.waiting_prep_time)


@router.callback_query(F.data.startswith("prep_time_"))
async def process_prep_time(callback: CallbackQuery, state: FSMContext):
    prep_time = callback.data[len("prep_time_"):]

    data = await state.get_data()
    delivery_type = data.get("delivery_type", "delivery")

    await state.update_data(prep_time=prep_time)

    if delivery_type == "delivery":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Картой курьеру", callback_data="payment_card")],
            [InlineKeyboardButton(text="💵 Наличными", callback_data="payment_cash")],
            [InlineKeyboardButton(text="← Назад", callback_data="user_checkout")]
        ])
        await callback.message.edit_text("Выберите способ оплаты:", reply_markup=kb)
        await state.set_state(UserStates.waiting_payment_method)
    else:
        await callback.message.edit_text("Напишите комментарий к заказу (или «нет»):")
        await state.set_state(UserStates.waiting_comment)


@router.callback_query(F.data.startswith("payment_"))
async def process_payment_method(callback: CallbackQuery, state: FSMContext):
    payment_method = callback.data[len("payment_"):]

    if payment_method == "card":
        await state.update_data(payment_method="card", cash_amount=None)
        await callback.message.edit_text("Напишите комментарий к заказу (или «нет»):")
        await state.set_state(UserStates.waiting_comment)
    elif payment_method == "cash":
        await state.update_data(payment_method="cash")
        await callback.message.edit_text("С какой суммы выдать сдачу? (укажите сумму, с которой оплатите)")
        await state.set_state(UserStates.waiting_cash_amount)


@router.message(UserStates.waiting_cash_amount)
async def get_cash_amount(message: Message, state: FSMContext):
    try:
        cash_amount = int(message.text.strip())
        if cash_amount < 0:
            raise ValueError
    except:
        await message.answer("Введите корректную сумму (целое число больше 0):")
        return

    await state.update_data(cash_amount=cash_amount)
    await message.answer("Напишите комментарий к заказу (или «нет»):")
    await state.set_state(UserStates.waiting_comment)


@router.message(UserStates.waiting_comment)
async def get_comment(message: Message, state: FSMContext, bot: Bot):
    from config import ADMIN_IDS

    comment = message.text.strip()
    if comment.lower() == "нет":
        comment = "Без комментария"

    data = await state.get_data()
    phone = data["phone"]
    delivery_type = data.get("delivery_type", "delivery")
    delivery_address = data.get("delivery_address", "Не указан")
    prep_time = data.get("prep_time", "Не указано")
    delivery_cost = data.get("delivery_cost", 0)
    payment_method = data.get("payment_method", "Не указано")
    cash_amount = data.get("cash_amount")
    cart = data["cart"]

    total_items = sum(int(item["price"]) for item in cart)
    final_total = total_items + delivery_cost

    order_text = "Заказ:\n"
    grouped = defaultdict(list)
    for item in cart:
        grouped[item["category"]].append(item)

    for cat, items in grouped.items():
        order_text += f"<b>{cat}</b>\n"
        for item in items:
            desc = item.get('desc', '').strip()
            order_text += f"• {item['name']} — {item['price']} ₽\n"
            if desc:
                order_text += f"  {desc}\n"
        order_text += "\n"

    order_text += f"Сумма заказа: {total_items} ₽\n"
    if delivery_type == "delivery":
        if delivery_cost == 0:
            order_text += "Доставка: бесплатно\n"
        else:
            order_text += f"Доставка: {delivery_cost} ₽\n"
    order_text += f"<b>К оплате: {final_total} ₽</b>"

    username = message.from_user.username or "Скрыт"

    append_order(
        order_text, phone=phone, delivery_type=delivery_type, delivery_address=delivery_address,
        comment=comment, username=username, prep_time=prep_time,
        delivery_cost=delivery_cost, payment_method=payment_method, cash_amount=cash_amount
    )

    local_now = (datetime.datetime.utcnow() + LOCAL_TZ_OFFSET).strftime("%d.%m.%Y %H:%M")

    # Определяем "СЕГОДНЯ"/"ЗАВТРА" для prep_time
    prep_dt = datetime.datetime.strptime(prep_time, "%d.%m.%Y %H:%M")
    local_today = (datetime.datetime.utcnow() + LOCAL_TZ_OFFSET).date()
    day_label = "СЕГОДНЯ" if prep_dt.date() == local_today else "ЗАВТРА" if prep_dt.date() == local_today + datetime.timedelta(days=1) else ""
    prep_time_with_day = f"<b>{day_label}</b> {prep_time}" if day_label else prep_time

    admin_notification = f"🍲 <b>Новый заказ — Сытный Дом</b>\n\n"
    admin_notification += f"📞 Телефон: {phone}\n"
    admin_notification += f"👤 Username: @{username}\n"
    admin_notification += f"💬 Комментарий: {comment}\n"
    admin_notification += f"⏰ Готовность к: {prep_time_with_day}\n"
    if payment_method == "cash" and cash_amount is not None:
        admin_notification += f"💵 Оплата наличными, сдача с {cash_amount} ₽\n"
    elif payment_method == "card":
        admin_notification += f"💳 Оплата картой курьеру\n"
    admin_notification += "\n"
    if delivery_type == "delivery":
        admin_notification += f"🚚 <b>Доставка</b>\n📍 Адрес: {delivery_address}\n"
        if delivery_cost == 0:
            admin_notification += "Доставка бесплатная\n"
        else:
            admin_notification += f"Стоимость доставки: {delivery_cost} ₽\n"
    else:
        admin_notification += f"🏃 <b>Самовывоз</b>\n📍 Адрес: {PICKUP_ADDRESS}\n"
    admin_notification += "\n"
    admin_notification += order_text + "\n"
    admin_notification += f"🕒 Время оформления: {local_now}"

    for admin_id in ADMIN_IDS:
        await bot.send_message(admin_id, admin_notification, parse_mode="HTML")

    client_confirmation = "✅ <b>Спасибо за заказ!</b>\n\n"
    client_confirmation += order_text + "\n\n"
    client_confirmation += f"⏰ Готовность к: {prep_time_with_day}\n"
    if delivery_type == "delivery":
        client_confirmation += f"🚚 <b>Доставка по адресу:</b>\n{delivery_address}\n"
        if delivery_cost == 0:
            client_confirmation += "Доставка бесплатная\n"
        else:
            client_confirmation += f"Стоимость доставки: {delivery_cost} ₽\n"
    else:
        client_confirmation += f"🏃 <b>Самовывоз по адресу:</b>\n{PICKUP_ADDRESS}\n"
    if payment_method == "cash" and cash_amount is not None:
        client_confirmation += f"💵 Оплата наличными, сдача с {cash_amount} ₽\n"
    elif payment_method == "card":
        client_confirmation += f"💳 Оплата картой курьеру\n"
    client_confirmation += "\nМы свяжемся с вами в ближайшее время для подтверждения. Приятного аппетита! 🍲"

    await message.answer(client_confirmation, parse_mode="HTML")
    await state.clear()


@router.message(Command("clear_addresses"))
async def clear_addresses(message: Message, state: FSMContext):
    current_state = await state.get_state()

    if current_state:
        await message.answer("Нельзя очистить адреса во время оформления заказа. Завершите или отмените заказ сначала.")
        return

    user_id = str(message.from_user.id)
    save_user_addresses(user_id, [])
    await message.answer("Список сохранённых адресов очищен.")
    await show_categories(message, state)


@router.callback_query(F.data == "user_clear_cart")
async def clear_cart(callback: CallbackQuery, state: FSMContext):
    await state.update_data(cart=[])
    await callback.answer("Корзина очищена")
    await show_categories(callback, state)