import os
import hmac
import hashlib
import logging
import tempfile
import threading
from urllib.parse import parse_qsl

import requests
from flask import Flask, request, jsonify, send_from_directory
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

from downloader import download_media, MAX_TELEGRAM_UPLOAD

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set!")

flask_app = Flask(__name__, static_folder="static")


# ---------- Mini App validation ----------
def validate_init_data(init_data: str) -> dict | None:
    """Validates Telegram WebApp initData per Telegram's spec. Returns parsed data or None."""
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None

        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            return None
        return parsed
    except Exception:
        return None


# ---------- Mini App routes ----------
@flask_app.route("/miniapp")
def miniapp():
    return send_from_directory("static", "index.html")


@flask_app.route("/")
def health():
    return "Bot is running"


@flask_app.route("/api/download", methods=["POST"])
def api_download():
    body = request.get_json(force=True, silent=True) or {}
    url = body.get("url", "").strip()
    init_data = body.get("initData", "")

    parsed = validate_init_data(init_data)
    if not parsed:
        return jsonify({"ok": False, "error": "Invalid Telegram session. Please reopen the mini app."}), 401

    import json as _json
    user = _json.loads(parsed.get("user", "{}"))
    chat_id = user.get("id")
    if not chat_id:
        return jsonify({"ok": False, "error": "Could not identify user."}), 400

    if not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"ok": False, "error": "Please provide a valid link."}), 400

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            filepath = download_media(url, tmp_dir)
            if not filepath or not os.path.exists(filepath):
                return jsonify({"ok": False, "error": "Download failed."}), 500

            if os.path.getsize(filepath) > MAX_TELEGRAM_UPLOAD:
                return jsonify({"ok": False, "error": "File is too large for Telegram (over 50MB)."}), 400

            send_file_to_chat(chat_id, filepath)
        return jsonify({"ok": True, "message": "Sent! Check your chat with the bot."})
    except Exception as e:
        logger.exception("Mini app download error")
        return jsonify({"ok": False, "error": str(e)}), 500


def send_file_to_chat(chat_id, filepath):
    ext = filepath.lower()
    if ext.endswith((".mp4", ".mov", ".mkv", ".webm")):
        method, field = "sendVideo", "video"
    elif ext.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
        method, field = "sendPhoto", "photo"
    else:
        method, field = "sendDocument", "document"

    with open(filepath, "rb") as f:
        resp = requests.post(
            f"{TELEGRAM_API}/{method}",
            data={"chat_id": chat_id},
            files={field: f},
            timeout=120,
        )
    resp.raise_for_status()


# ---------- Chat bot handlers (unchanged behavior, now importing shared downloader) ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send me a link (YouTube, Instagram, TikTok, Twitter/X, or any direct file link) "
        "and I'll download and send it back to you.\n\n"
        "You can also tap the menu button below to use the mini app."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if not text.startswith("http://") and not text.startswith("https://"):
        await update.message.reply_text("Please send me a valid link starting with http:// or https://")
        return

    status_msg = await update.message.reply_text("Downloading... this may take a moment.")

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            filepath = download_media(text, tmp_dir)
            if not filepath or not os.path.exists(filepath):
                raise ValueError("Download failed, no file produced.")

            file_size = os.path.getsize(filepath)
            if file_size > MAX_TELEGRAM_UPLOAD:
                await status_msg.edit_text("Sorry, this file is too large for me to send (Telegram's 50MB limit).")
                return

            await status_msg.edit_text("Uploading to Telegram...")
            with open(filepath, "rb") as f:
                ext = filepath.lower()
                if ext.endswith((".mp4", ".mov", ".mkv", ".webm")):
                    await update.message.reply_video(video=f)
                elif ext.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
                    await update.message.reply_photo(photo=f)
                else:
                    await update.message.reply_document(document=f)

            await status_msg.delete()

        except Exception as e:
            logger.exception("Download error")
            await status_msg.edit_text(f"Couldn't download that link.\nReason: {e}")


def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT)


def main():
    # Flask (health check + mini app + API) runs in a background thread
    threading.Thread(target=run_flask, daemon=True).start()

    # Telegram bot polling runs in the main thread
    tg_app = ApplicationBuilder().token(BOT_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot starting...")
    tg_app.run_polling()


if __name__ == "__main__":
    main()
