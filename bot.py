import os
import sqlite3
import json
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import openpyxl


# === Конфигурация ===
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')  # Получаем из переменной окружения
ADMIN_ID = int(os.getenv('ADMIN_ID'))     # ID администратора
MAX_USERS = 5                             # Максимальное количество пользователей
DB_NAME = 'meter_readings.db'             # Имя файла базы данных SQLite


# === Инициализация БД ===
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    phone TEXT,
                    flat TEXT,
                    meter_id TEXT UNIQUE,
                    tariff TEXT)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meter_id TEXT,
                    value_json TEXT,
                    date DATETIME)''')
    conn.commit()
    conn.close()


# === Работа с БД: пользователи ===
def get_user_by_id(user_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = cur.fetchone()
    conn.close()
    return user


def get_all_users():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id, flat, meter_id FROM users")
    rows = cur.fetchall()
    conn.close()
    return rows


def delete_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT meter_id FROM users WHERE user_id=?", (user_id,))
    meter_id = cur.fetchone()
    if meter_id:
        meter_id = meter_id[0]
        cur.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        cur.execute("DELETE FROM readings WHERE meter_id=?", (meter_id,))
        conn.commit()
    conn.close()


def add_user(user_id, phone, flat, meter_id, tariff):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?)",
                (user_id, phone, flat, meter_id, tariff))
    conn.commit()
    conn.close()


# === Работа с БД: показания ===
def add_reading(meter_id, values_dict):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    values_json = json.dumps(values_dict)
    cur.execute("INSERT INTO readings (meter_id, value_json, date) VALUES (?, ?, ?)",
                (meter_id, values_json, datetime.now()))
    conn.commit()
    conn.close()


# === Формирование Excel файла ===
def save_to_excel():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Показания"
    ws.append(['Номер квартиры', 'Предыдущие показания', 'Текущие показания', 'Номер телефона', 'Дата'])

    seen = {}  # {meter_id: {flat, phone, prev, current, date}}

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('''SELECT u.flat, r.meter_id, r.value_json, u.phone, r.date 
                   FROM readings r
                   JOIN users u ON r.meter_id = u.meter_id
                   ORDER BY r.date DESC''')
    rows = cur.fetchall()
    conn.close()

    for row in rows:
        flat, meter, value_json, phone, date_str = row
        date = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        values = json.loads(value_json)

        if meter not in seen:
            seen[meter] = {
                "flat": flat,
                "phone": phone,
                "values": [values],
                "date": date
            }
        else:
            seen[meter]["values"].append(values)
            seen[meter]["date"] = date

    for meter in seen:
        data = seen[meter]
        values_list = data["values"]
        last_values = values_list[0]

        if len(values_list) > 1:
            prev_values = values_list[1]
        else:
            prev_values = {}

        def format_value(val):
            return ", ".join([f"{k}: {v}" for k, v in val.items()]) if isinstance(val, dict) else str(val)

        ws.append([
            data["flat"],
            format_value(prev_values),
            format_value(last_values),
            data["phone"],
            data["date"].strftime("%d.%m.%Y")
        ])

    file_path = "report.xlsx"
    wb.save(file_path)
    return file_path


# === Клавиатура ===
def get_main_menu_keyboard(user_id):
    buttons = [
        [InlineKeyboardButton("Меню", callback_data='main_menu')]
    ]
    return InlineKeyboardMarkup(buttons)


def get_full_menu_keyboard(user_id):
    buttons = []

    if not get_user_by_id(user_id):
        buttons.append([InlineKeyboardButton("Зарегистрироваться", callback_data='register')])

    buttons.append([InlineKeyboardButton("Передать показания", callback_data='submit_reading')])

    if user_id == ADMIN_ID:
        buttons += [
            [InlineKeyboardButton("Выгрузить данные", callback_data='export')],
            [InlineKeyboardButton("Удалить пользователя", callback_data='delete_user')]
        ]

    buttons.append([InlineKeyboardButton("<< Назад", callback_data='back_to_start')])
    return InlineKeyboardMarkup(buttons)


# === Обработчики команд ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Добро пожаловать!", reply_markup=get_main_menu_keyboard(update.effective_user.id))


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    await query.answer()
    await query.message.reply_text("Выберите действие:", reply_markup=get_full_menu_keyboard(user.id))


async def back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.delete()
    await query.message.reply_text("Главное меню:", reply_markup=get_main_menu_keyboard(query.from_user.id))


# === Обработка кнопок ===
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    await query.answer()

    if query.data == 'main_menu':
        await menu_handler(update, context)

    elif query.data == 'back_to_start':
        await back_to_start(update, context)

    elif query.data == 'register':
        users = get_all_users()
        if len(users) >= MAX_USERS:
            await query.message.reply_text("Регистрация невозможна — достигнут лимит пользователей.")
            return

        if get_user_by_id(user.id):
            await query.message.reply_text("Вы уже зарегистрированы.")
            return

        context.user_data['registration_step'] = 'phone'
        await query.message.reply_text("Введите ваш номер телефона:")

    elif query.data == 'submit_reading':
        if not get_user_by_id(user.id):
            await query.message.reply_text("Сначала зарегистрируйтесь.")
            return

        user_data = get_user_by_id(user.id)
        meter_id = user_data[3]
        tariff = user_data[4]

        context.user_data['reading'] = {
            'tariff': tariff,
            'meter_id': meter_id,
            'values': [],
            'step': 0
        }

        if tariff == 'суточный':
            await query.message.reply_text("Введите общее значение:")
        elif tariff == 'двухтарифный':
            await query.message.reply_text("Введите показания пиковой зоны:")
        elif tariff == 'трехтарифный':
            await query.message.reply_text("Введите показания пиковой зоны:")

    elif query.data == 'export':
        if user.id != ADMIN_ID:
            return

        file_path = save_to_excel()
        with open(file_path, 'rb') as f:
            await context.bot.send_document(chat_id=user.id, document=f, filename="report.xlsx")

    elif query.data == 'delete_user':
        if user.id != ADMIN_ID:
            return
        users = get_all_users()
        if not users:
            await query.message.reply_text("Нет пользователей для удаления.")
            return

        keyboard = []
        for user_row in users:
            user_id, flat, meter = user_row
            btn_text = f"ID: {user_id} | Квартира: {flat} | Прибор: {meter}"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f'delete_{user_id}')])
        keyboard.append([InlineKeyboardButton("<< Отмена", callback_data='cancel_delete')])
        await query.message.edit_text("Выберите пользователя для удаления:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith('delete_'):
        target_id = int(query.data.split('_')[1])
        delete_user(target_id)
        await query.message.edit_text(f"Пользователь {target_id} удален.")
        await query.message.reply_text("Меню:", reply_markup=get_full_menu_keyboard(ADMIN_ID))

    elif query.data == 'cancel_delete':
        await query.message.delete()
        await query.message.reply_text("Удаление отменено.", reply_markup=get_full_menu_keyboard(ADMIN_ID))


# === Обработка текстовых сообщений ===
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    # --- Регистрация ---
    if 'registration_step' in context.user_data:
        step = context.user_data['registration_step']

        if step == 'phone':
            context.user_data['phone'] = text
            context.user_data['registration_step'] = 'flat'
            await update.message.reply_text("Введите номер вашей квартиры:")

        elif step == 'flat':
            context.user_data['flat'] = text
            context.user_data['registration_step'] = 'meter'
            await update.message.reply_text("Введите номер прибора учета электроэнергии:")

        elif step == 'meter':
            meter_id = text
            conn = sqlite3.connect(DB_NAME)
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM users WHERE meter_id=?", (meter_id,))
            exists = cur.fetchone()
            conn.close()
            if exists:
                await update.message.reply_text("Прибор с таким номером уже зарегистрирован.")
                return
            context.user_data['meter'] = meter_id
            context.user_data['registration_step'] = 'tariff'
            tariff_kb = [['суточный', 'двухтарифный', 'трехтарифный']]
            reply_markup = InlineKeyboardMarkup(tariff_kb)
            await update.message.reply_text("Выберите тариф:", reply_markup=reply_markup)

        elif step == 'tariff':
            tariff = text.lower()
            if tariff not in ['суточный', 'двухтарифный', 'трехтарифный']:
                await update.message.reply_text("Неверный тариф. Попробуйте снова.")
                return

            add_user(user.id, context.user_data['phone'], context.user_data['flat'],
                     context.user_data['meter'], tariff)
            del context.user_data['registration_step']
            await update.message.reply_text("Вы успешно зарегистрированы!")

    # --- Ввод показаний ---
    elif 'reading' in context.user_data:
        reading_data = context.user_data['reading']
        tariff = reading_data['tariff']
        values = reading_data['values']
        step = reading_data['step']

        try:
            value = int(text)
        except ValueError:
            await update.message.reply_text("Показание должно быть целым числом. Повторите ввод:")
            return

        values.append(value)
        reading_data['step'] += 1

        if tariff == 'суточный' and len(values) == 1:
            add_reading(reading_data['meter_id'], {"total": values[0]})
            del context.user_data['reading']
            await update.message.reply_text("Показание сохранено.")

        elif tariff == 'двухтарифный' and len(values) < 2:
            if step == 1:
                await update.message.reply_text("Введите показания ночной зоны:")

        elif tariff == 'двухтарифный' and len(values) == 2:
            add_reading(reading_data['meter_id'], {"peak": values[0], "night": values[1]})
            del context.user_data['reading']
            await update.message.reply_text("Показания (пик и ночь) сохранены.")

        elif tariff == 'трехтарифный' and len(values) < 3:
            if step == 1:
                await update.message.reply_text("Введите показания полупиковой зоны:")
            elif step == 2:
                await update.message.reply_text("Введите показания ночной зоны:")

        elif tariff == 'трехтарифный' and len(values) == 3:
            add_reading(reading_data['meter_id'], {"peak": values[0], "semi_peak": values[1], "night": values[2]})
            del context.user_data['reading']
            await update.message.reply_text("Показания (пик, полупик, ночь) сохранены.")


# === Точка входа ===
if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    print("Бот запущен...")

    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    app.run_polling()
