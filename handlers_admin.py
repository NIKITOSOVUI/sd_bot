from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from db import LOCAL_TZ_OFFSET

from db import read_menu, write_menu, get_orders_filtered, get_all_user_ids, create_promo, get_promos, get_promo_by_code, delete_promo, get_promo_stats, get_menu_item_by_id
from keyboards import admin_main_kb, admin_categories_kb, promo_type_kb, admin_promos_kb, admin_promo_actions_kb, admin_promo_categories_kb, admin_promo_items_kb
from states import AdminStates
from config import ADMIN_IDS

import datetime

router = Router()


async def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


@router.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return

    await state.clear()  # Очищаем состояние при входе

    await message.answer("Админ-панель — Сытный Дом", reply_markup=admin_main_kb())


@router.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return

    await state.clear()  # Очищаем состояние при возврате

    await callback.message.edit_text("Админ-панель — Сытный Дом", reply_markup=admin_main_kb())


@router.callback_query(F.data == "admin_view_menu")
async def admin_view_menu(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    
    menu_list = read_menu()
    text = "<b>Текущее меню</b>\n\n"
    
    if not menu_list:
        text += "Меню пустое."
    else:
        for cat_dict in menu_list:
            text += f"<b>{cat_dict['category']}</b>\n"
            for item in cat_dict['items']:
                desc = f"\n{item.get('desc', '')}" if item.get('desc') else ""
                text += f"• {item['name']} — {item['price']} ₽{desc}\n"
            text += "\n"
    
    await callback.message.edit_text(text, reply_markup=admin_main_kb(), parse_mode="HTML")


@router.callback_query(F.data == "admin_add_category")
async def admin_add_category_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await callback.message.edit_text("Введите название новой категории:")
    await state.set_state(AdminStates.adding_category)


@router.message(AdminStates.adding_category)
async def admin_add_category(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    
    category = message.text.strip()
    menu_list = read_menu()
    menu_list.append({"category": category, "items": []})
    write_menu(menu_list)
    
    await message.answer(f"Категория «{category}» добавлена!", reply_markup=admin_main_kb())
    await state.clear()


@router.callback_query(F.data == "admin_delete_category")
async def admin_delete_category_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    
    if not read_menu():
        await callback.message.edit_text("Меню пустое — нет категорий для удаления.", reply_markup=admin_main_kb())
        return
    
    kb = admin_categories_kb("delete_cat_")
    await callback.message.edit_text("Выберите категорию для удаления:", reply_markup=kb)
    await state.set_state(AdminStates.choosing_delete_category)


@router.callback_query(F.data.startswith("admin_delete_cat_"))
async def admin_delete_category_confirm(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    
    category = callback.data[len("admin_delete_cat_"):]
    menu_list = read_menu()
    found = False
    
    for i, cat_dict in enumerate(menu_list):
        if cat_dict["category"] == category:
            if cat_dict["items"]:
                await callback.message.edit_text(
                    f"Ошибка: категория «{category}» содержит блюда. Сначала удалите блюда.",
                    reply_markup=admin_main_kb()
                )
            else:
                menu_list.pop(i)
                write_menu(menu_list)
                await callback.message.edit_text(
                    f"Категория «{category}» удалена!",
                    reply_markup=admin_main_kb()
                )
            found = True
            break
    
    if not found:
        await callback.message.edit_text("Категория не найдена.", reply_markup=admin_main_kb())
    
    await state.clear()


@router.callback_query(F.data == "admin_add_dish")
async def admin_add_dish_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    
    if not read_menu():
        await callback.message.edit_text("Меню пустое. Сначала добавьте категорию.", reply_markup=admin_main_kb())
        return
    
    kb = admin_categories_kb("add_dish_cat_", include_new=True)
    await callback.message.edit_text("Выберите категорию для добавления блюда:", reply_markup=kb)
    await state.set_state(AdminStates.choosing_add_dish_category)


@router.callback_query(F.data.startswith("admin_add_dish_cat_"))
async def admin_add_dish_category_selected(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    
    data = callback.data[len("admin_add_dish_cat_"):]
    
    if data == "new":
        await callback.message.edit_text("Введите название новой категории:")
        await state.set_state(AdminStates.adding_new_category_for_dish)
        return
    
    await state.update_data(category=data)
    await callback.message.edit_text("Введите название блюда:")
    await state.set_state(AdminStates.adding_dish_name)


@router.message(AdminStates.adding_new_category_for_dish)
async def admin_add_new_category_for_dish(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    
    category = message.text.strip()
    await state.update_data(category=category)
    await message.answer("Введите название блюда:")
    await state.set_state(AdminStates.adding_dish_name)


@router.message(AdminStates.adding_dish_name)
async def admin_add_dish_price(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    
    await state.update_data(name=message.text.strip())
    await message.answer("Введите цену (только цифры):")
    await state.set_state(AdminStates.adding_dish_price)


@router.message(AdminStates.adding_dish_price)
async def admin_add_dish_desc(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    
    if not message.text.strip().isdigit():
        await message.answer("Ошибка: цена должна состоять только из цифр!")
        return
    
    await state.update_data(price=message.text.strip())
    await message.answer("Введите описание (или «нет» для пустого):")
    await state.set_state(AdminStates.adding_dish_desc)


@router.message(AdminStates.adding_dish_desc)
async def admin_add_dish_finish(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    desc = message.text.strip() if message.text.strip().lower() != "нет" else ""
    
    menu_list = read_menu()
    found = False
    
    for cat_dict in menu_list:
        if cat_dict["category"] == data["category"]:
            cat_dict["items"].append({
                "name": data["name"],
                "price": data["price"],
                "desc": desc
            })
            found = True
            break
    
    if not found:
        menu_list.append({
            "category": data["category"],
            "items": [{"name": data["name"], "price": data["price"], "desc": desc}]
        })
    
    write_menu(menu_list)
    
    await message.answer(
        f"Блюдо «{data['name']}» добавлено в категорию «{data['category']}»!",
        reply_markup=admin_main_kb()
    )
    await state.clear()


@router.callback_query(F.data == "admin_delete_dish")
async def admin_delete_dish_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    
    if not read_menu():
        await callback.message.edit_text("Меню пустое — нет блюд для удаления.", reply_markup=admin_main_kb())
        return
    
    kb = admin_categories_kb("delete_dish_cat_")
    await callback.message.edit_text("Выберите категорию для удаления блюда:", reply_markup=kb)
    await state.set_state(AdminStates.choosing_delete_dish_category)


@router.callback_query(F.data.startswith("admin_delete_dish_cat_"))
async def admin_delete_dish_show(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    
    category = callback.data[len("admin_delete_dish_cat_"):]
    menu_list = read_menu()
    items = None
    
    for cat_dict in menu_list:
        if cat_dict["category"] == category:
            items = cat_dict["items"]
            break
    
    if not items:
        await callback.message.edit_text("В этой категории нет блюд.", reply_markup=admin_main_kb())
        await state.clear()
        return
    
    text = f"<b>Блюда в категории «{category}»</b>:\n\n"
    for i, item in enumerate(items, 1):
        text += f"{i}. {item['name']} — {item['price']} ₽\n"
    
    text += "\nВведите номер блюда для удаления:"
    
    await callback.message.edit_text(text, parse_mode="HTML")
    await state.update_data(delete_category=category, delete_items=items)
    await state.set_state(AdminStates.deleting_dish_num)


@router.message(AdminStates.deleting_dish_num)
async def admin_delete_dish_finish(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    
    try:
        num = int(message.text.strip()) - 1
        data = await state.get_data()
        items = data["delete_items"]
        
        if num < 0 or num >= len(items):
            raise ValueError
        
        removed = items.pop(num)
        menu_list = read_menu()
        
        for cat_dict in menu_list:
            if cat_dict["category"] == data["delete_category"]:
                cat_dict["items"] = items
                break
        
        write_menu(menu_list)
        
        await message.answer(f"Удалено: {removed['name']}", reply_markup=admin_main_kb())
    
    except:
        await message.answer("Неверный номер!", reply_markup=admin_main_kb())
    
    await state.clear()


# ────────────────────────────────────────────────
#               ПРОСМОТР ЗАКАЗОВ + ФИЛЬТРЫ + ПАГИНАЦИЯ
# ────────────────────────────────────────────────

ORDERS_PER_PAGE = 12
MAX_MESSAGE_LEN = 3800


def format_order_block(order) -> str:
    time_str = order.get("time", "—")
    username = order.get("username", "Скрыт")
    phone = order.get("phone", "Не указан")
    delivery_type = order.get("delivery_type", "Не указан")
    delivery_address = order.get("delivery_address", "Не указан")
    comment = order.get("comment", "Без комментария")
    prep_time = order.get("prep_time", "Не указано")
    text = order.get("text", "")

    # "СЕГОДНЯ"/"ЗАВТРА" для prep_time
    if prep_time != "Не указано":
        try:
            prep_dt = datetime.datetime.strptime(prep_time, "%d.%m.%Y %H:%M")
            local_today = (datetime.datetime.utcnow() + LOCAL_TZ_OFFSET).date()
            day_label = "СЕГОДНЯ" if prep_dt.date() == local_today else "ЗАВТРА" if prep_dt.date() == local_today + datetime.timedelta(days=1) else ""
            prep_time_with_day = f"<b>{day_label}</b> {prep_time}" if day_label else prep_time
        except:
            prep_time_with_day = prep_time
    else:
        prep_time_with_day = prep_time

    result = [
        f"<b>{time_str} (@{username})</b>",
        f"📞 {phone}",
        f"⏰ Готовность к: {prep_time_with_day}",
    ]

    if delivery_type == "delivery":
        result.append(f"🚚 Доставка: {delivery_address}")
    elif delivery_type == "pickup":
        result.append(f"🏃 Самовывоз: {delivery_address}")
    else:
        result.append(f"Тип получения: {delivery_type}\nАдрес: {delivery_address}")

    result += [
        f"💬 {comment}",
        "",
        text if text else "(Состав заказа не указан)",
        "────────────────────",
        ""
    ]

    return "\n".join(result)


def split_orders_into_pages(orders, per_page=ORDERS_PER_PAGE) -> list[str]:
    pages = []
    current = []
    current_len = 0

    for order in orders:
        block = format_order_block(order)
        block_len = len(block)

        if current_len + block_len > MAX_MESSAGE_LEN and current:
            pages.append("".join(current))
            current = []
            current_len = 0

        current.append(block)
        current_len += block_len

        if len(current) >= per_page:
            pages.append("".join(current))
            current = []
            current_len = 0

    if current:
        pages.append("".join(current))

    return pages


async def show_orders_page(
    event: Message | CallbackQuery,
    state: FSMContext,
    page: int = 0
):
    data = await state.get_data()
    period = data.get("orders_period")
    date_from = data.get("orders_date_from")
    date_to = data.get("orders_date_to")

    orders = get_orders_filtered(
        period=period,
        date_from=date_from,
        date_to=date_to
    )

    if not orders:
        text = "Заказов за выбранный период нет."
        kb = get_orders_filter_kb()
        
        if isinstance(event, CallbackQuery):
            try:
                await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            except TelegramBadRequest:
                await event.answer("Вы уже здесь")
        else:
            await event.answer(text, reply_markup=kb, parse_mode="HTML")
        return

    pages = split_orders_into_pages(orders)

    if page < 0:
        page = 0
    if page >= len(pages):
        page = len(pages) - 1

    text = f"<b>Заказы</b>  (страница {page+1}/{len(pages)})\n\n"
    text += pages[page]

    kb = get_orders_pagination_kb(page, len(pages))

    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except TelegramBadRequest:
            await event.answer("Страница уже открыта")
    else:
        await event.answer(text, parse_mode="HTML", reply_markup=kb)


def get_orders_filter_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="Сегодня", callback_data="orders_filter_today")],
        [InlineKeyboardButton(text="Последние 3 дня", callback_data="orders_filter_3days")],
        [InlineKeyboardButton(text="Последняя неделя", callback_data="orders_filter_week")],
        [InlineKeyboardButton(text="Выбрать даты", callback_data="orders_filter_custom")],
        [InlineKeyboardButton(text="Все заказы", callback_data="orders_filter_all")],
        [InlineKeyboardButton(text="← Назад в админку", callback_data="admin_back")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_orders_pagination_kb(page: int, total_pages: int) -> InlineKeyboardMarkup:
    row1 = []
    if page > 0:
        row1.append(InlineKeyboardButton(text="« Пред.", callback_data=f"orders_page_{page-1}"))
    if page < total_pages - 1:
        row1.append(InlineKeyboardButton(text="След. »", callback_data=f"orders_page_{page+1}"))

    row2 = [
        InlineKeyboardButton(text="← Назад к выбору периода", callback_data="orders_back_to_filter"),
    ]

    kb_lines = [row1] if row1 else []
    kb_lines.append(row2)

    return InlineKeyboardMarkup(inline_keyboard=kb_lines)


@router.callback_query(F.data == "admin_view_orders")
async def admin_view_orders(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return

    await state.set_state(AdminStates.viewing_orders)
    await state.update_data(
        orders_period=None,
        orders_date_from=None,
        orders_date_to=None
    )

    text = "<b>Просмотр заказов</b>\n\nВыберите период:"
    kb = get_orders_filter_kb()

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("orders_filter_"))
async def process_orders_filter(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return

    filter_type = callback.data.replace("orders_filter_", "")

    if filter_type in ("today", "3days", "week", "all"):
        period = None if filter_type == "all" else filter_type
        await state.update_data(
            orders_period=period,
            orders_date_from=None,
            orders_date_to=None
        )
        await show_orders_page(callback, state, page=0)

    elif filter_type == "custom":
        await callback.message.edit_text(
            "Введите дату <b>от</b> в формате ДД.ММ.ГГГГ\n(или /cancel для отмены)",
            parse_mode="HTML"
        )
        await state.set_state(AdminStates.choosing_date_from)


@router.message(AdminStates.choosing_date_from)
async def process_date_from(message: Message, state: FSMContext):
    text = message.text.strip()
    
    if text == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=admin_main_kb())
        return

    try:
        datetime.datetime.strptime(text, "%d.%m.%Y")
        await state.update_data(orders_date_from=text)
        await message.answer(
            "Теперь введите дату <b>до</b> в формате ДД.ММ.ГГГГ\n(или /cancel)",
            parse_mode="HTML"
        )
        await state.set_state(AdminStates.choosing_date_to)
    except ValueError:
        await message.answer(
            "Неверный формат даты. Используйте ДД.ММ.ГГГГ\nПример: 15.03.2025"
        )


@router.message(AdminStates.choosing_date_to)
async def process_date_to(message: Message, state: FSMContext):
    text = message.text.strip()
    
    if text == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=admin_main_kb())
        return

    try:
        dt_to = datetime.datetime.strptime(text, "%d.%m.%Y")
        data = await state.get_data()
        date_from_str = data.get("orders_date_from")

        if date_from_str:
            dt_from = datetime.datetime.strptime(date_from_str, "%d.%m.%Y")
            if dt_to < dt_from:
                await message.answer("Дата «до» не может быть раньше даты «от».")
                return

        await state.update_data(
            orders_date_to=text,
            orders_period=None
        )
        await show_orders_page(message, state, page=0)

    except ValueError:
        await message.answer(
            "Неверный формат даты. Используйте ДД.ММ.ГГГГ\nПример: 15.03.2025"
        )


@router.callback_query(F.data.startswith("orders_page_"))
async def process_orders_pagination(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return

    try:
        page = int(callback.data.replace("orders_page_", ""))
        await show_orders_page(callback, state, page=page)
    except Exception as e:
        print(f"Ошибка пагинации: {e}")
        await callback.answer("Ошибка переключения страницы", show_alert=True)


@router.callback_query(F.data == "orders_back_to_filter")
async def back_to_orders_filter(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return

    await state.clear()  # Очищаем состояние при возврате к выбору периода

    text = "<b>Просмотр заказов</b>\n\nВыберите период:"
    kb = get_orders_filter_kb()

    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        await callback.answer("Вы уже в меню выбора периода")


@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await callback.answer()

    text = "📤 Введите сообщение для рассылки всем пользователям бота:"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Отмена", callback_data="admin_back")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await state.set_state(AdminStates.waiting_broadcast_message)


@router.message(AdminStates.waiting_broadcast_message)
async def process_broadcast_message(message: Message, state: FSMContext, bot: Bot):
    if not await is_admin(message.from_user.id):
        return

    broadcast_message = message.text.strip()
    if not broadcast_message:
        await message.answer("Сообщение не может быть пустым. Повторите ввод:")
        return

    user_ids = get_all_user_ids()
    sent_count = 0
    for user_id in user_ids:
        try:
            await bot.send_message(int(user_id), broadcast_message)
            sent_count += 1
        except Exception as e:
            print(f"Ошибка отправки пользователю {user_id}: {e}")

    await message.answer(f"✅ Рассылка завершена. Отправлено {sent_count} пользователям.", reply_markup=admin_main_kb())
    await state.clear()


# Новое: подменю промокодов
@router.callback_query(F.data == "admin_promos")
async def admin_promos(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await callback.message.edit_text("Управление промокодами", reply_markup=admin_promos_kb())
    await state.set_state(AdminStates.managing_promos)

# Новое: просмотр конкретного промокода
@router.callback_query(F.data.startswith("admin_view_promo_"))
async def admin_view_promo(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    promo_id = int(callback.data[len("admin_view_promo_"):])
    promos = get_promos()
    promo = next((p for p in promos if p[0] == promo_id), None)
    if not promo:
        await callback.answer("Промокод не найден", show_alert=True)
        return
    _, name, code, min_sum, promo_type, item_id, discount = promo
    text = f"Промокод: {name} ({code})\nУсловие: от {min_sum} ₽\nТип: {'Бесплатная позиция' if promo_type == 'item' else 'Скидка'}\n"
    if promo_type == 'item':
        item = get_menu_item_by_id(item_id)
        text += f"Позиция: {item['name']} (бесплатно)"
    else:
        text += f"Скидка: {discount} ₽"
    await callback.message.edit_text(text, reply_markup=admin_promo_actions_kb(promo_id))

# Новое: статистика по промокоду
@router.callback_query(F.data.startswith("admin_promo_stats_"))
async def admin_promo_stats(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    promo_id = int(callback.data[len("admin_promo_stats_"):])
    promos = get_promos()
    promo = next((p for p in promos if p[0] == promo_id), None)
    if not promo:
        await callback.answer("Промокод не найден", show_alert=True)
        return
    code = promo[2]
    count = get_promo_stats(code)
    await callback.answer(f"Использовано: {count} раз в завершенных заказах", show_alert=True)

# Новое: удаление промокода
@router.callback_query(F.data.startswith("admin_delete_promo_"))
async def admin_delete_promo(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    promo_id = int(callback.data[len("admin_delete_promo_"):])
    delete_promo(promo_id)
    await callback.message.edit_text("Промокод удален", reply_markup=admin_promos_kb())

# Новое: начало добавления промокода
@router.callback_query(F.data == "admin_add_promo")
async def admin_add_promo_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await callback.message.edit_text("Введите название промокода:")
    await state.set_state(AdminStates.adding_promo_name)

@router.message(AdminStates.adding_promo_name)
async def admin_add_promo_code(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    await state.update_data(promo_name=message.text.strip())
    await message.answer("Введите сам промокод (строку):")
    await state.set_state(AdminStates.adding_promo_code)

@router.message(AdminStates.adding_promo_code)
async def admin_add_promo_min_sum(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    code = message.text.strip().upper()
    if get_promo_by_code(code):
        await message.answer("Промокод уже существует! Введите другой.")
        return
    await state.update_data(promo_code=code)
    await message.answer("Введите минимальную сумму заказа (без доставки, только цифры):")
    await state.set_state(AdminStates.adding_promo_min_sum)

@router.message(AdminStates.adding_promo_min_sum)
async def admin_add_promo_type(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    if not message.text.strip().isdigit():
        await message.answer("Ошибка: сумма должна быть числом!")
        return
    await state.update_data(promo_min_sum=int(message.text.strip()))
    await message.answer("Выберите тип промокода:", reply_markup=promo_type_kb())
    await state.set_state(AdminStates.adding_promo_type)

@router.callback_query(F.data.startswith("admin_promo_type_"))
async def admin_add_promo_type_selected(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    promo_type = callback.data[len("admin_promo_type_"):]
    await state.update_data(promo_type=promo_type)
    if promo_type == "discount":
        await callback.message.edit_text("Введите сумму скидки (рублей, только цифры):")
        await state.set_state(AdminStates.adding_promo_discount)
    else:  # item
        await callback.message.edit_text("Выберите категорию для позиции:", reply_markup=admin_promo_categories_kb())
        await state.set_state(AdminStates.choosing_promo_item_category)

@router.message(AdminStates.adding_promo_discount)
async def admin_add_promo_finish_discount(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    if not message.text.strip().isdigit():
        await message.answer("Ошибка: сумма должна быть числом!")
        return
    data = await state.get_data()
    create_promo(data["promo_name"], data["promo_code"], data["promo_min_sum"], "discount", discount=int(message.text.strip()))
    await message.answer("Промокод создан!", reply_markup=admin_promos_kb())
    await state.clear()

@router.callback_query(F.data.startswith("admin_promo_cat_"))
async def admin_promo_select_category(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    category = callback.data[len("admin_promo_cat_"):]
    menu_list = read_menu()
    items = next((cat["items"] for cat in menu_list if cat["category"] == category), None)
    if not items:
        await callback.answer("Категория пустая")
        return
    await state.update_data(promo_category=category, promo_items=items)
    text = f"Выберите позицию в {category}:"
    await callback.message.edit_text(text, reply_markup=admin_promo_items_kb(items))

@router.callback_query(F.data.startswith("admin_promo_select_item_"))
async def admin_add_promo_finish_item(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    data = await state.get_data()
    items = data.get("promo_items")
    try:
        index = int(callback.data[len("admin_promo_select_item_"):])
        item = items[index]
    except:
        await callback.answer("Ошибка выбора")
        return
    # Получаем item_id из БД (нужно добавить id в read_menu, но для простоты предположим, что read_menu возвращает с id; иначе перепишите read_menu)
    # Внимание: нужно обновить read_menu чтобы возвращать id для items!
    # Для этого изменим read_menu:
    # В read_menu: items = [{"id": row[0], "name": row[1], "price": row[2], "desc": row[3]} for row in cur.fetchall() где SELECT id, name, price, desc
    # (Добавьте в db.py: cur.execute("SELECT id, name, price, desc FROM menu_items WHERE category_id = ? ORDER BY id", (cat_id,)))
    item_id = item["id"]  # Предполагаем, что добавлено
    create_promo(data["promo_name"], data["promo_code"], data["promo_min_sum"], "item", item_id=item_id)
    await callback.message.edit_text(f"Промокод создан с позицией {item['name']}!", reply_markup=admin_promos_kb())
    await state.clear()

@router.callback_query(F.data == "admin_promo_categories")
async def admin_promo_back_to_categories(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Выберите категорию для позиции:", reply_markup=admin_promo_categories_kb())