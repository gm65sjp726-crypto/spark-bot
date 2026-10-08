import asyncio
import hashlib
import html
import logging
import os
import re
import tempfile

from telegram import BotCommand, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)

from spark import db
from spark.ai import generate_deck
from spark.config import (
    ADMIN_IDS, ANTHROPIC_API_KEY, BOT_TOKEN, MAX_TOPIC_LEN, MAX_WISHES_LEN, MONTHLY_SPARKS,
    REFERRAL_BONUS, RESTYLE_COST, SLIDE_OPTIONS, presentation_cost,
)
from spark.pptx_builder import THEMES, build_pptx

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("spark-bot")

# ====== Клавиатуры ======
MAIN_MENU = Kb([
    [Btn("✨ Создать презентацию", callback_data="menu:new")],
    [Btn("⚡ Баланс", callback_data="menu:balance"), Btn("📂 Мои работы", callback_data="menu:history")],
    [Btn("🎁 Пригласить друга", callback_data="menu:invite"), Btn("❓ Помощь", callback_data="menu:help")],
])
CANCEL = Btn("✖️ Отмена", callback_data="cancel")


def slides_kb() -> Kb:
    row = [Btn(f"{n} · ⚡{presentation_cost(n)}", callback_data=f"slides:{n}") for n in SLIDE_OPTIONS]
    return Kb([row[:3], row[3:], [CANCEL]])


def themes_kb(prefix: str) -> Kb:
    btns = [Btn(t.name, callback_data=f"{prefix}{key}") for key, t in THEMES.items()]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    return Kb(rows + [[CANCEL]])


def result_kb(pres_id: int) -> Kb:
    restyle = "🎨 Другой дизайн" + (f" · ⚡{RESTYLE_COST}" if RESTYLE_COST else " · бесплатно")
    return Kb([
        [Btn(restyle, callback_data=f"restyle:{pres_id}")],
        [Btn("🔄 Переписать текст", callback_data=f"regen:{pres_id}")],
        [Btn("✨ Новая презентация", callback_data="menu:new"), Btn("🏠 Меню", callback_data="menu:home")],
    ])


# ====== Вспомогательное ======
FLOW_KEYS = ("step", "topic", "slides", "theme", "wishes")


def reset_flow(ud: dict) -> None:
    for k in FLOW_KEYS:
        ud.pop(k, None)


def safe_filename(topic: str) -> str:
    name = re.sub(r"[^\w\s-]", "", topic, flags=re.UNICODE).strip()
    return (re.sub(r"\s+", "_", name)[:60] or "presentation") + ".pptx"


def summary_text(ud: dict) -> str:
    cost = presentation_cost(ud["slides"])
    wishes = ud.get("wishes") or "—"
    return (
        "<b>Проверь параметры:</b>\n\n"
        f"📝 Тема: <i>{html.escape(ud['topic'])}</i>\n"
        f"📄 Слайдов: <b>{ud['slides']}</b>\n"
        f"🎨 Дизайн: <b>{THEMES[ud['theme']].name}</b>\n"
        f"💬 Пожелания: <i>{html.escape(wishes)}</i>\n\n"
        f"Стоимость: <b>⚡{cost}</b>"
    )


async def reply(update: Update, text: str, kb: Kb | None = None) -> None:
    """Отвечает и на сообщение, и на нажатие кнопки (редактируя сообщение)."""
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
            return
        except Exception:
            pass
    await update.effective_chat.send_message(text, reply_markup=kb, parse_mode=ParseMode.HTML)


