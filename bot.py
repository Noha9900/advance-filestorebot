import os
import asyncio
import secrets
from datetime import datetime, timedelta
from flask import Flask
from threading import Thread
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from openai import OpenAI

# --- CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
OPENAI_API_KEY = os.getenv("OPEN_AI_KEY")
PORT = int(os.getenv("PORT", "8080"))

client = OpenAI(api_key=OPENAI_API_KEY)

# --- DATABASE (Persistent in-memory for this session) ---
db = {
    "welcome": {"photo": None, "text": "Welcome to the Vault!"},
    "adult": {"photo": "https://via.placeholder.com/500", "text": "Adult Stream Content", "desc": "Premium Channel", "links": "https://t.me/example"},
    "anime": [{"name": "Naruto", "link": "https://t.me/anime_link"}],
    "movies": [{"name": "Inception", "link": "https://t.me/movie_link"}],
    "vault_keys": [],
}

# --- AI FACILITY ---
async def ai_guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message.text
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": "You are an AI Guide for a Telegram Vault Bot. Help users navigate and explain that only a 12-digit key works for the vault."},
                      {"role": "user", "content": user_msg}]
        )
        await update.message.reply_text(f"🤖 AI: {response.choices[0].message.content}")
    except:
        await update.message.reply_text("🤖 Use the buttons below to navigate the vault!")

# --- UTILS ---
async def auto_delete(context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.delete_message(chat_id=context.job.chat_id, message_id=context.job.data)
    except: pass

# --- UI HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Welcome 🏠", callback_data="w_show"), 
         InlineKeyboardButton("Adult Stream 🔥", callback_data="a_show")],
        [InlineKeyboardButton("Anime Guide 🎌", callback_data="anime_list"),
         InlineKeyboardButton("Movie Guide 🎬", callback_data="movie_list")],
        [InlineKeyboardButton("Secret Vault 🔒", callback_data="v_lock")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = db["welcome"]["text"]
    
    if update.message:
        msg = await update.message.reply_text(text, reply_markup=reply_markup)
        # Auto-delete after 30 seconds
        context.job_queue.run_once(auto_delete, 30, data=msg.message_id, chat_id=update.effective_chat.id)
    else: # If triggered by 'Back' button
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)

async def show_adult(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]
    
    caption = f"🔞 **{db['adult']['text']}**\n\n{db['adult']['desc']}\nLinks: {db['adult']['links']}"
    await query.edit_message_text(caption, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_guides(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data_type = "anime" if "anime" in query.data else "movies"
    
    text = f"📖 **{data_type.upper()} GUIDE**\nChoose a number:\n"
    for i, item in enumerate(db[data_type], 1):
        text += f"{i}. {item['name']}\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# --- VAULT KEY SYSTEM ---
async def v_lock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔐 **VAULT LOCKED**\nEnter the 12-digit key provided by Admin:")
    return 1 # State for key input

async def check_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = update.message.text
    if key in db["vault_keys"]:
        msg = await update.message.reply_text("✅ Access Granted! Viewing files for 35 minutes.")
        # Auto-delete vault access after 35 mins
        context.job_queue.run_once(auto_delete, 2100, data=msg.message_id, chat_id=update.effective_chat.id)
    else:
        await update.message.reply_text("❌ Access Denied. Invalid Key.")
    return ConversationHandler.END

# --- ADMIN COMMANDS ---
async def gen_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    new_key = "".join([str(secrets.randbelow(10)) for _ in range(12)])
    db["vault_keys"].append(new_key)
    await update.message.reply_text(f"🗝️ **New Key Generated:** `{new_key}`")

# --- SERVER ---
app = Flask(__name__)
@app.route('/')
def health(): return "Bot Running"

def main():
    application = Application.builder().token(TOKEN).build()

    # Conversation for Secret Vault Key
    vault_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(v_lock, pattern="v_lock")],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_key)]},
        fallbacks=[CommandHandler("start", start)]
    )

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("genkey", gen_key))
    application.add_handler(vault_conv)
    application.add_handler(CallbackQueryHandler(start, pattern="main_menu"))
    application.add_handler(CallbackQueryHandler(show_adult, pattern="a_show"))
    application.add_handler(CallbackQueryHandler(show_guides, pattern=".*_list"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_guide))

    Thread(target=lambda: app.run(host='0.0.0.0', port=PORT)).start()
    application.run_polling()

if __name__ == "__main__":
    main()
