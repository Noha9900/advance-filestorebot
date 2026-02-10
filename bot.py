import os
import asyncio
import secrets
import time
from datetime import datetime, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from openai import OpenAI

# Configuration
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
OPENAI_API_KEY = os.getenv("OPEN_AI_KEY")
PORT = int(os.getenv("PORT", "8080"))

client = OpenAI(api_key=OPENAI_API_KEY)

# Mock Database (In production, use Motor/MongoDB)
db = {
    "welcome": {"photo": None, "text": "Welcome!"},
    "adult": {"photo": None, "text": "", "desc": "", "links": []},
    "anime": [], # List of dicts
    "movies": [],
    "vault": {} # key: {poster, caption, files: []}
}

# --- AI Guidance Helper ---
async def get_ai_guidance(user_input):
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": "You are a helpful vault bot assistant. Guide the user on how to use buttons or find content."},
                      {"role": "user", "content": user_input}]
        )
        return response.choices[0].message.content
    except:
        return "I'm here to help! Use the menu buttons to navigate the vault."

# --- Helper: Auto-delete ---
async def delete_after(message, seconds):
    await asyncio.sleep(seconds)
    try:
        await message.delete()
    except:
        pass

# --- Main Menu ---
def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("Welcome 🏠", callback_data="start_node"),
         InlineKeyboardButton("Adult Stream 🔥", callback_data="adult_node")],
        [InlineKeyboardButton("Anime Guide 🎌", callback_data="anime_list"),
         InlineKeyboardButton("Movie Guide 🎬", callback_data="movie_list")],
        [InlineKeyboardButton("Secret Vault 🔒", callback_data="vault_node")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Welcome feature with 30s disappear
    msg_text = db["welcome"]["text"]
    photo = db["welcome"]["photo"]
    
    if photo:
        msg = await update.message.reply_photo(photo=photo, caption=msg_text, reply_markup=main_menu_keyboard())
    else:
        msg = await update.message.reply_text(msg_text, reply_markup=main_menu_keyboard())
    
    context.job_queue.run_once(lambda c: delete_after(msg, 30), 30)

# --- Admin Functionality (Simplified Example) ---
async def admin_set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    # Logic to save photo and text to db["welcome"]
    await update.message.reply_text("Welcome message updated!")

# --- Secret Vault Logic ---
async def vault_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Generate Key logic
    if not context.user_data.get("vault_authorized"):
        await query.edit_message_text("Enter the 12-digit Secret Key to access the vault:")
        return # Transition to state awaiting key

async def handle_vault_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_key = update.message.text
    # In reality, check against db["vault_keys"]
    if len(user_key) == 12:
        context.user_data["vault_authorized"] = True
        msg = await update.message.reply_text("Access Granted. This session expires in 35 minutes.")
        # Auto delete session
        context.job_queue.run_once(lambda c: context.user_data.update({"vault_authorized": False}), 2100)
    else:
        await update.message.reply_text("Invalid Key.")

# --- Application Setup ---
def main():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(vault_access, pattern="^vault_node$"))
    # Add more handlers for Admin Setup (Anime, Movies, etc.)

    # Start the server (Required for Render)
    import threading
    from flask import Flask
    server = Flask(__name__)
    @server.route('/')
    def health(): return "Bot is running"
    
    threading.Thread(target=lambda: server.run(host='0.0.0.0', port=PORT)).start()

    application.run_polling()

if __name__ == '__main__':
    main()
