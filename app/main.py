from __future__ import annotations

import asyncio
import html
import logging
import os
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import BOT_TOKEN, DOWNLOAD_DIR
from .database import (
    active_jobs_for_user,
    cancel_job,
    create_job,
    get_history,
    get_user,
    init_db,
    is_banned,
    rate_ok,
    stats,
    upsert_user,
    update_job,
)
from .downloader import info
from .pubsub import publish_job

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("yt2mf.main")

QUALITYS = [("360p", "360"), ("480p", "480"), ("720p", "720"), ("1080p", "1080"), ("Best", "best"), ("Audio", "audio")]
DESTINATIONS = [("Telegram", "telegram"), ("MediaFire", "mediafire")]
RATE_LIMIT_PER_HOUR = int(os.getenv("RATE_LIMIT_PER_HOUR", "20"))
MAX_QUEUE_PER_USER = int(os.getenv("MAX_QUEUE_PER_USER", "3"))


def quality_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"quality:{value}") for label, value in QUALITYS[:3]],
        [InlineKeyboardButton(label, callback_data=f"quality:{value}") for label, value in QUALITYS[3:]],
    ])


def destination_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"dest:{value}") for label, value in DESTINATIONS]])


def cancel_menu(job_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data=f"cancel:{job_id}")]])


def valid_url(text: str) -> bool:
    return text.startswith(("https://", "http://"))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("سلام 👋\nلینک YouTube را ارسال کنید.")


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user = update.effective_user
    if not user:
        return
    upsert_user(user.id, user.username or "", user.first_name or "")
    if is_banned(user.id):
        await update.message.reply_text("❌ دسترسی شما مسدود است.")
        return
    if not rate_ok(user.id, RATE_LIMIT_PER_HOUR):
        await update.message.reply_text("⛔ محدودیت درخواست ساعتی شما پر شده است.")
        return
    if active_jobs_for_user(user.id) >= MAX_QUEUE_PER_USER:
        await update.message.reply_text("⏳ چند Job فعال دارید. لطفاً بعد از اتمام یکی از آن‌ها دوباره تلاش کنید.")
        return
    url = update.message.text.strip()
    if not valid_url(url):
        await update.message.reply_text("❌ لطفاً URL معتبر ارسال کنید.")
        return
    await update.message.reply_text("🔎 در حال دریافت اطلاعات ویدیو...")
    try:
        data = await info(url)
    except Exception as exc:
        await update.message.reply_text(f"❌ دریافت اطلاعات ناموفق بود:\n{str(exc)[-2500:]}")
        return
    title = data.get("title") or "Unknown"
    duration = int(data.get("duration") or 0)
    minutes, seconds = divmod(duration, 60)
    context.user_data["url"] = url
    context.user_data["title"] = title
    await update.message.reply_text(
        f"🎬 <b>{html.escape(title)}</b>\n⏱ {minutes}:{seconds:02d}\n\nکیفیت را انتخاب کنید:",
        parse_mode=ParseMode.HTML,
        reply_markup=quality_menu(),
    )


async def handle_video_for_compression(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.video:
        return
    user = update.effective_user
    if not user:
        return
    upsert_user(user.id, user.username or "", user.first_name or "")
    if is_banned(user.id):
        return
    video = update.message.video
    job_id = create_job(user.id, update.message.chat_id, f"telegram:{video.file_id}", update.message.caption or "Telegram video")
    update_job(job_id, format="telegram_compress", destination="telegram")
    publish_job(job_id)
    await update.message.reply_text(f"⏳ Job #{job_id} در صف فشرده‌سازی قرار گرفت.", reply_markup=cancel_menu(job_id))


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    if data.startswith("quality:"):
        url = context.user_data.get("url")
        if not url:
            await query.edit_message_text("❌ اطلاعات Job منقضی شده است.")
            return
        quality = data.split(":", 1)[1]
        context.user_data["format"] = quality
        await query.edit_message_text("📤 فایل را کجا ارسال کنم؟", reply_markup=destination_menu())
        return

    if data.startswith("dest:"):
        url = context.user_data.get("url")
        if not url:
            await query.edit_message_text("❌ اطلاعات Job منقضی شده است.")
            return
        destination = data.split(":", 1)[1]
        title = context.user_data.get("title", "Unknown")
        quality = context.user_data.get("format", "best")
        job_id = create_job(query.from_user.id, query.message.chat_id, url, title)
        update_job(job_id, format=quality, destination=destination, title=title)
        try:
            message_id = publish_job(job_id)
        except Exception as exc:
            cancel_job(job_id)
            await query.edit_message_text(f"❌ ارسال Job به صف ناموفق بود: {str(exc)[-1500:]}")
            return
        await query.edit_message_text(
            f"⏳ <b>Job #{job_id}</b>\n\n🎬 {html.escape(title)}\n🎞 کیفیت: {quality}\n📤 مقصد: {destination}\n\nدر صف پردازش قرار گرفت.",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_menu(job_id),
        )
        logger.info("Published job=%s message=%s", job_id, message_id)
        context.user_data.clear()
        return

    if data.startswith("cancel:"):
        try:
            job_id = int(data.split(":", 1)[1])
        except ValueError:
            await query.edit_message_text("❌ شناسه Job نامعتبر است.")
            return
        changed = cancel_job(job_id)
        await query.edit_message_text(f"{'❌ Job لغو شد.' if changed else 'ℹ️ Job قبلاً پردازش شده یا لغو شده است.'}")


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    rows = get_history(update.effective_user.id, 10)
    if not rows:
        await update.message.reply_text("تاریخچه‌ای وجود ندارد.")
        return
    lines = ["📚 <b>تاریخچه</b>"]
    for row in rows:
        lines.append(f"#{row['id']} — {html.escape(row['title'] or 'Unknown')} — {row['size_mb'] or 0:.1f} MB")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    s = stats()
    await update.message.reply_text(f"👥 Users: {s['users']}\n📦 Jobs: {s['jobs']}\n⏳ Active: {s['active']}\n📚 History: {s['history']}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Telegram handler error", exc_info=context.error)


def build_application():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("history", history_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(CallbackQueryHandler(callbacks))
    application.add_handler(MessageHandler(filters.VIDEO, handle_video_for_compression))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    application.add_error_handler(error_handler)
    return application


async def main():
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(init_db)
    application = build_application()
    logger.info("yt2mf main Telegram server started")
    await application.initialize()
    await application.start()
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    try:
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
