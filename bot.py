import os
import random
import string
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@your_username")
KASPI_NUMBER = os.getenv("KASPI_NUMBER", "87011885707")
PRICE = os.getenv("PRICE", "1 990")
TRIAL_DAYS = 3
DATABASE_URL = os.getenv("DATABASE_URL")

# ─────────────────────────────────────────────
# БАЗА ДАННЫХ (PostgreSQL)
# ─────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     BIGINT PRIMARY KEY,
            username    TEXT,
            full_name   TEXT,
            joined_at   TEXT,
            trial_ends  TEXT,
            sub_ends    TEXT,
            verify_code TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id          SERIAL PRIMARY KEY,
            user_id     BIGINT,
            amount      REAL,
            category    TEXT,
            label       TEXT,
            note        TEXT,
            created_at  TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            user_id     BIGINT,
            category    TEXT,
            amount      REAL,
            PRIMARY KEY (user_id, category)
        )
    """)
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id, username, full_name, joined_at, trial_ends, sub_ends, verify_code FROM users WHERE user_id=%s", (user_id,))
    row = c.fetchone()
    conn.close()
    return row

def register_user(user_id, username, full_name):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    trial_ends = (datetime.utcnow() + timedelta(days=TRIAL_DAYS)).isoformat()
    c.execute("""
        INSERT INTO users (user_id, username, full_name, joined_at, trial_ends)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (user_id) DO NOTHING
    """, (user_id, username, full_name, now, trial_ends))
    conn.commit()
    conn.close()

def is_active(user_id):
    if user_id == ADMIN_ID:
        return True
    user = get_user(user_id)
    if not user:
        return False
    now = datetime.utcnow()
    trial_ends = datetime.fromisoformat(user[4]) if user[4] else None
    sub_ends   = datetime.fromisoformat(user[5]) if user[5] else None
    if sub_ends and now < sub_ends:
        return True
    if trial_ends and now < trial_ends:
        return True
    return False

def generate_code(user_id):
    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET verify_code=%s WHERE user_id=%s", (code, user_id))
    conn.commit()
    conn.close()
    return code

def confirm_payment(code):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE verify_code=%s", (code,))
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    user_id = row[0]
    sub_ends = (datetime.utcnow() + timedelta(days=30)).isoformat()
    c.execute("UPDATE users SET sub_ends=%s, verify_code=NULL WHERE user_id=%s", (sub_ends, user_id))
    conn.commit()
    conn.close()
    return user_id

def add_expense(user_id, amount, category, label="", note=""):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    c.execute("""
        INSERT INTO expenses (user_id, amount, category, label, note, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (user_id, amount, category, label, note, now))
    conn.commit()
    conn.close()

def get_expenses(user_id, days=30):
    conn = get_conn()
    c = conn.cursor()
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    c.execute("""
        SELECT amount, category, label, note, created_at FROM expenses
        WHERE user_id=%s AND created_at >= %s
        ORDER BY created_at DESC
    """, (user_id, since))
    rows = c.fetchall()
    conn.close()
    return rows

def get_today_expenses(user_id):
    conn = get_conn()
    c = conn.cursor()
    since = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    c.execute("""
        SELECT amount, category, label, note, created_at FROM expenses
        WHERE user_id=%s AND created_at >= %s
        ORDER BY created_at DESC
    """, (user_id, since))
    rows = c.fetchall()
    conn.close()
    return rows

