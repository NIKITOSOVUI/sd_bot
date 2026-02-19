from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, Contact, ReplyKeyboardRemove, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.filters.logic import or_f
from aiogram.fsm.context import FSMContext
from db import read_menu, append_order, read_users, save_user_phone, get_user_addresses, save_user_addresses, get_user_orders
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

ORDER_END_TIME = datetime.time(20, 30)  # ← НОВОЕ: заказы принимаются до 20:30
PICKUP_ORDER_END_TIME = datetime.time(20, 45)  # ← НОВОЕ: для самовывоза заказы (и "Ближайшее время") до 20:45

# Минимальное время подготовки
PICKUP_PREPARE_MINUTES = 30   # для самовывоза
DELIVERY_PREPARE_MINUTES = 60  # для доставки — через час

# Стоимость доставки
FREE_DELIVERY_MIN = 1500
DELIVERY_COST = 250
MIN_ORDER_FOR_DELIVERY = 300


def generate_time_options(min_delay_minutes: int = PICKUP_PREPARE_MINUTES):
    utc_now = datetime.datetime.utcnow()
    local_now = utc_now + LOCAL_TZ_OFFSET
    local_date = local_now.date()

    # Минимальное время: текущее + delay, округление вверх до 30 мин
    min_time = local_now + datetime.timedelta(minutes=min_delay_minutes)
    min_time = min_time.replace(second=0, microsecond=0)

    extra_minutes = min_time.minute % TIME_STEP_MINUTES
    if extra_minutes > 0:
        min_time += datetime.timedelta(minutes=TIME_STEP_MINUTES - extra_minutes)

    if min_time.minute == 60:
        min_time = min_time.replace(minute=0) + datetime.timedelta(hours=1)

    # Всегда корректируем на окно заказов: не раньше 10:00, не позже 20:30
    start_time = datetime.time(ORDER_START_HOUR, 0)
    end_time_limit = datetime.time(ORDER_END_HOUR, 30)

    # Если слишком рано — ставим 10:00 той же даты
    if min_time.time() < start_time:
        min_time = min_time.replace(hour=ORDER_START_HOUR, minute=0)

    # Если слишком поздно — перенос на следующий день 10:00
    if min_time.time() > end_time_limit:
        min_time = min_time + datetime.timedelta(days=1)
        min_time = min_time.replace(hour=ORDER_START_HOUR, minute=0)

    # Определяем дату для слотов и end_time
    slots_date = min_time.date()
    end_time = datetime.datetime.combine(slots_date, end_time_limit)

    # Если после всех корректировок min_time > end_time — перенос на следующий день
    if min_time > end_time:
        slots_date += datetime.timedelta(days=1)
        min_time = datetime.datetime.combine(slots_date, datetime.time(ORDER_START_HOUR, 0))
        end_time = datetime.datetime.combine(slots_date, end_time_limit)

    # Генерируем слоты
    options = []
    current = min_time
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
        if local_time >= ORDER_END_TIME:
            return "🟢 Ресторан сейчас открыт (до 21:00), но заказы принимаются только до 20:30. Ваш заказ будет на завтра."
        else:
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


