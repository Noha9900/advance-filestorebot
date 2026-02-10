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

# --- DATABASE (Mock - Use MongoDB for Production) ---
db = {
    "welcome": {"photo": None, "text": "Welcome to the Vault!"},
    "adult": {"photo": None, "text": "Adult Stream", "desc": "", "links": []},
    "anime": [],
    "movies": [],
    "vault_keys": [],
    "vault_content": {}
}

# --- STATES ---
(MAIN_ADMIN, SET_W_TEXT, SET_W_PHOTO, 
 SET_ADULT_TEXT, SET_ANIME_NAME, SET_ANIME_DATA, 
 VAULT_KEY_CHECK) = range(7)

# --- AI FACILITY ---
async def ai_guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message.text
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": "You are an AI Guide for a Telegram Vault Bot. Help users navigate buttons and explain the 12-digit key system politely."},
                      {"role": "user", "content": user_msg}]
        )
        await update.message.reply_text(f"🤖 AI Guide: {response.choices[0].message.content}")
    except Exception:
        await update.message.reply_text("🤖 I'm here to help! Use the menu buttons to navigate.")

# --- UTILS ---
async def auto_delete(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    await context.bot.delete_message(chat_id=job.chat_id, message_id=job.data)

# --- CORE LOGIC ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = db["welcome"]["photo"]
    text = db["welcome"]["text"]
    
    keyboard = [
        [InlineKeyboardButton("Welcome 🏠", callback_data="w_show"), 
         InlineKeyboardButton("Adult Stream 🔥", callback_data="a_show")],
        [InlineKeyboardButton("Anime Guide 🎌", callback_data="anime_list"),
         InlineKeyboardButton("Movie Guide 🎬", callback_data="movie_list")],
        [InlineKeyboardButton("Secret Vault 🔒", callback_data="v_lock")]
    ]
    
    if photo:
        msg = await update.message.reply_photo(photo=photo, caption=text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        msg = await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # Feature: Auto-disappear after 30 seconds
    context.job_queue.run_once(auto_delete, 30, data=msg.message_id, chat_id=update.effective_chat.id)

# --- VAULT KEY SYSTEM ---
async def vault_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔐 **VAULT LOCKED**\nPlease enter your 12-digit access key:")
    return VAULT_KEY_CHECK

async def check_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = update.message.text
    if key in db["vault_keys"]:
        await update.message.reply_text("✅ Access Granted. Files visible for 35 minutes.")
        # Logic to show files would go here
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Invalid Key. Ask Admin for access.")
        return ConversationHandler.END

# --- ADMIN PANEL ---
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    kb = [[InlineKeyboardButton("Set Welcome", callback_data="conf_w")]]
    await update.message.reply_text("Admin Panel:", reply_markup=InlineKeyboardMarkup(kb))
    return MAIN_ADMIN

# --- WEB SERVER FOR RENDER ---
app = Flask(__name__)
@app.route('/')
def health(): return "Bot Active"

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

# --- MAIN ---
def main():
    application = Application.builder().token(TOKEN).build()
    
    # Conversation for Admin & Vault
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("admin", admin_cmd), CallbackQueryHandler(vault_trigger, pattern="v_lock")],
        states={
            VAULT_KEY_CHECK: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_key)],
            MAIN_ADMIN: [CallbackQueryHandler(lambda u, c: SET_W_TEXT, pattern="conf_w")],
            # Add other setup states here...
        },
        fallbacks=[CommandHandler("start", start)]
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_guide))

    # Start Flask in thread
    Thread(target=run_flask).start()
    
    print("Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()
