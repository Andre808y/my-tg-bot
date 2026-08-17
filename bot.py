import os
import re
import csv
import json
import asyncio
from pathlib import Path
from collections import defaultdict
from http import HTTPStatus

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo,
    ReplyKeyboardMarkup, KeyboardButton, Update,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters,
)

# ---------- настройки ----------
TOKEN = os.environ["BOT_TOKEN"]
GAME_URL = "https://andre808y.github.io/Iphone-jumper/"
SCRATCH_URL = "https://andre808y.github.io/scratch-card-frontend/"
SELLER_CHAT_ID = int(os.environ["SELLER_CHAT_ID"])
PORT = int(os.environ.get("PORT", 10000))
HOSTNAME = os.environ["RENDER_EXTERNAL_HOSTNAME"]
WEBHOOK_URL = f"https://{HOSTNAME}/{TOKEN}"

PRODUCTS_FILE = Path(__file__).parent / "products.csv"
LEADERBOARD_FILE = Path(__file__).parent / "leaderboard.json"
LEADERBOARD_MAX_STORE = 100
LEADERBOARD_MAX_RETURN = 20

BTN_CATALOG = "📂 Каталог по категориям"
BTN_PRICE = "🔍 Найти по названию"
BTN_GAME = "🎮 Играть"
BTN_SCRATCH = "🎫 Скретч-карта"
BTN_CONTACT = "💬 Связаться с продавцом"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton(BTN_CATALOG)], [KeyboardButton(BTN_PRICE)],
     [KeyboardButton(BTN_GAME)], [KeyboardButton(BTN_SCRATCH)],
     [KeyboardButton(BTN_CONTACT)]],
    resize_keyboard=True,
)

CATEGORY_ORDER = [
    "📱 iPhone", "📱 Samsung", "📱 Xiaomi / Poco", "📱 iPad", "💻 MacBook",
    "⌚️ Часы", "🎧 Наушники", "🔌 Чехлы и аксессуары", "🎮 Приставки",
    "🔊 Marshall", "🌀 Dyson", "🗣 Умные колонки", "🗂 Другое",
]

NAME_COLUMNS = ("title", "name", "название", "товар")
PRICE_COLUMNS = ("price", "цена")
CATEGORY_COLUMNS = ("category",)
PARENT_COLUMNS = ("parent uid",)
UID_COLUMNS = ("tilda uid",)


def _pick(row: dict, candidates) -> str:
    for key in row:
        if key and key.strip().lower() in candidates:
            return row[key] or ""
    return ""


def _read_rows() -> list[dict]:
    if not PRODUCTS_FILE.exists():
        return []
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            with open(PRODUCTS_FILE, encoding=encoding, newline="") as f:
                sample = f.read(4096)
                f.seek(0)
                try:
                    delimiter = csv.Sniffer().sniff(sample, delimiters=";,").delimiter
                except csv.Error:
                    delimiter = ";"
                return list(csv.DictReader(f, delimiter=delimiter))
        except UnicodeDecodeError:
            continue
    return []


def top_level_category(tags: list[str], title: str) -> str:
    tags_l = [t.lower() for t in tags]
    title_l = title.lower()
    if "чехлы" in tags_l or "чехол" in title_l or "аксессуары" in tags_l:
        return "🔌 Чехлы и аксессуары"
    if any("iphone" in t for t in tags_l) or "iphone" in title_l:
        return "📱 iPhone"
    if "samsung" in tags_l or "samsung" in title_l:
        return "📱 Samsung"
    if "xiaomi" in tags_l or "xiaomi" in title_l or "poco" in tags_l or "poco" in title_l:
        return "📱 Xiaomi / Poco"
    if "watch" in tags_l or "часы" in tags_l or "apple watch" in title_l:
        return "⌚️ Часы"
    if "macbook" in tags_l or "macbook" in title_l:
        return "💻 MacBook"
    if "ipad" in tags_l or "ipad" in title_l:
        return "📱 iPad"
    if "airpods" in tags_l or "наушники" in tags_l or "airpods" in title_l or "buds" in title_l:
        return "🎧 Наушники"
    if "marshall" in tags_l or "marshall" in title_l:
        return "🔊 Marshall"
    if "приставки" in tags_l or "xbox" in title_l or "playstation" in title_l:
        return "🎮 Приставки"
    if "dyson" in tags_l or "dyson" in title_l:
        return "🌀 Dyson"
    if "алиса" in tags_l or "алиса" in title_l or "яндекс" in title_l:
        return "🗣 Умные колонки"
    return "🗂 Другое"


