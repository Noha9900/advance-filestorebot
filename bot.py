import os
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timedelta
import pytz

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", "12345"))
API_HASH = os.environ.get("API_HASH", "your_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_token")
MONGO_URL = os.environ.get("MONGO_URL", "your_mongodb_url")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "your_user_id"))

client = Client("FileStoreBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
db_client = AsyncIOMotorClient(MONGO_URL)
db = db_client.FileStoreBot
users_db = db.users
settings_db = db.settings

IST = pytz.timezone('Asia/Kolkata')

# --- HELPER FUNCTIONS ---
async def is_subscribed(user_id):
    settings = await settings_db.find_one({"id": "force_sub"})
    if not settings or not settings.get("channels"):
        return True
    for channel in settings["channels"]:
        try:
            member = await client.get_chat_member(channel, user_id)
            if member.status == enums.ChatMemberStatus.LEFT:
                return False
        except Exception:
            return False
    return True

# --- BUTTON FACTORY ---
def get_main_buttons(is_admin=False):
    buttons = [
        [InlineKeyboardButton("📁 Store File 📁", callback_data="store_file"),
         InlineKeyboardButton("🚀 Batch 🚀", callback_data="batch_store")],
        [InlineKeyboardButton("🎧 Support 🎧", callback_data="support_chat")]
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton("⚙️ Admin Panel ⚙️", callback_data="admin_main")])
    return InlineKeyboardMarkup(buttons)

# --- HANDLERS ---
@client.on_message(filters.command("start"))
async def start_handler(bot, message):
    user_id = message.from_user.id
    await users_db.update_one({"id": user_id}, {"$set": {"last_seen": datetime.now()}}, upsert=True)
    
    if not await is_subscribed(user_id):
        # Implementation of Force Sub logic
        return await message.reply_photo(
            photo="https://telegra.ph/file/your_image.jpg",
            caption="⚠️ **Access Denied!**\n\nPlease join our channels to use this bot.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Join Channel", url="https://t.me/yourchannel")]])
        )

    await message.reply_text(
        f"👋 **Welcome {message.from_user.mention}!**\nI am a Permanent File Store Bot.",
        reply_markup=get_main_buttons(user_id == ADMIN_ID)
    )

@client.on_callback_query()
async def cb_handler(bot, cb: CallbackQuery):
    if cb.data == "admin_main" and cb.from_user.id == ADMIN_ID:
        await cb.message.edit_text(
            "🛠 **Admin Control Center**",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👋 Welcome Set", callback_data="set_welcome"),
                 InlineKeyboardButton("📢 Force Join", callback_data="set_force")],
                [InlineKeyboardButton("📊 Statistics", callback_data="stats"),
                 InlineKeyboardButton("📡 Broadcast", callback_data="broadcast")],
                [InlineKeyboardButton("🔙 Back", callback_data="home")]
            ])
        )
    elif cb.data == "home":
        await cb.message.edit_text("Main Menu", reply_markup=get_main_buttons(cb.from_user.id == ADMIN_ID))

# --- KEEP ALIVE FOR RENDER ---
from flask import Flask
from threading import Thread

app = Flask(__name__)
@app.route('/')
def index(): return "Bot is Running"

def run(): app.run(host="0.0.0.0", port=8080)

if __name__ == "__main__":
    Thread(target=run).start()
    client.run()