# ====== Экраны меню ======
async def show_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reset_flow(context.user_data)
    user = db.get_user(update.effective_user.id, update.effective_user.username)
    await reply(
        update,
        f"⚡ <b>Spark</b> — презентации за минуту.\n\n"
        f"Напиши тему — я придумаю структуру, текст и заметки для выступления и соберу готовый .pptx в красивом дизайне.\n\n"
        f"Баланс: <b>⚡{user['sparks']}</b>",
        MAIN_MENU,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    is_new = db.is_new_user(uid)
    db.get_user(uid, update.effective_user.username)

    if is_new and context.args and context.args[0].startswith("ref_"):
        try:
            ref_id = int(context.args[0][4:])
        except ValueError:
            ref_id = 0
        if ref_id and ref_id != uid and not db.is_new_user(ref_id) and db.set_referrer(uid, ref_id):
            db.add_sparks(uid, REFERRAL_BONUS)
            db.add_sparks(ref_id, REFERRAL_BONUS)
            await update.message.reply_text(f"🎁 Бонус за приглашение: +⚡{REFERRAL_BONUS}")
            try:
                await context.bot.send_message(ref_id, f"🎉 По твоей ссылке пришёл друг! +⚡{REFERRAL_BONUS}")
            except Exception:
                pass
    await show_home(update, context)


async def ask_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reset_flow(context.user_data)
    context.user_data["step"] = "topic"
    await reply(
        update,
        "📝 <b>О чём будет презентация?</b>\n\n"
        "Напиши тему одним сообщением. Чем конкретнее — тем лучше результат.\n"
        "<i>Например: «Влияние ИИ на рынок труда в 2026 году» или «Фотосинтез для 6 класса»</i>",
        Kb([[CANCEL]]),
    )


async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = db.get_user(update.effective_user.id)
    await reply(
        update,
        f"⚡ Баланс: <b>{user['sparks']}</b> Sparks\n\n"
        f"• Каждый месяц баланс пополняется до {MONTHLY_SPARKS}\n"
        f"• 1 слайд = ⚡{presentation_cost(1)}\n"
        f"• Смена дизайна готовой презентации — {'⚡' + str(RESTYLE_COST) if RESTYLE_COST else 'бесплатно'}\n"
        f"• За каждого приглашённого друга — +⚡{REFERRAL_BONUS} тебе и ему",
        Kb([[Btn("🎁 Пригласить друга", callback_data="menu:invite")], [Btn("🏠 Меню", callback_data="menu:home")]]),
    )


async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db.list_presentations(update.effective_user.id)
    if not rows:
        await reply(update, "📂 Здесь пока пусто. Создадим первую?",
                    Kb([[Btn("✨ Создать", callback_data="menu:new")], [Btn("🏠 Меню", callback_data="menu:home")]]))
        return
    btns = [[Btn(f"📄 {r['topic'][:40]} · {r['slides']} сл.", callback_data=f"dl:{r['id']}")] for r in rows]
    await reply(update, "📂 <b>Последние презентации</b>\nНажми, чтобы скачать снова (бесплатно):",
                Kb(btns + [[Btn("🏠 Меню", callback_data="menu:home")]]))


async def show_invite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    link = f"https://t.me/{context.bot.username}?start=ref_{uid}"
    await reply(
        update,
        f"🎁 Приглашай друзей и получайте по <b>⚡{REFERRAL_BONUS}</b> каждый!\n\n"
        f"Твоя ссылка:\n<code>{link}</code>\n\n"
        f"Приглашено: <b>{db.referral_count(uid)}</b>",
        Kb([[Btn("🏠 Меню", callback_data="menu:home")]]),
    )


HELP_TEXT = (
    "❓ <b>Как это работает</b>\n\n"
    "1. Нажми «Создать презентацию» и напиши тему\n"
    "2. Выбери количество слайдов и дизайн\n"
    "3. Добавь пожелания (аудитория, стиль, акценты) — или пропусти\n"
    "4. Получи готовый .pptx — с титульным слайдом, содержанием и заметками докладчика\n\n"
    "Файл открывается в PowerPoint, Google Slides и Keynote, всё можно редактировать.\n"
    "Не понравился дизайн — смени его одной кнопкой, текст сохранится.\n\n"
    "Команды: /new — новая презентация, /balance — баланс, /history — мои работы, /cancel — отмена"
)


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(update, HELP_TEXT, Kb([[Btn("🏠 Меню", callback_data="menu:home")]]))


# ====== Сценарий создания ======
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ud = context.user_data
    text = update.message.text.strip()
    step = ud.get("step")

    if step == "wishes":
        ud["wishes"] = text[:MAX_WISHES_LEN]
        ud["step"] = "confirm"
        await show_confirm(update, context)
        return

    # Любой текст вне сценария считаем новой темой — так быстрее
    if len(text) < 3:
        await update.message.reply_text("Тема слишком короткая — опиши подробнее 🙂")
        return
    reset_flow(ud)
    ud.update(step="slides", topic=text[:MAX_TOPIC_LEN])
    await update.message.reply_text(
        f"📝 Тема: <i>{html.escape(ud['topic'])}</i>\n\n📄 <b>Сколько слайдов?</b> (включая титульный и финальный)",
        reply_markup=slides_kb(), parse_mode=ParseMode.HTML,
    )


async def on_slides(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ud = context.user_data
    if "topic" not in ud:
        await ask_topic(update, context)
        return
    ud["slides"] = int(update.callback_query.data.split(":")[1])
    ud["step"] = "theme"
    await reply(update, "🎨 <b>Выбери дизайн</b>\nЕго можно будет сменить бесплатно после генерации.", themes_kb("theme:"))


async def on_theme(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ud = context.user_data
    if "slides" not in ud:
        await ask_topic(update, context)
        return
    ud["theme"] = update.callback_query.data.split(":")[1]
    ud["step"] = "wishes"
    await reply(
        update,
        "💬 <b>Есть пожелания?</b>\n\nНапиши, для кого презентация, какой тон, что обязательно упомянуть.\n"
        "<i>Например: «для инвесторов, больше цифр» или «для школьников, простым языком»</i>",
        Kb([[Btn("⏭ Пропустить", callback_data="wishes:skip")], [CANCEL]]),
    )


async def on_skip_wishes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ud = context.user_data
    if "theme" not in ud:
        await ask_topic(update, context)
        return
    ud["wishes"] = ""
    ud["step"] = "confirm"
    await show_confirm(update, context)


async def show_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ud = context.user_data
    await reply(update, summary_text(ud), Kb([
        [Btn("🚀 Создать", callback_data="go")],
        [Btn("✏️ Изменить тему", callback_data="menu:new"), CANCEL],
    ]))


async def generate_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE,
                            topic: str, slides: int, theme: str, wishes: str) -> None:
    uid = update.effective_user.id
    chat = update.effective_chat
    cost = presentation_cost(slides)

    if not db.try_spend(uid, cost):
        user = db.get_user(uid)
        await reply(update, f"⛔ Не хватает Sparks: нужно ⚡{cost}, у тебя ⚡{user['sparks']}.\n"
                            f"Выбери меньше слайдов или пригласи друга (+⚡{REFERRAL_BONUS}).",
                    Kb([[Btn("🎁 Пригласить друга", callback_data="menu:invite")],
                        [Btn("🏠 Меню", callback_data="menu:home")]]))
        return

    if context.user_data.get("busy"):
        db.add_sparks(uid, cost)
        await chat.send_message("⏳ Уже делаю предыдущую презентацию, подожди немного.")
        return
    context.user_data["busy"] = True

    await reply(update, f"🧠 Придумываю структуру и пишу текст для «{html.escape(topic)}»…\nОбычно это 20–60 секунд.")
    status = update.callback_query.message if update.callback_query else None
    try:
        await chat.send_action(ChatAction.TYPING)
        deck = await asyncio.to_thread(generate_deck, topic, max(slides - 2, 1), wishes)
        if status:
            await status.edit_text("🎨 Оформляю слайды…")
        pres_id = db.save_presentation(uid, topic, slides, theme, deck)
        await send_file(update, context, pres_id, deck, theme, topic)
        if status:
            await status.delete()
    except Exception:
        log.exception("Ошибка генерации")
        db.add_sparks(uid, cost)
        await chat.send_message("😔 Что-то пошло не так. Sparks возвращены — попробуй ещё раз.",
                                reply_markup=MAIN_MENU)
    finally:
        context.user_data.pop("busy", None)


async def send_file(update: Update, context: ContextTypes.DEFAULT_TYPE,
                    pres_id: int, deck: dict, theme: str, topic: str) -> None:
    chat = update.effective_chat
    await chat.send_action(ChatAction.UPLOAD_DOCUMENT)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, safe_filename(topic))
        await asyncio.to_thread(build_pptx, deck, theme, path)
        balance = db.get_user(update.effective_user.id)["sparks"]
        outline = "\n".join(f"{i}. {html.escape(s['title'])}" for i, s in enumerate(deck["slides"], 1))
        caption = (f"✅ <b>{html.escape(deck['title'])}</b>\n🎨 {THEMES[theme].name}\n\n{outline}\n\n"
                   f"Баланс: ⚡{balance}")
        if len(caption) > 1000:
            caption = caption[:990] + "…"
        with open(path, "rb") as f:
            await chat.send_document(f, filename=os.path.basename(path), caption=caption,
                                     parse_mode=ParseMode.HTML, reply_markup=result_kb(pres_id))


async def on_go(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ud = context.user_data
    if not {"topic", "slides", "theme"} <= ud.keys():
        await ask_topic(update, context)
        return
    params = dict(topic=ud["topic"], slides=ud["slides"], theme=ud["theme"], wishes=ud.get("wishes", ""))
    reset_flow(ud)
    await generate_and_send(update, context, **params)


# ====== Действия с готовой презентацией ======
async def on_restyle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pres_id = int(update.callback_query.data.split(":")[1])
    await update.effective_chat.send_message("🎨 Выбери новый дизайн:", reply_markup=themes_kb(f"rt:{pres_id}:"))


async def on_restyle_theme(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, pres_id, theme = update.callback_query.data.split(":")
    uid = update.effective_user.id
    pres = db.get_presentation(int(pres_id), uid)
    if not pres:
        await reply(update, "Презентация не найдена.", MAIN_MENU)
        return
    if RESTYLE_COST and not db.try_spend(uid, RESTYLE_COST):
        await reply(update, f"⛔ Нужно ⚡{RESTYLE_COST} для смены дизайна.", MAIN_MENU)
        return
    await update.callback_query.message.delete()
    new_id = db.save_presentation(uid, pres["topic"], pres["slides"], theme, pres["content"])
    await send_file(update, context, new_id, pres["content"], theme, pres["topic"])


async def on_regen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pres_id = int(update.callback_query.data.split(":")[1])
    pres = db.get_presentation(pres_id, update.effective_user.id)
    if not pres:
        await reply(update, "Презентация не найдена.", MAIN_MENU)
        return
    await update.effective_chat.send_message(
        f"🔄 Переписать текст заново? Стоимость ⚡{presentation_cost(pres['slides'])}.",
        reply_markup=Kb([[Btn("✅ Да", callback_data=f"regen_ok:{pres_id}"), CANCEL]]),
    )


async def on_regen_ok(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pres = db.get_presentation(int(update.callback_query.data.split(":")[1]), update.effective_user.id)
    if not pres:
        await reply(update, "Презентация не найдена.", MAIN_MENU)
        return
    await generate_and_send(update, context, pres["topic"], pres["slides"], pres["theme"],
                            "Сделай другую структуру и формулировки, чем обычно.")


async def on_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pres = db.get_presentation(int(update.callback_query.data.split(":")[1]), update.effective_user.id)
    if not pres:
        await reply(update, "Презентация не найдена.", MAIN_MENU)
        return
    await send_file(update, context, pres["id"], pres["content"], pres["theme"], pres["topic"])


async def on_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_home(update, context)


# ====== Роутинг кнопок ======
MENU = {"new": ask_topic, "balance": show_balance, "history": show_history,
        "invite": show_invite, "help": show_help, "home": show_home}


async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await MENU[update.callback_query.data.split(":")[1]](update, context)


def answering(handler):
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.callback_query.answer()
        await handler(update, context)
    return wrapped


# ====== Админ ======
async def admin_give(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        uid, amount = int(context.args[0]), int(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("Использование: /give <user_id> <кол-во>")
        return
    db.get_user(uid)
    db.add_sparks(uid, amount)
    await update.message.reply_text(f"Готово: {uid} +⚡{amount}")


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        return
    users, pres = db.stats()
    await update.message.reply_text(f"👥 Пользователей: {users}\n📄 Презентаций: {pres}")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Необработанная ошибка", exc_info=context.error)


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start", "Главное меню"),
        BotCommand("new", "Новая презентация"),
        BotCommand("balance", "Баланс Sparks"),
        BotCommand("history", "Мои презентации"),
        BotCommand("help", "Помощь"),
        BotCommand("cancel", "Отмена"),
    ])


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Не задан BOT_TOKEN. Скопируй .env.example в .env и впиши токен.")
    if not ANTHROPIC_API_KEY:
        log.warning("ANTHROPIC_API_KEY не задан — текст слайдов будет шаблонным")

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).concurrent_updates(True).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", ask_topic))
    app.add_handler(CommandHandler("balance", show_balance))
    app.add_handler(CommandHandler("history", show_history))
    app.add_handler(CommandHandler("help", show_help))
    app.add_handler(CommandHandler("cancel", on_cancel))
    app.add_handler(CommandHandler("give", admin_give))
    app.add_handler(CommandHandler("stats", admin_stats))

    for pattern, handler in [
        (r"^menu:", on_menu), (r"^slides:\d+$", on_slides), (r"^theme:\w+$", on_theme),
        (r"^wishes:skip$", on_skip_wishes), (r"^go$", on_go), (r"^cancel$", on_cancel),
        (r"^restyle:\d+$", on_restyle), (r"^rt:\d+:\w+$", on_restyle_theme),
        (r"^regen:\d+$", on_regen), (r"^regen_ok:\d+$", on_regen_ok), (r"^dl:\d+$", on_download),
    ]:
        app.add_handler(CallbackQueryHandler(answering(handler), pattern=pattern))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)

    # На хостинге с публичным адресом (Render и т.п.) работаем через webhook — так бот
    # просыпается от входящего сообщения. Локально — обычный polling.
    public_url = os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL")
    if public_url:
        log.info("Бот запущен (webhook: %s)", public_url)
        app.run_webhook(
            listen="0.0.0.0",
            port=int(os.getenv("PORT", "8080")),
            url_path="telegram",
            webhook_url=public_url.rstrip("/") + "/telegram",
            secret_token=hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:32],
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        log.info("Бот запущен (polling)")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