def base_name(title: str) -> str:
    return re.split(r"\s+-\s+", title)[0].strip()


PHONE_CATEGORIES = {"📱 iPhone", "📱 Samsung", "📱 Xiaomi / Poco"}
_TIER_KEYWORDS = (
    ("ultra", 0), ("pro max", 1), ("max", 1), ("pro", 2), ("plus", 3), ("e", 5),
)


def _phone_sort_key(name: str):
    name_l = name.lower()
    nums = re.findall(r"\d+", name)
    num = int(nums[0]) if nums else -1
    if num == -1 and "air" in name_l:
        num = 17
    tier = 4
    for kw, val in _TIER_KEYWORDS:
        if kw in name_l:
            tier = val
            break
    return (-num, tier, name)


def sort_models(category: str, model_names) -> list[str]:
    if category in PHONE_CATEGORIES:
        return sorted(model_names, key=_phone_sort_key)
    return sorted(model_names)


def load_catalog():
    rows = _read_rows()
    parent_category = {}
    for r in rows:
        uid = _pick(r, UID_COLUMNS).strip()
        cat = _pick(r, CATEGORY_COLUMNS).strip()
        if uid and cat:
            parent_category[uid] = cat

    catalog = defaultdict(lambda: defaultdict(list))
    for r in rows:
        name = _pick(r, NAME_COLUMNS).strip()
        price = _pick(r, PRICE_COLUMNS).strip()
        if not name or not price:
            continue
        own_cat = _pick(r, CATEGORY_COLUMNS).strip()
        parent_uid = _pick(r, PARENT_COLUMNS).strip()
        raw_cat = own_cat or parent_category.get(parent_uid, "")
        tags = [t.strip() for t in raw_cat.split(";") if t.strip()]
        top_cat = top_level_category(tags, name)
        catalog[top_cat][base_name(name)].append({"name": name, "price": price})
    return catalog


def format_price(raw: str) -> str:
    try:
        value = float(raw.replace(",", "."))
        return f"{value:,.0f}".replace(",", " ")
    except ValueError:
        return raw


def search_products(query: str) -> list[dict]:
    query = query.lower().strip()
    catalog = load_catalog()
    results = []
    for models in catalog.values():
        for items in models.values():
            for p in items:
                if query in p["name"].lower():
                    results.append(p)
    return results


# ---------- таблица лидеров ----------

