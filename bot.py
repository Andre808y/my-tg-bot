import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, Update
from telegram.ext import Application, CommandHandler, ContextTypes

# --- настройки ---
TOKEN = os.environ["BOT_TOKEN"]                 # задаётся в Render, в коде токен не хранится
GAME_URL = "https://andre808y.github.io/Iphone-jumper/"   # ссылка на вашу игру
PORT = int(os.environ.get("PORT", 10000))        # Render сам подставляет порт
HOSTNAME = os.environ["RENDER_EXTERNAL_HOSTNAME"]  # Render сам подставляет домен сервиса


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("🎮 Играть", web_app=WebAppInfo(url=GAME_URL))]
    ]
    await update.message.reply_text(
        "Прыгай через коробки с айфонами! 📦📱",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


def main() -> None:
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("game", start))
    # сюда же добавляйте остальные ваши обработчики (add_handler),
    # если они уже были в старом файле бота

    # webhook вместо polling — так Render может "будить" бота по входящему сообщению,
    # даже если сервис перед этим уснул из-за неактивности
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"https://{HOSTNAME}/{TOKEN}",
    )


if __name__ == "__main__":
    main()
