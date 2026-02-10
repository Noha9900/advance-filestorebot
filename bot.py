import os, asyncio, secrets
from datetime import datetime, timedelta
from flask import Flask
from threading import Thread
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from openai import OpenAI

# --- CONFIG ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
OPENAI_API_KEY = os.getenv("OPEN_AI_KEY")
PORT = int(os.getenv("PORT", "8080"))

client = OpenAI(api_key=OPENAI_API_KEY)

# --- DB (Local storage - will reset on Render restart) ---
db = {
    "welcome": {"photo": None, "text": "Welcome to the Vault! 🔥"},
    "adult": {"photo": None, "text": "Adult Stream Zone", "channels": []},
    "anime": [], "movies": [], "vault": {}, "keys": []
}

# --- STATES ---
(A_W_TEXT, A_W_PHOTO, A_NAME, A_MEDIA, A_DESC, A_LINK, V_KEY) = range(7)

# --- UTILS ---
async def del_msg(context: ContextTypes.DEFAULT_TYPE):
    try: await context.bot.delete_message(chat_id=context.job.chat_id, message_id=context.job.data)
    except: pass

# --- USER INTERFACE ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    w = db["welcome"]
    kb = [
        [InlineKeyboardButton("Adult Stream 🔥", callback_data="u_a")],
        [InlineKeyboardButton("Anime Guide 🎌", callback_data="u_list_anime"), 
         InlineKeyboardButton("Movie Guide 🎬", callback_data="u_list_movies")],
        [InlineKeyboardButton("Secret Vault 🔒", callback_data="u_v")]
    ]
    markup = InlineKeyboardMarkup(kb)
    
    if update.message:
        if w["photo"]:
            msg = await update.message.reply_photo(w["photo"], caption=w["text"], reply_markup=markup)
        else:
            msg = await update.message.reply_text(w["text"], reply_markup=markup)
        context.job_queue.run_once(del_msg, 30, data=msg.message_id, chat_id=update.effective_chat.id)
    else:
        # Handle "Back" buttons or button edits
        try:
            await update.callback_query.edit_message_text(w["text"], reply_markup=markup)
        except:
            # If the original message had a photo, we can't edit text only, so we send new
            await update.callback_query.message.reply_text(w["text"], reply_markup=markup)

# --- ADMIN PANEL ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Access Denied.")
        return
    kb = [
        [InlineKeyboardButton("Set Welcome", callback_data="adm_w"), InlineKeyboardButton("Add Anime", callback_data="adm_ani")],
        [InlineKeyboardButton("Add Movie", callback_data="adm_mov"), InlineKeyboardButton("Gen Key", callback_data="adm_gen")]
    ]
    await update.message.reply_text("🛠 **ADMIN PANEL**", reply_markup=InlineKeyboardMarkup(kb))

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "adm_w":
        await query.edit_message_text("1️⃣ Send the new Welcome Text:")
        return A_W_TEXT
    elif query.data in ["adm_ani", "adm_mov"]:
        context.user_data["type"] = "anime" if "ani" in query.data else "movies"
        await query.edit_message_text(f"Enter Name for {context.user_data['type']}:")
        return A_NAME
    elif query.data == "adm_gen":
        key = "".join([str(secrets.randbelow(10)) for _ in range(12)])
        db["keys"].append(key)
        await query.edit_message_text(f"🗝 **New Key:** `{key}`")
        return ConversationHandler.END

# --- ADMIN SAVE LOGIC (FIXED) ---
async def save_welcome_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_w_text"] = update.message.text
    await update.message.reply_text("2️⃣ Now send the Photo (or send /skip to use text only):")
    return A_W_PHOTO

async def save_welcome_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db["welcome"]["text"] = context.user_data["new_w_text"]
    if update.message.photo:
        db["welcome"]["photo"] = update.message.photo[-1].file_id
        await update.message.reply_text("✅ Welcome Message updated with Photo!")
    else:
        db["welcome"]["photo"] = None
        await update.message.reply_text("✅ Welcome Message updated (Text Only)!")
    return ConversationHandler.END

# --- GUIDE LOGIC ---
async def save_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["temp"] = {"name": update.message.text}
    await update.message.reply_text("Send Media (Photo/Video):")
    return A_MEDIA

async def save_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fid = update.message.photo[-1].file_id if update.message.photo else update.message.video.file_id
    context.user_data["temp"]["file"] = fid
    await update.message.reply_text("Send Description:")
    return A_DESC

async def save_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["temp"]["desc"] = update.message.text
    await update.message.reply_text("Send Link:")
    return A_LINK

async def save_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["temp"]["link"] = update.message.text
    db[context.user_data["type"]].append(context.user_data["temp"])
    await update.message.reply_text("✅ Added to Guide!")
    return ConversationHandler.END

# --- MAIN APP ---
server = Flask(__name__)
@server.route('/')
def h(): return "OK"

def main():
    app = Application.builder().token(TOKEN).build()

    admin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^adm_")],
        states={
            A_W_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_welcome_text)],
            A_W_PHOTO: [MessageHandler(filters.PHOTO, save_welcome_photo), CommandHandler("skip", save_welcome_photo)],
            A_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_name)],
            A_MEDIA: [MessageHandler(filters.PHOTO | filters.VIDEO, save_media)],
            A_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_desc)],
            A_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_all)],
        },
        fallbacks=[CommandHandler("start", start)]
    )

    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(admin_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(start, pattern="main"))

    Thread(target=lambda: server.run(host='0.0.0.0', port=PORT)).start()
    app.run_polling()

if __name__ == "__main__":
    main()