def load_leaderboard() -> list[dict]:
    if not LEADERBOARD_FILE.exists():
        return []
    try:
        with open(LEADERBOARD_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_leaderboard(entries: list[dict]) -> None:
    with open(LEADERBOARD_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)


def add_score(name: str, score: int) -> list[dict]:
    entries = load_leaderboard()
    name = (name or "Игрок").strip()[:32] or "Игрок"
    existing = next((e for e in entries if e["name"] == name), None)
    if existing:
        if score > existing["score"]:
            existing["score"] = score
    else:
        entries.append({"name": name, "score": score})
    entries.sort(key=lambda e: e["score"], reverse=True)
    entries = entries[:LEADERBOARD_MAX_STORE]
    save_leaderboard(entries)
    return entries


# ---------- меню каталога (инлайн-кнопки) ----------

async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    catalog = load_catalog()
    ordered = [c for c in CATEGORY_ORDER if c in catalog] + \
              [c for c in catalog if c not in CATEGORY_ORDER]

    buttons = []
    row = []
    for i, cat in enumerate(ordered):
        count = sum(len(v) for v in catalog[cat].values())
        row.append(InlineKeyboardButton(f"{cat} ({count})", callback_data=f"cat|{i}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    context.chat_data["cat_order"] = ordered

    text = "Выберите категорию:"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def show_models(update: Update, context: ContextTypes.DEFAULT_TYPE, cat_idx: int) -> None:
    catalog = load_catalog()
    ordered = context.chat_data.get("cat_order") or list(catalog.keys())
    cat = ordered[cat_idx]
    models = catalog[cat]
    model_names = sort_models(cat, models.keys())

    context.chat_data["cur_cat"] = cat
    context.chat_data["cur_models"] = model_names

    buttons = []
    row = []
    for i, model in enumerate(model_names):
        items = models[model]
        label = model if len(items) == 1 else f"{model} ({len(items)})"
        if len(items) == 1:
            label = f"{model} — {format_price(items[0]['price'])} ₽"
        row.append(InlineKeyboardButton(label, callback_data=f"model|{i}"))
        if len(row) == 1:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("⬅️ Назад к категориям", callback_data="back_cats")])

    await update.callback_query.edit_message_text(
        f"{cat}\nВыберите модель:", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def show_variants(update: Update, context: ContextTypes.DEFAULT_TYPE, model_idx: int) -> None:
    catalog = load_catalog()
    cat = context.chat_data.get("cur_cat")
    model_names = context.chat_data.get("cur_models") or []
    model = model_names[model_idx]
    items = catalog[cat][model]

    lines = [f"*{model}*"]
    for p in items:
        tail = p["name"][len(model):].lstrip(" -")
        label = tail if tail else p["name"]
        lines.append(f"• {label} — {format_price(p['price'])} ₽")

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Назад к моделям", callback_data="back_models"),
        InlineKeyboardButton("💬 Спросить продавца", callback_data="ask_seller"),
    ]])
    await update.callback_query.edit_message_text(
        "\n".join(lines), reply_markup=keyboard, parse_mode="Markdown"
    )


async def catalog_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "back_cats":
        await show_categories(update, context)
        return
    if data == "back_models":
        ordered = context.chat_data.get("cat_order") or []
        cat = context.chat_data.get("cur_cat")
        cat_idx = ordered.index(cat) if cat in ordered else 0
        await show_models(update, context, cat_idx)
        return
    if data == "ask_seller":
        context.user_data["awaiting"] = "contact"
        await query.message.reply_text(
            "Напишите ваш вопрос или что хотите заказать — сразу перешлю продавцу.",
            reply_markup=MAIN_KEYBOARD,
        )
        return
    if data.startswith("cat|"):
        await show_models(update, context, int(data.split("|")[1]))
        return
    if data.startswith("model|"):
        await show_variants(update, context, int(data.split("|")[1]))
        return


# ---------- обычные обработчики ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("awaiting", None)
    await update.message.reply_text("Здравствуйте! Чем помочь?", reply_markup=MAIN_KEYBOARD)


async def send_game(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🎮 Играть", web_app=WebAppInfo(url=GAME_URL))]]
    )
    await update.message.reply_text(
        "Прыгай через коробки с айфонами! 📦📱", reply_markup=keyboard
    )


async def send_scratch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🎫 Стереть карту", web_app=WebAppInfo(url=SCRATCH_URL))]]
    )
    await update.message.reply_text(
        "Сотри слой пальцем — вдруг там промокод на скидку! 🎁\n"
        "Одна попытка раз в 24 часа.",
        reply_markup=keyboard,
    )


