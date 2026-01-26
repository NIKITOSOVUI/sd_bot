from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, Contact, ReplyKeyboardRemove, FSInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from db import read_menu, append_order, read_users, write_users
from keyboards import phone_kb, categories_kb, category_kb, cart_kb
from states import UserStates
from config import WELCOME_PHOTO_PATH
import datetime
from collections import defaultdict

router = Router()


async def show_categories(msg_or_cb, state: FSMContext):
    data = await state.get_data()
    cart = data.get("cart", [])
    kb = categories_kb(len(cart))
    text = "🍲 <b>Сытный Дом</b>\n\nВыберите категорию меню:"

    if isinstance(msg_or_cb, CallbackQuery):
        await msg_or_cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await msg_or_cb.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    users = read_users()

    # Отправка приветственного фото
    if WELCOME_PHOTO_PATH:
        try:
            if WELCOME_PHOTO_PATH.startswith(("http://", "https://")):
                if WELCOME_PHOTO_PATH.startswith("http://"):
                    print(f"Ошибка: http URL не поддерживается Telegram. Используйте https. Путь: {WELCOME_PHOTO_PATH}")
                else:
                    await message.answer_photo(photo=WELCOME_PHOTO_PATH)
            else:
                photo = FSInputFile(WELCOME_PHOTO_PATH)
                await message.answer_photo(photo=photo)
        except FileNotFoundError:
            print(f"Ошибка: файл фото не найден по пути '{WELCOME_PHOTO_PATH}'.")
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

    text = f"<b>{category}</b>\n\n\n"  # Без эмодзи

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
        text += f"<b>{cat}</b>\n"  # Без эмодзи

        for item in citems:
            desc = item.get('desc', '').strip()
            text += f"• {item['name']} — {item['price']} ₽\n"
            if desc:
                text += f"  {desc}\n"

        text += "\n"

    text += f"<b>Итого: {total} ₽</b>"

    await callback.message.edit_text(text, reply_markup=cart_kb(), parse_mode="HTML")


@router.callback_query(F.data == "user_checkout")
async def checkout(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🏠 Укажите адрес доставки:")
    await state.set_state(UserStates.waiting_address)


@router.message(UserStates.waiting_address)
async def get_address(message: Message, state: FSMContext):
    address = message.text.strip()
    if not address:
        await message.answer("Адрес не может быть пустым. Пожалуйста, укажите адрес доставки:")
        return

    await state.update_data(address=address)
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
    address = data.get("address", "не указан")
    cart = data["cart"]

    total = sum(int(item["price"]) for item in cart)

    order_text = "Заказ:\n"
    grouped = defaultdict(list)
    for item in cart:
        grouped[item["category"]].append(item)

    for cat, items in grouped.items():
        order_text += f"<b>{cat}</b>\n"  # Без эмодзи
        for item in items:
            desc = item.get('desc', '').strip()
            order_text += f"• {item['name']} — {item['price']} ₽\n"
            if desc:
                order_text += f"  {desc}\n"
        order_text += "\n"

    order_text += f"Итого: {total} ₽"

    username = message.from_user.username or "Скрыт"

    append_order(order_text, phone=phone, address=address, comment=comment, username=username)

    now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    full_text = f"🍲 <b>Новый заказ — Сытный Дом</b>\n\n"
    full_text += order_text + "\n\n"
    full_text += f"📞 Телефон: {phone}\n\n"
    full_text += f"📍 Адрес доставки: {address}\n"
    full_text += f"💬 Комментарий: {comment}\n"
    full_text += f"👤 Юзернейм: @{username}\n\n"
    full_text += f"🕒 Время: {now}"

    for admin_id in ADMIN_IDS:
        await bot.send_message(admin_id, full_text, parse_mode="HTML")

    await message.answer("Заказ отправлен! Скоро свяжемся. Спасибо! 🍲")
    await state.clear()


@router.callback_query(F.data == "user_clear_cart")
async def clear_cart(callback: CallbackQuery, state: FSMContext):
    await state.update_data(cart=[])
    await callback.answer("Корзина очищена")
    await show_categories(callback, state)