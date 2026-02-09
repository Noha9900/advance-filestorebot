import os
import asyncio
import pytz
from datetime import datetime, timedelta
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from motor.motor_asyncio import AsyncIOMotorClient
from flask import Flask
from threading import Thread

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", "12345"))
API_HASH = os.environ.get("API_HASH", "your_api_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")
MONGO_URL = os.environ.get("MONGO_URL", "your_mongodb_uri")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "12345678"))
PORT = os.environ.get("PORT", "8080")

# --- INITIALIZATION ---
bot = Client("FileStoreBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
db_client = AsyncIOMotorClient(MONGO_URL)
db = db_client["TelegramBot"]
users_col = db["users"]
settings_col = db["settings"]
files_col = db["files"]

IST = pytz.timezone('Asia/Kolkata')

# --- FLASK SERVER FOR RENDER ---
app = Flask(__name__)
@app.route('/')
def health_check(): return "Bot is Active", 200

def run_flask():
    app.run(host="0.0.0.0", port=int(PORT))

# --- BUTTON FACTORY ---
def main_menu(is_admin=False):
    buttons = [
        [InlineKeyboardButton("📂 File Store", callback_data="file_store"),
         InlineKeyboardButton("📦 Batch Store", callback_data="batch_store")],
        [InlineKeyboardButton("🎧 Support Chat", callback_data="user_support")]
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton("⚙️ Admin Panel ⚙️", callback_data="admin_panel")])
    return InlineKeyboardMarkup(buttons)

# --- CORE LOGIC ---
@bot.on_message(filters.command("start"))
async def start_cmd(client, message):
    user_id = message.from_user.id
    # Save user to DB
    await users_col.update_one({"id": user_id}, {"$set": {"name": message.from_user.first_name, "active": True}}, upsert=True)
    
    # 1. Welcome Message Logic (Check DB for settings)
    welcome = await settings_col.find_one({"type": "welcome"})
    if welcome:
        sent_msg = await message.reply_photo(photo=welcome['photo'], caption=welcome['text']) if welcome.get('photo') else await message.reply_text(welcome['text'])
        # Auto-delete after X seconds
        asyncio.get_event_loop().call_later(welcome.get('seconds', 10), lambda: bot.delete_messages(message.chat.id, sent_msg.id))

    await message.reply_text("💎 **Main Menu** 💎", reply_markup=main_menu(user_id == ADMIN_ID))

@bot.on_callback_query()
async def cb_handler(client, cb: CallbackQuery):
    data = cb.data
    user_id = cb.from_user.id

    if data == "admin_panel" and user_id == ADMIN_ID:
        await cb.message.edit_text(
            "🛠 **Admin Control Panel**",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👋 Welcome Msg", callback_data="set_welcome"), 
                 InlineKeyboardButton("📢 Force Join", callback_data="set_fsub")],
                [InlineKeyboardButton("📡 Broadcast", callback_data="broadcast_opt"),
                 InlineKeyboardButton("📊 Stats", callback_data="view_stats")],
                [InlineKeyboardButton("🎧 Support: ON/OFF", callback_data="toggle_support")],
                [InlineKeyboardButton("🔙 Back", callback_data="home_menu")]
            ])
        )
    
    elif data == "home_menu":
        await cb.message.edit_text("💎 **Main Menu** 💎", reply_markup=main_menu(user_id == ADMIN_ID))

# --- FILE STORE & AUTO-DELETE LOGIC ---
async def file_auto_delete(chat_id, message_ids):
    await asyncio.sleep(1800) # 30 Minutes
    try:
        await bot.delete_messages(chat_id, message_ids)
    except: pass

# --- START BOT ---
if __name__ == "__main__":
    print("Starting Flask server on port", PORT)
    Thread(target=run_flask).start()
    print("Bot is starting...")
    bot.run()
