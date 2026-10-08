import os
import re
import sqlite3
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from pptx import Presentation
from pptx.dml.color import RGBColor

# ====== НАСТРОЙКИ ======
TOKEN = os.environ.get("BOT_TOKEN")

MONTHLY_SPARKS = 400
SPARKS_RESET_PERIOD = timedelta(days=30)
PRESENTATION_COST = 80
FREE_SLIDE_LIMIT = 7

# стиль: (фон, заголовок, текст)
STYLES = {
    "бизнес": ("FFFFFF", "1F3A5F", "333333"),
    "минимализм": ("FAFAFA", "111111", "555555"),
    "темный": ("1E1E2E", "FFFFFF", "D0D0D0"),
    "яркий": ("FFF3E0", "E65100", "3E2723"),
}

# ====== БАЗА ======
conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    sparks INTEGER,
    last_reset TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS states (
    user_id INTEGER PRIMARY KEY,
    topic TEXT,
    style TEXT,
    font TEXT,
    slides INTEGER
)
""")

conn.commit()

# ====== SPARKS ======
def get_user(user_id):
    cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = cursor.fetchone()
    if not user:
        cursor.execute(
            "INSERT INTO users VALUES (?, ?, ?)",
            (user_id, MONTHLY_SPARKS, datetime.now().isoformat())
        )
        conn.commit()
        return get_user(user_id)
    if datetime.now() - datetime.fromisoformat(user[2]) >= SPARKS_RESET_PERIOD:
        cursor.execute(
            "UPDATE users SET sparks = ?, last_reset = ? WHERE user_id=?",
            (MONTHLY_SPARKS, datetime.now().isoformat(), user_id)
        )
        conn.commit()
        return get_user(user_id)
    return user

def has_enough_sparks(user_id):
    get_user(user_id)
    cursor.execute("SELECT sparks FROM users WHERE user_id=?", (user_id,))
    return cursor.fetchone()[0] >= PRESENTATION_COST

def spend_sparks(user_id):
    cursor.execute(
        "UPDATE users SET sparks = sparks - ? WHERE user_id=?",
        (PRESENTATION_COST, user_id)
    )
    conn.commit()

# ====== PPT ======
def style_text(shape, font, color):
    for p in shape.text_frame.paragraphs:
        for run in p.runs:
            run.font.name = font
            run.font.color.rgb = RGBColor.from_string(color)

def create_presentation(topic, style, font, slides_count, filename):
    prs = Presentation()
    titles = ["Введение", "Проблема", "Решение", "Преимущества", "Вывод"]
    background, title_color, text_color = STYLES[style]
    slides_count = min(slides_count, FREE_SLIDE_LIMIT)

    for i in range(slides_count):
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = RGBColor.from_string(background)
        slide.shapes.title.text = titles[i % len(titles)]
        slide.placeholders[1].text = f"{titles[i % len(titles)]} по теме: {topic}"
        style_text(slide.shapes.title, font, title_color)
        style_text(slide.placeholders[1], font, text_color)

    prs.save(filename)

# ====== BOT ======
def parse_params(text, style, font, slides):
    if text.strip().lower().rstrip(".!") == "нет":
        return style, font, slides

    found = False
    for line in re.split(r"[\n,;]+", text):
        key, sep, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if not sep or not value:
            continue
        if key.startswith("стил"):
            style = value.lower().replace("ё", "е")
            if style not in STYLES:
                raise ValueError(f"Не знаю стиль «{value}». Доступны: {', '.join(STYLES)}")
        elif key.startswith("шрифт"):
            font = value
        elif key.startswith("слайд"):
            slides = int(value) if value.isdecimal() else 0
            if not 1 <= slides <= FREE_SLIDE_LIMIT:
                raise ValueError(f"Слайдов может быть от 1 до {FREE_SLIDE_LIMIT}")
        else:
            continue
        found = True

    if not found:
        raise ValueError(
            "Не понял параметры. Напиши их как в примере или 'нет'.\n"
            "Чтобы сменить тему — /start"
        )
    return style, font, slides

def clear_state(user_id):
    cursor.execute("DELETE FROM states WHERE user_id=?", (user_id,))
    conn.commit()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    get_user(user_id)
    clear_state(user_id)
    await update.message.reply_text("⚡ Напиши тему презентации")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Пока тема сохранена, следующее сообщение — параметры к ней
    cursor.execute("SELECT 1 FROM states WHERE user_id=?", (update.message.from_user.id,))
    if cursor.fetchone():
        await handle_params(update, context)
    else:
        await handle_topic(update, context)

async def handle_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    topic = update.message.text

    cursor.execute(
        "INSERT OR REPLACE INTO states VALUES (?, ?, ?, ?, ?)",
        (user_id, topic, "бизнес", "Arial", 5)
    )
    conn.commit()

    await update.message.reply_text(
        "Напиши параметры или 'нет'\n"
        "Пример:\nСтиль: бизнес\nШрифт: Arial\nСлайдов: 5\n\n"
        f"Стили: {', '.join(STYLES)}\n"
        f"Слайдов: от 1 до {FREE_SLIDE_LIMIT}"
    )

async def handle_params(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    cursor.execute("SELECT * FROM states WHERE user_id=?", (user_id,))
    state = cursor.fetchone()
    if not state:
        return

    try:
        style, font, slides = parse_params(update.message.text, state[2], state[3], state[4])
    except ValueError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return

    if not has_enough_sparks(user_id):
        next_reset = datetime.fromisoformat(get_user(user_id)[2]) + SPARKS_RESET_PERIOD
        await update.message.reply_text(
            f"⛔ Sparks закончились. Новые {MONTHLY_SPARKS} ⚡ начислятся {next_reset:%d.%m.%Y}"
        )
        return

    filename = f"{user_id}.pptx"
    create_presentation(state[1], style, font, slides, filename)

    with open(filename, "rb") as f:
        await update.message.reply_document(f)

    spend_sparks(user_id)
    clear_state(user_id)
    await update.message.reply_text(
        f"✅ Осталось {get_user(user_id)[1]} ⚡\nНапиши тему следующей презентации"
    )

def main():
    if not TOKEN:
        raise SystemExit("Не задан токен: укажи его в переменной окружения BOT_TOKEN")

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE & filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