# Глобальная отмена по слову "отмена" на всех этапах (добавьте waiting_phone)
@router.message(or_f(
    UserStates.waiting_delivery_type,
    UserStates.waiting_address_choice,
    UserStates.waiting_address,
    UserStates.waiting_prep_time,
    UserStates.waiting_payment_method,
    UserStates.waiting_cash_amount,
    UserStates.waiting_comment,
    UserStates.waiting_phone  # ← НОВОЕ: для отмены на этапе номера
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
    phone_raw = message.contact.phone_number  # например "+79016406231" или "89016406231"
    phone_clean = phone_raw.lstrip('+')  # убираем +
    if phone_clean.startswith('8'):
        phone_clean = '7' + phone_clean[1:]  # заменяем 8 на 7
    
    user_id = str(message.from_user.id)
    
    users = read_users()
    users[user_id] = phone_clean  # сохраняем чистые цифры "79016406231"
    save_user_phone(user_id, phone_clean)  # Исправьте на вашу функцию
    
    await state.update_data(phone=phone_clean, cart=[])
    await message.answer(
        f"Спасибо! Номер сохранён: +{phone_clean}",  # показываем с +
        reply_markup=ReplyKeyboardRemove()
    )
    await show_categories(message, state)
    await state.clear()  # ← НОВОЕ: очищаем состояние


@router.message(F.contact, UserStates.waiting_phone_share)
async def update_phone_from_contact(message: Message, state: FSMContext):
    phone_raw = message.contact.phone_number
    phone_clean = phone_raw.lstrip('+')
    if phone_clean.startswith('8'):
        phone_clean = '7' + phone_clean[1:]
    
    await process_phone_update(message, state, phone_clean)  # передаём чистый

@router.message(UserStates.waiting_phone_manual)
async def update_phone_from_text(message: Message, state: FSMContext):
    phone_raw = message.text.strip()
    phone_clean = "".join(filter(str.isdigit, phone_raw))  # только цифры
    
    if phone_clean.startswith("8"):
        phone_clean = "7" + phone_clean[1:]
    
    if not (phone_clean.startswith("7") and len(phone_clean) == 11):
        await message.answer("❌ Неверный формат номера. Введите в формате +7XXXXXXXXXX или 8XXXXXXXXXX или 7XXXXXXXXXX:")
        return
    
    await process_phone_update(message, state, phone_clean)


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
    text += "<i>Стоимость доставки 250 рублей\n<b>📌 Бесплатная доставка от 1500 ₽</b></i>"

    await callback.message.edit_text(text, reply_markup=cart_kb(), parse_mode="HTML")


@router.callback_query(F.data == "user_checkout")
async def checkout(callback: CallbackQuery, state: FSMContext):

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

    if delivery_type == "delivery" and total < MIN_ORDER_FOR_DELIVERY:
        await callback.answer(
            f"Минимальная сумма заказа для доставки — {MIN_ORDER_FOR_DELIVERY} ₽",
            show_alert=True
        )
        return

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
    else:  # самовывоз
                await state.update_data(delivery_address=PICKUP_ADDRESS)

                # Расчёт текущего местного времени для проверки открытости
                utc_now = datetime.datetime.utcnow()
                local_now = utc_now + LOCAL_TZ_OFFSET
                local_time = local_now.time()

                status_text = get_restaurant_status_text()
                time_options = generate_time_options(min_delay_minutes=PICKUP_PREPARE_MINUTES)

                kb_rows = []

                # Кнопка «Ближайшее время» только если сейчас открыто
                if local_time < PICKUP_ORDER_END_TIME:
                    kb_rows.append([InlineKeyboardButton(text="🔥 Ближайшее время", callback_data="prep_time_asap")])

                # Обычные слоты по 2 в ряд
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
    await callback.answer()  # ← Важно: снимает loading с кнопки

    await callback.message.edit_text("🏠 Укажите новый адрес доставки:")
    await state.set_state(UserStates.waiting_address)


@router.callback_query(F.data.startswith("saved_address_"))
async def select_saved_address(callback: CallbackQuery, state: FSMContext):
    address = callback.data[len("saved_address_"):]

    await state.update_data(delivery_address=address)

    # Расчёт текущего местного времени
    utc_now = datetime.datetime.utcnow()
    local_now = utc_now + LOCAL_TZ_OFFSET
    local_time = local_now.time()

    status_text = get_restaurant_status_text()
    time_options = generate_time_options(min_delay_minutes=DELIVERY_PREPARE_MINUTES)

    kb_rows = []

    # Кнопка «Ближайшее время» только если сейчас открыто
    if local_time < ORDER_END_TIME:
        kb_rows.append([InlineKeyboardButton(text="🔥 Ближайшее время", callback_data="prep_time_asap")])

    # Обычные слоты по 2 в ряд
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

    # Расчёт текущего местного времени
    utc_now = datetime.datetime.utcnow()
    local_now = utc_now + LOCAL_TZ_OFFSET
    local_time = local_now.time()

    status_text = get_restaurant_status_text()
    time_options = generate_time_options(min_delay_minutes=DELIVERY_PREPARE_MINUTES)

    kb_rows = []

    # Кнопка «Ближайшее время» только если сейчас открыто
    if local_time < ORDER_END_TIME:
        kb_rows.append([InlineKeyboardButton(text="🔥 Ближайшее время", callback_data="prep_time_asap")])

    # Обычные слоты по 2 в ряд
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
    raw_prep_time = callback.data[len("prep_time_"):]

    if raw_prep_time == "asap":
        prep_time = "Ближайшее время"
    else:
        prep_time = raw_prep_time   # обычная строка даты/времени

    await state.update_data(prep_time=prep_time)

    data = await state.get_data()
    delivery_type = data.get("delivery_type", "delivery")

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
        if cash_amount < 500:
            raise ValueError
    except:
        await message.answer("Введите корректную сумму (целое число больше 500):")
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

    # === БЕЗОПАСНОЕ получение телефона ===
    user_id_str = str(message.from_user.id)
    users = read_users()
    phone_raw = users.get(user_id_str)  # Может быть None

    if phone_raw is None or not phone_raw:
        phone_display = "Не указан"
        phone_for_db = "Не указан"
    else:
        phone_display = "+" + phone_raw
        phone_for_db = phone_raw
    # === КОНЕЦ ===

    # Обновляем username актуальным
    current_username = message.from_user.username
    if current_username:
        current_username = "@" + current_username
    else:
        current_username = "Скрыт"

    delivery_type = data.get("delivery_type", "delivery")
    delivery_address = data.get("delivery_address", "Не указан")
    prep_time = data.get("prep_time", "Не указано")
    delivery_cost = data.get("delivery_cost", 0)
    payment_method = data.get("payment_method", "Не указано")
    cash_amount = data.get("cash_amount")
    cart = data["cart"]

    total_items = sum(int(item["price"]) for item in cart)
    final_total = total_items + delivery_cost

    grouped = defaultdict(list)
    for item in cart:
        grouped[item["category"]].append(item)

    # Текст заказа БЕЗ описаний (для админов и БД)
    admin_order_text = "Заказ:\n"
    for cat, items in grouped.items():
        admin_order_text += f"<b>{cat}</b>\n"
        for item in items:
            admin_order_text += f"• {item['name']} — {item['price']} ₽\n"
        admin_order_text += "\n"

    admin_order_text += f"Сумма заказа: {total_items} ₽\n"
    if delivery_type == "delivery":
        if delivery_cost == 0:
            admin_order_text += "Доставка: бесплатно\n"
        else:
            admin_order_text += f"Доставка: {delivery_cost} ₽\n"
    admin_order_text += f"<b>К оплате: {final_total} ₽</b>"

    # Текст заказа С описаниями (только для клиента)
    client_order_text = "Заказ:\n"
    for cat, items in grouped.items():
        client_order_text += f"<b>{cat}</b>\n"
        for item in items:
            desc = item.get('desc', '').strip()
            client_order_text += f"• {item['name']} — {item['price']} ₽\n"
            if desc:
                client_order_text += f"  {desc}\n"
        client_order_text += "\n"

    client_order_text += f"Сумма заказа: {total_items} ₽\n"
    if delivery_type == "delivery":
        if delivery_cost == 0:
            client_order_text += "Доставка: бесплатно\n"
        else:
            client_order_text += f"Доставка: {delivery_cost} ₽\n"
    client_order_text += f"<b>К оплате: {final_total} ₽</b>"

    # Сохраняем в БД
    append_order(
        admin_order_text, phone=phone_for_db, delivery_type=delivery_type, delivery_address=delivery_address,
        comment=comment, username=current_username, prep_time=prep_time,
        delivery_cost=delivery_cost, payment_method=payment_method, cash_amount=cash_amount,
        user_id=user_id_str
    )

    local_now = (datetime.datetime.utcnow() + LOCAL_TZ_OFFSET).strftime("%d.%m.%Y %H:%M")
    local_today = (datetime.datetime.utcnow() + LOCAL_TZ_OFFSET).date()

    if prep_time == "Ближайшее время":
        prep_time_with_day = "<b>БЛИЖАЙШЕЕ ВРЕМЯ</b>"
    else:
        try:
            prep_dt = datetime.datetime.strptime(prep_time, "%d.%m.%Y %H:%M")
            day_label = "СЕГОДНЯ" if prep_dt.date() == local_today else "ЗАВТРА" if prep_dt.date() == local_today + datetime.timedelta(days=1) else ""
            prep_time_with_day = f"<b>{day_label}</b> {prep_time}" if day_label else prep_time
        except:
            prep_time_with_day = prep_time

    # === ИСПРАВЛЕНО: инициализация admin_notification с заголовком ===
    admin_notification = f"🍲 <b>Новый заказ — Сытный Дом</b>\n\n"
    admin_notification += f"📞 Телефон: {phone_display}\n"
    admin_notification += f"👤 Username: {current_username}\n"
    admin_notification += f"💬 Комментарий: {comment}\n"
    admin_notification += f"⏰ Готовность к: {prep_time_with_day}\n"
    if payment_method == "cash" and cash_amount is not None:
        admin_notification += f"💵 Оплата наличными, сдача с {cash_amount} ₽\n"
    elif payment_method == "card":
        admin_notification += f"💳 Оплата картой курьеру\n"
    admin_notification += "\n"
    if delivery_type == "delivery":
        admin_notification += f"🚚 <b>Доставка</b>\n📍 Адрес: {delivery_address}\n"
    else:
        admin_notification += f"🏃 <b>Самовывоз</b>\n📍 Адрес: {PICKUP_ADDRESS}\n"
    admin_notification += "\n"
    admin_notification += admin_order_text + "\n"
    admin_notification += f"🕒 Время оформления: {local_now}"
    # === КОНЕЦ ИСПРАВЛЕНИЯ ===

    for admin_id in ADMIN_IDS:
        await bot.send_message(admin_id, admin_notification, parse_mode="HTML")

    # Подтверждение клиенту
    client_confirmation = "✅ <b>Спасибо за заказ!</b>\n\n"
    client_confirmation += client_order_text + "\n\n"
    client_confirmation += f"⏰ Готовность к: {prep_time_with_day}\n"
    if delivery_type == "delivery":
        client_confirmation += f"🚚 <b>Доставка по адресу:</b>\n{delivery_address}\n"
    else:
        client_confirmation += f"🏃 <b>Самовывоз по адресу:</b>\n{PICKUP_ADDRESS}\n"
    if payment_method == "cash" and cash_amount is not None:
        client_confirmation += f"💵 Оплата наличными, сдача с {cash_amount} ₽\n"
    elif payment_method == "card":
        client_confirmation += f"💳 Оплата картой курьеру\n"
    client_confirmation += "\n\n👤 Чтобы посмотреть ваши адреса и историю заказов, введите команду /profile"
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


async def process_phone_update(message: Message, state: FSMContext, phone_clean: str):
    user_id = str(message.from_user.id)
    
    users = read_users()
    users[user_id] = phone_clean  # сохраняем чистые цифры
    save_user_phone(user_id, phone_clean)
    
    phone_display = "+" + phone_clean  # для показа пользователю
    
    await message.answer(
        f"✅ Номер телефона успешно обновлён: {phone_display}",
        reply_markup=ReplyKeyboardRemove()
    )
    await show_profile(message)
    await state.clear()


async def show_profile(obj):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚗 Адреса доставки", callback_data="profile_addresses")],
        [InlineKeyboardButton(text="📋 Мои заказы", callback_data="profile_orders")],
        [InlineKeyboardButton(text="📱 Номер телефона", callback_data="profile_phone")],  # ← НОВАЯ КНОПКА
        [InlineKeyboardButton(text="← В меню", callback_data="user_back_to_categories")]
    ])
    text = "👤 <b>Ваш профиль</b>\n\nВыберите раздел:"

    if isinstance(obj, Message):
        await obj.answer(text, reply_markup=kb, parse_mode="HTML")
    elif isinstance(obj, CallbackQuery):
        await obj.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.message(Command("profile"))