def get_budget(user_id, category):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT amount FROM budgets WHERE user_id=%s AND category=%s", (user_id, category))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def set_budget(user_id, category, amount):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO budgets (user_id, category, amount) VALUES (%s, %s, %s)
        ON CONFLICT (user_id, category) DO UPDATE SET amount=%s
    """, (user_id, category, amount, amount))
    conn.commit()
    conn.close()

# ─────────────────────────────────────────────
# ИНТЕРФЕЙС
# ─────────────────────────────────────────────

DEFAULT_CATEGORIES = [
    "🍔 Еда", "🚗 Транспорт", "🏠 Жильё", "💊 Здоровье",
    "🎮 Развлечения", "👗 Покупки", "📚 Образование", "💡 Коммуналка", "➕ Другое"
]

def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["➕ Добавить расход", "📊 Отчёт"],
            ["📅 Сегодня", "📈 Статистика"],
            ["⚙️ Настройки", "💳 Подписка"],
        ],
        resize_keyboard=True
    )

def category_keyboard():
    buttons = []
    row = []
    for cat in DEFAULT_CATEGORIES:
        row.append(InlineKeyboardButton(cat, callback_data=f"cat:{cat}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cat:cancel")])
    return InlineKeyboardMarkup(buttons)

def report_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("7 дней", callback_data="report:7"),
            InlineKeyboardButton("30 дней", callback_data="report:30"),
            InlineKeyboardButton("90 дней", callback_data="report:90"),
        ]
    ])

def paywall_text(user_id):
    code = generate_code(user_id)
    return (
        f"⏸ Ваш пробный период закончился.\n\n"
        f"Чтобы продолжить пользоваться ботом:\n"
        f"1. Оплатите *{PRICE} ₸* на Kaspi: `{KASPI_NUMBER}`\n"
        f"2. Напишите {ADMIN_USERNAME} с кодом ниже\n\n"
        f"Код для проверки: `{code}`\n\n"
        f"_Подписка действует 30 дней._"
    )

# ─────────────────────────────────────────────
# ОБРАБОТЧИКИ
# ─────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.username, user.full_name)
    db_user = get_user(user.id)
    trial_ends = datetime.fromisoformat(db_user[4])
    days_left = max((trial_ends - datetime.utcnow()).days + 1, 0)

    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        f"Добро пожаловать в *SpendBot* — твой личный трекер расходов.\n\n"
        f"🎁 У тебя *{days_left} дня* бесплатного доступа.\n\n"
        f"Начни прямо сейчас:\n"
        f"• Нажми *➕ Добавить расход*\n"
        f"• Или напиши: `1500 Еда кофе`\n\n"
        f"Используй меню внизу для навигации.",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

async def check_access(update: Update) -> bool:
    user_id = update.effective_user.id
    if is_active(user_id):
        return True
    await update.message.reply_text(paywall_text(user_id), parse_mode="Markdown")
    return False

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if text == "➕ Добавить расход":
        if not await check_access(update): return
        ctx.user_data["adding"] = True
        await update.message.reply_text("Выбери категорию:", reply_markup=category_keyboard())
        return

    if text == "📊 Отчёт":
        if not await check_access(update): return
        await update.message.reply_text("Выбери период:", reply_markup=report_keyboard())
        return

    if text == "📅 Сегодня":
        if not await check_access(update): return
        await show_today(update, ctx)
        return

    if text == "📈 Статистика":
        if not await check_access(update): return
        await show_stats(update, ctx)
        return

    if text == "💳 Подписка":
        await update.message.reply_text(paywall_text(user_id), parse_mode="Markdown")
        return

    if text == "⚙️ Настройки":
        if not await check_access(update): return
        await settings_menu(update, ctx)
        return

    # Сначала проверяем — ждём ли сумму после выбора категории кнопкой
    if ctx.user_data.get("waiting_amount"):
        try:
            parts = text.split(maxsplit=1)
            amount = float(parts[0].replace(",", "."))
            note = parts[1] if len(parts) > 1 else ""
            category = ctx.user_data.pop("waiting_amount")
            ctx.user_data.pop("adding", None)
            add_expense(user_id, amount, category, note=note)

            budget = get_budget(user_id, category)
            budget_msg = ""
            if budget:
                spent = sum(r[0] for r in get_expenses(user_id, 30) if r[1] == category)
                pct = spent / budget * 100
                if pct >= 90:
                    budget_msg = f"\n⚠️ Использовано {pct:.0f}% бюджета на {category}!"

            undo_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Удалить", callback_data="undo_last")]])
            await update.message.reply_text(
                f"✅ *{amount:,.0f} ₸* — {category}" + (f"\n📝 {note}" if note else "") + budget_msg,
                parse_mode="Markdown",
                reply_markup=undo_btn
            )
        except:
            await update.message.reply_text("Введи сумму, например: `1500`", parse_mode="Markdown")
        return

    # Быстрый ввод без кнопок: "1500 Еда кофе"
    parts = text.split(maxsplit=2)
    if parts and parts[0].replace(".", "").replace(",", "").isdigit():
        if not await check_access(update): return
        amount = float(parts[0].replace(",", "."))
        category = parts[1] if len(parts) > 1 else "Другое"
        note = parts[2] if len(parts) > 2 else ""
        add_expense(user_id, amount, category, note=note)

        budget = get_budget(user_id, category)
        budget_msg = ""
        if budget:
            spent = sum(r[0] for r in get_expenses(user_id, 30) if r[1] == category)
            pct = spent / budget * 100
            if pct >= 90:
                budget_msg = f"\n⚠️ Использовано {pct:.0f}% бюджета на {category}!"

        undo_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Удалить", callback_data="undo_last")]])
        await update.message.reply_text(
            f"✅ *{amount:,.0f} ₸* — {category}" + (f"\n📝 {note}" if note else "") + budget_msg,
            parse_mode="Markdown",
            reply_markup=undo_btn
        )
        return

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data.startswith("cat:"):
        cat = data[4:]
        if cat == "cancel":
            ctx.user_data.pop("adding", None)
            await query.edit_message_text("Отменено.")
            return
        ctx.user_data["waiting_amount"] = cat
        await query.edit_message_text(
            f"Категория: *{cat}*\n\nВведи сумму (и заметку по желанию):\n`1500` или `1500 кофе доодо`",
            parse_mode="Markdown"
        )

    elif data == "undo_last":
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT id, amount, category FROM expenses WHERE user_id=%s ORDER BY id DESC LIMIT 1", (user_id,))
        row = c.fetchone()
        if row:
            c.execute("DELETE FROM expenses WHERE id=%s", (row[0],))
            conn.commit()
            await query.edit_message_text(f"🗑 Удалено: *{row[2]}* — {row[1]:,.0f} ₸", parse_mode="Markdown")
        else:
            await query.edit_message_text("Нет расходов для удаления.")
        conn.close()

    elif data.startswith("report:"):
        days = int(data[7:])
        await show_report(query, user_id, days)

async def show_report(query, user_id, days):
    rows = get_expenses(user_id, days)
    if not rows:
        await query.edit_message_text(f"Расходов за последние {days} дней нет.")
        return

    by_cat = {}
    for amount, category, label, note, created_at in rows:
        by_cat[category] = by_cat.get(category, 0) + amount

    total = sum(by_cat.values())
    lines = [f"📊 *За {days} дней — {total:,.0f} ₸*\n"]
    for cat, amt in sorted(by_cat.items(), key=lambda x: -x[1]):
        pct = amt / total * 100
        bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
        lines.append(f"`{bar}` {cat}: *{amt:,.0f} ₸* ({pct:.0f}%)")

    await query.edit_message_text("\n".join(lines), parse_mode="Markdown")

async def show_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rows = get_today_expenses(user_id)
    if not rows:
        await update.message.reply_text("Сегодня расходов нет. Начни отслеживать!")
        return

    total = sum(r[0] for r in rows)
    lines = [f"📅 *Сегодня — {total:,.0f} ₸*\n"]
    for amount, category, label, note, created_at in rows:
        time_str = datetime.fromisoformat(created_at).strftime("%H:%M")
        lines.append(f"{time_str} • {category}: *{amount:,.0f} ₸*" + (f" — {note}" if note else ""))

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def show_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rows_30 = get_expenses(user_id, 30)
    rows_7  = get_expenses(user_id, 7)
    today   = get_today_expenses(user_id)

    total_30    = sum(r[0] for r in rows_30)
    total_7     = sum(r[0] for r in rows_7)
    total_today = sum(r[0] for r in today)
    avg_day     = total_30 / 30 if rows_30 else 0
    top_cat     = (
        max(set(r[1] for r in rows_30), key=lambda c: sum(r[0] for r in rows_30 if r[1] == c))
        if rows_30 else "—"
    )

    await update.message.reply_text(
        f"📈 *Твоя статистика*\n\n"
        f"Сегодня: *{total_today:,.0f} ₸*\n"
        f"За 7 дней: *{total_7:,.0f} ₸*\n"
        f"За 30 дней: *{total_30:,.0f} ₸*\n"
        f"Среднее в день: *{avg_day:,.0f} ₸*\n\n"
        f"_Больше всего тратишь на:_ {top_cat}",
        parse_mode="Markdown"
    )

async def settings_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    sub_ends   = user[5]
    trial_ends = user[4]
    status = "—"
    if update.effective_user.id == ADMIN_ID:
        status = "👑 Администратор (безлимитный доступ)"
    elif sub_ends:
        status = f"Pro до {sub_ends[:10]}"
    elif trial_ends:
        days_left = (datetime.fromisoformat(trial_ends) - datetime.utcnow()).days + 1
        status = f"Пробный период — осталось {max(days_left, 0)} дн."

    await update.message.reply_text(
        f"⚙️ *Настройки*\n\n"
        f"Статус: {status}\n\n"
        f"*Установить бюджет:*\n`/budget Еда 30000`\n`/budget Транспорт 15000`\n\n"
        f"*Удалить последний расход:*\n`/undo`",
        parse_mode="Markdown"
    )

async def cmd_budget(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("Использование: `/budget Еда 30000`", parse_mode="Markdown")
        return
    category = args[0]
    try:
        amount = float(args[1].replace(",", "."))
    except:
        await update.message.reply_text("Неверная сумма.")
        return
    set_budget(update.effective_user.id, category, amount)
    await update.message.reply_text(f"✅ Бюджет на *{category}*: *{amount:,.0f} ₸/мес*", parse_mode="Markdown")

async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    args = ctx.args
    if not args:
        await update.message.reply_text("Использование: `/add 1500 Еда кофе`", parse_mode="Markdown")
        return
    try:
        amount = float(args[0].replace(",", "."))
        category = args[1] if len(args) > 1 else "Другое"
        note = " ".join(args[2:]) if len(args) > 2 else ""
        add_expense(update.effective_user.id, amount, category, note=note)
        await update.message.reply_text(f"✅ *{amount:,.0f} ₸* — {category}", parse_mode="Markdown")
    except:
        await update.message.reply_text("Использование: `/add 1500 Еда кофе`", parse_mode="Markdown")

async def cmd_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Только для администратора.")
        return
    if not ctx.args:
        await update.message.reply_text("Использование: `/confirm КОД123`", parse_mode="Markdown")
        return
    code = ctx.args[0].upper()
    activated_user_id = confirm_payment(code)
    if activated_user_id:
        await update.message.reply_text(f"✅ Подписка активирована для `{activated_user_id}`", parse_mode="Markdown")
        try:
            await ctx.bot.send_message(
                chat_id=activated_user_id,
                text="🎉 *Твоя подписка активирована!*\nПолный доступ на 30 дней. Спасибо!",
                parse_mode="Markdown"
            )
        except:
            pass
    else:
        await update.message.reply_text(f"❌ Код `{code}` не найден.", parse_mode="Markdown")

async def cmd_subscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(paywall_text(update.effective_user.id), parse_mode="Markdown")

async def cmd_undo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update): return
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, amount, category FROM expenses WHERE user_id=%s ORDER BY id DESC LIMIT 1",
              (update.effective_user.id,))
    row = c.fetchone()
    if row:
        c.execute("DELETE FROM expenses WHERE id=%s", (row[0],))
        conn.commit()
        await update.message.reply_text(f"🗑 Удалено: *{row[2]}* — {row[1]:,.0f} ₸", parse_mode="Markdown")
    else:
        await update.message.reply_text("Нет расходов для удаления.")
    conn.close()

# ─────────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────────

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("budget", cmd_budget))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("confirm", cmd_confirm))
    app.add_handler(CommandHandler("undo", cmd_undo))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
