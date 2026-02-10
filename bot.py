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

# --- DB ---
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

# --- MAIN UI (DIRECT WELCOME) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This replaces the "Welcome Button" - User gets this content immediately
    w = db["welcome"]
    kb = [
        [InlineKeyboardButton("Adult Stream 🔥", callback_data="u_a")],
        [InlineKeyboardButton("Anime Guide 🎌", callback_data="u_list_anime"), InlineKeyboardButton("Movie Guide 🎬", callback_data="u_list_movies")],
        [InlineKeyboardButton("Secret Vault 🔒", callback_data="u_v")]
    ]
    
    markup = InlineKeyboardMarkup(kb)
    
    if update.message:
        if w["photo"]:
            msg = await update.message.reply_photo(w["photo"], caption=w["text"], reply_markup=markup)
        else:
            msg = await update.message.reply_text(w["text"], reply_markup=markup)
        # Auto-delete after 30 seconds as per requirement
        context.job_queue.run_once(del_msg, 30, data=msg.message_id, chat_id=update.effective_chat.id)
    else:
        # Handle "Back to Main" button clicks
        await update.callback_query.edit_message_text(w["text"], reply_markup=markup)

# --- BUTTON LOGIC ---
async def handle_user_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "u_a":
        kb = [[InlineKeyboardButton("🔙 Back", callback_data="main")]]
        await query.edit_message_text(f"🔞 **{db['adult']['text']}**\nJoin our channels below:", reply_markup=InlineKeyboardMarkup(kb))

    elif "u_list_" in query.data:
        g_type = query.data.split("_")[-1]
        items = db[g_type]
        txt = f"📖 **{g_type.upper()} GUIDE**\nReply with a number to view:\n\n"
        for i, item in enumerate(items, 1): txt += f"{i}. {item['name']}\n"
        if not items: txt += "No entries found yet."
        kb = [[InlineKeyboardButton("🔙 Back", callback_data="main")]]
        await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))

    elif query.data == "u_v":
        await query.edit_message_text("🔐 **VAULT LOCKED**\nEnter your 12-digit secret key:")
        return V_KEY

# --- ADMIN COMMANDS (FIXED) ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Access Denied.")
        return
    
    kb = [
        [InlineKeyboardButton("Set Welcome", callback_data="adm_w"), InlineKeyboardButton("Add Anime", callback_data="adm_ani")],
        [InlineKeyboardButton("Add Movie", callback_data="adm_mov"), InlineKeyboardButton("Gen Key", callback_data="adm_gen")]
    ]
    await update.message.reply_text("🛠 **ADMIN PANEL**\nManage your bot settings below:", reply_markup=InlineKeyboardMarkup(kb))

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "adm_w":
        await query.edit_message_text("Please send the new Welcome Text:")
        return A_W_TEXT
    elif query.data in ["adm_ani", "adm_mov"]:
        context.user_data["type"] = "anime" if "ani" in query.data else "movies"
        await query.edit_message_text(f"Enter the name of the {context.user_data['type']}:")
        return A_NAME
    elif query.data == "adm_gen":
        key = "".join([str(secrets.randbelow(10)) for _ in range(12)])
        db["keys"].append(key)
        await query.edit_message_text(f"🗝 **Generated Key:** `{key}`\nProvide this to users.")
        return ConversationHandler.END

# --- ADMIN CONVERSATION STEPS ---
async def save_anime_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["temp"] = {"name": update.message.text}
    await update.message.reply_text("Now send the Photo or Video file:")
    return A_MEDIA

async def save_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_id = update.message.photo[-1].file_id if update.message.photo else update.message.video.file_id
    context.user_data["temp"]["file"] = file_id
    await update.message.reply_text("Now send the Description:")
    return A_DESC

async def save_desc_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["temp"]["desc"] = update.message.text
    await update.message.reply_text("Finally, send the Channel/Video Link:")
    return A_LINK

async def save_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["temp"]["link"] = update.message.text
    db[context.user_data["type"]].append(context.user_data["temp"])
    await update.message.reply_text("✅ Success! Item added to guide.")
    return ConversationHandler.END

# --- VAULT KEY CHECK ---
async def check_v_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text in db["keys"]:
        m = await update.message.reply_text("🔓 **UNLOCKED**\nFiles are available for 30 minutes.")
        context.job_queue.run_once(del_msg, 1800, data=m.message_id, chat_id=update.effective_chat.id)
    else:
        await update.message.reply_text("❌ Key incorrect.")
    return ConversationHandler.END

# --- RENDER SERVER ---
server = Flask(__name__)
@server.route('/')
def health(): return "Bot Online"

def main():
    app = Application.builder().token(TOKEN).build()

    # Admin Conversation (Simplified)
    admin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^adm_")],
        states={
            A_W_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: A_W_PHOTO)],
            A_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_anime_name)],
            A_MEDIA: [MessageHandler((filters.PHOTO | filters.VIDEO), save_media)],
            A_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_desc_final)],
            A_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_all)],
        },
        fallbacks=[CommandHandler("start", start)]
    )

    # User Key Conversation
    user_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_user_buttons, pattern="u_v")],
        states={V_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_v_key)]},
        fallbacks=[CommandHandler("start", start)]
    )

    app.add_handler(CommandHandler("admin", admin_panel)) # Outside conv for reliability
    app.add_handler(admin_conv)
    app.add_handler(user_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_user_buttons, pattern="^u_"))
    app.add_handler(CallbackQueryHandler(start, pattern="main"))

    Thread(target=lambda: server.run(host='0.0.0.0', port=PORT)).start()
    app.run_polling()

if __name__ == "__main__":
    main()