async def cmd_profile(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        await message.answer("Во время оформления заказа команда /profile недоступна. Завершите заказ или введите «отмена» для отмены.")
        return

    user_id = str(message.from_user.id)
    users = read_users()
    if user_id not in users:
        await message.answer("Вы не авторизованы. Начните с команды /start")
        return

    await show_profile(message)


@router.callback_query(F.data == "back_to_profile")
async def back_to_profile(callback: CallbackQuery):
    await show_profile(callback)


@router.callback_query(F.data == "profile_addresses")
async def profile_addresses(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    addresses = get_user_addresses(user_id)

    text = "🚗 <b>Сохранённые адреса доставки</b>\n\n"
    if not addresses:
        text += "Нет сохранённых адресов.\n\nАдрес можно добавить при оформлении заказа с доставкой."
    else:
        for num, addr in enumerate(addresses, 1):
            text += f"{num}. {addr}\n"
        text += "\nЧтобы очистить весь список адресов — используйте команду /clear_addresses"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад в профиль", callback_data="back_to_profile")]
    ])

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "profile_orders")
async def profile_orders(callback: CallbackQuery):
    user_id = str(callback.from_user.id)

    orders = get_user_orders(user_id)

    text = "📋 <b>Ваши последние заказы</b> (до 10 шт.)\n\n"
    if not orders:
        text += "Вы ещё не делали заказы 🙂"
    else:
        for order in orders:
            prep_time = order['prep_time']
            if prep_time == "Ближайшее время":
                prep_display = "<b>БЛИЖАЙШЕЕ ВРЕМЯ</b>"
            else:
                prep_display = prep_time

            text += f"🕒 Оформлен: {order['timestamp']}\n"
            text += f"⏰ Готовность: {prep_display}\n\n"
            text += order['order_text']
            text += "\n" + "—" * 30 + "\n\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад в профиль", callback_data="back_to_profile")]
    ])

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "profile_phone")
async def profile_phone(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    user_id = str(callback.from_user.id)
    users = read_users()
    phone_clean = users.get(user_id)  # Может быть None, если ключа нет

    if phone_clean is None or not phone_clean:
        phone_display = "Не указан"
    else:
        phone_display = "+" + phone_clean

    text = f"📱 <b>Ваш номер телефона:</b> {phone_display}\n\n"
    text += "Вы можете обновить номер одним из способов ниже:"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Поделиться номером телефона", callback_data="phone_share")],
        [InlineKeyboardButton(text="⌨️ Ввести номер телефона вручную", callback_data="phone_manual")],
        [InlineKeyboardButton(text="← Назад в профиль", callback_data="back_to_profile")]
    ])

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "phone_share")
async def phone_share(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    await callback.message.edit_text("Нажмите кнопку ниже, чтобы поделиться номером телефона:")
    await callback.message.answer("Поделитесь контактом:", reply_markup=phone_kb)
    await state.set_state(UserStates.waiting_phone_share)


@router.callback_query(F.data == "phone_manual")
async def phone_manual(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    await callback.message.edit_text("Введите номер телефона вручную (в формате +7XXXXXXXXXX или 8XXXXXXXXXX):")
    await state.set_state(UserStates.waiting_phone_manual)