async def ask_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["awaiting"] = "price"
    await update.message.reply_text(
        "Напишите название товара (например: iPhone 15) — посмотрю в прайсе.\n"
        "Или нажмите «📂 Каталог по категориям», чтобы просто полистать список."
    )


async def ask_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["awaiting"] = "contact"
    await update.message.reply_text(
        "Напишите ваш вопрос или что хотите заказать — сразу перешлю продавцу."
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    awaiting = context.user_data.pop("awaiting", None)
    text = update.message.text

    if awaiting == "price":
        results = search_products(text)
        if not results:
            context.user_data["awaiting"] = "contact"
            await update.message.reply_text(
                "Не нашёл такой товар в прайсе. Могу сразу переслать вопрос "
                "продавцу — просто напишите, что вас интересует.",
                reply_markup=MAIN_KEYBOARD,
            )
            return
        lines = [f"• {p['name']} — {format_price(p['price'])} ₽" for p in results[:15]]
        if len(results) > 15:
            lines.append(f"…и ещё {len(results) - 15}. Уточните запрос точнее.")
        await update.message.reply_text("\n".join(lines), reply_markup=MAIN_KEYBOARD)
        return

    if awaiting == "contact":
        user = update.effective_user
        contact_line = f"@{user.username}" if user.username else f"id {user.id}"
        forward_text = f"📩 Новое сообщение от клиента ({contact_line}):\n\n{text}"
        await context.bot.send_message(chat_id=SELLER_CHAT_ID, text=forward_text)
        await update.message.reply_text(
            "Сообщение отправлено продавцу, он скоро с вами свяжется!",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    await update.message.reply_text("Выберите, что нужно:", reply_markup=MAIN_KEYBOARD)


# ---------- веб-сервер (Telegram webhook + API таблицы лидеров) ----------

application: Application = None


async def telegram_webhook_route(request: Request) -> Response:
    data = await request.json()
    await application.update_queue.put(Update.de_json(data=data, bot=application.bot))
    return Response()


async def get_leaderboard_route(request: Request) -> JSONResponse:
    return JSONResponse(load_leaderboard()[:LEADERBOARD_MAX_RETURN])


async def post_leaderboard_route(request: Request) -> JSONResponse:
    try:
        data = await request.json()
        name = str(data.get("name", "Игрок"))[:32]
        score = int(data.get("score", 0))
    except Exception:
        return JSONResponse({"error": "bad request"}, status_code=HTTPStatus.BAD_REQUEST)
    if score < 0 or score > 100000:
        return JSONResponse({"error": "invalid score"}, status_code=HTTPStatus.BAD_REQUEST)
    entries = add_score(name, score)
    return JSONResponse(entries[:LEADERBOARD_MAX_RETURN])


async def health_route(request: Request) -> Response:
    return Response(content="ok")


async def main() -> None:
    global application
    application = Application.builder().token(TOKEN).updater(None).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("game", send_game))
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_CATALOG}$"), show_categories))
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_GAME}$"), send_game))
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_SCRATCH}$"), send_scratch))
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_PRICE}$"), ask_price))
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_CONTACT}$"), ask_contact))
    application.add_handler(CallbackQueryHandler(catalog_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    await application.bot.set_webhook(url=WEBHOOK_URL, allowed_updates=Update.ALL_TYPES)

    starlette_app = Starlette(
        routes=[
            Route(f"/{TOKEN}", telegram_webhook_route, methods=["POST"]),
            Route("/leaderboard", get_leaderboard_route, methods=["GET"]),
            Route("/leaderboard", post_leaderboard_route, methods=["POST"]),
            Route("/healthcheck", health_route, methods=["GET"]),
        ],
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["*"],
            )
        ],
    )

    webserver = uvicorn.Server(
        config=uvicorn.Config(
            app=starlette_app, port=PORT, host="0.0.0.0", use_colors=False
        )
    )

    async with application:
        await application.start()
        await webserver.serve()
        await application.stop()


if __name__ == "__main__":
    asyncio.run(main())
