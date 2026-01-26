from aiogram.fsm.state import State, StatesGroup


class UserStates(StatesGroup):
    waiting_phone = State()       # ожидание отправки номера телефона
    waiting_address = State()     # ожидание ввода адреса доставки
    waiting_comment = State()     # ожидание комментария к заказу


class AdminStates(StatesGroup):
    adding_category = State()
    choosing_delete_category = State()
    choosing_add_dish_category = State()
    adding_new_category_for_dish = State()
    adding_dish_name = State()
    adding_dish_price = State()
    adding_dish_desc = State()
    choosing_delete_dish_category = State()
    deleting_dish_num = State()

    # Для просмотра заказов
    viewing_orders = State()
    choosing_date_from = State()
    choosing_date_to = State()