import os
import asyncio
import pytz
import uuid
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

bot = Client("FileStoreBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
db_client = AsyncIOMotorClient(MONGO_URL)
db = db_client["TelegramBot"]
users_col = db["users"]
settings_col = db["settings"]
files_col = db["files"]

IST = pytz.timezone('Asia/Kolkata')

app = Flask(__name__)
@app.route('/')
def health_check(): return "Bot is Active", 200

def run_flask():
    app.run(host="0.0.0.0", port=int(PORT))

def main_menu(is_admin=False):
    if is_admin:
        buttons = [
            [InlineKeyboardButton("📂 File Store", callback_data="file_store"),
             InlineKeyboardButton("📦 Batch Store", callback_data="batch_store")],
            [InlineKeyboardButton("🎧 Support Chat", callback_data="user_support")],
            [InlineKeyboardButton("⚙️ Admin Panel ⚙️", callback_data="admin_panel")]
        ]
    else:
        buttons = [[InlineKeyboardButton("🎧 Support Chat", callback_data="user_support")]]
    return InlineKeyboardMarkup(buttons)

# --- FSUB CHECK ---
async def check_fsub(user_id):
    fsub = await settings_col.find_one({"type": "fsub"})
    if not fsub or not fsub.get("channel"): return True
    try:
        member = await bot.get_chat_member(fsub["channel"], user_id)
        return member.status not in [enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED]
    except: return False

@bot.on_message(filters.command("start"))
async def start_cmd(client, message):
    user_id = message.from_user.id
    # Link Handling
    if len(message.command) > 1:
        if not await check_fsub(user_id):
            return await message.reply("❌ Join channel first!")
        file_data = await files_col.find_one({"file_id": message.command[1]})
        if file_data:
            msg = await bot.copy_message(user_id, ADMIN_ID, file_data["msg_id"])
            asyncio.create_task(file_auto_delete(user_id, msg.id))
            return

    await users_col.update_one({"id": user_id}, {"$set": {"name": message.from_user.first_name}}, upsert=True)
    welcome = await settings_col.find_one({"type": "welcome"})
    if welcome:
        sent_msg = await message.reply_photo(photo=welcome['photo'], caption=welcome['text']) if welcome.get('photo') else await message.reply_text(welcome['text'])
        asyncio.get_event_loop().call_later(welcome.get('seconds', 10), lambda: bot.delete_messages(message.chat.id, sent_msg.id))
    
    await message.reply_text("💎 **Main Menu** 💎", reply_markup=main_menu(user_id == ADMIN_ID))

@bot.on_callback_query()
async def cb_handler(client, cb: CallbackQuery):
    data = cb.data
    user_id = cb.from_user.id

    if data == "admin_panel" and user_id == ADMIN_ID:
        await cb.message.edit_text("🛠 **Admin Control Panel**", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👋 Welcome Msg", callback_data="set_welcome"), InlineKeyboardButton("📢 Force Join", callback_data="set_fsub")],
            [InlineKeyboardButton("📡 Broadcast", callback_data="broadcast_opt"), InlineKeyboardButton("📊 Stats", callback_data="view_stats")],
            [InlineKeyboardButton("🎧 Support: ON/OFF", callback_data="toggle_support")],
            [InlineKeyboardButton("🔙 Back", callback_data="home_menu")]
        ]))
    
    elif data == "file_store" and user_id == ADMIN_ID:
        await cb.message.reply_text("Send the file to store permanently.")
        await users_col.update_one({"id": user_id}, {"$set": {"action": "storing"}})

    elif data == "user_support":
        await cb.message.reply_text("Support Active. Send message (No videos > 3min).", 
                                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ End Chat", callback_data="end_chat")]]))
        await users_col.update_one({"id": user_id}, {"$set": {"action": "chatting"}})

    elif data == "end_chat":
        await users_col.update_one({"id": user_id}, {"$set": {"action": None}})
        await cb.message.edit_text("Chat Ended.")

    elif data == "home_menu":
        await cb.message.edit_text("💎 **Main Menu** 💎", reply_markup=main_menu(user_id == ADMIN_ID))

# --- MESSAGE HANDLING (SUPPORT & STORAGE) ---
@bot.on_message(filters.private & ~filters.command("start"))
async def handle_messages(client, message):
    user = await users_col.find_one({"id": message.from_user.id})
    
    # Storage Logic
    if user and user.get("action") == "storing" and message.from_user.id == ADMIN_ID:
        file_uuid = str(uuid.uuid4())[:8]
        await files_col.insert_one({"file_id": file_uuid, "msg_id": message.id})
        await message.reply_text(f"✅ Stored! Shareable Link:\n`https://t.me/{(await bot.get_me()).username}?start={file_uuid}`")
        await users_col.update_one({"id": ADMIN_ID}, {"$set": {"action": None}})

    # Support Logic
    elif user and user.get("action") == "chatting":
        if message.from_user.id == ADMIN_ID:
            # Admin replying to user (Assuming you reply to forwarded message)
            if message.reply_to_message and message.reply_to_message.forward_from:
                target_id = message.reply_to_message.forward_from.id
                await bot.copy_message(target_id, ADMIN_ID, message.id)
        else:
            if message.video and message.video.duration > 180:
                return await message.reply("❌ Videos over 3 mins not allowed.")
            await bot.forward_messages(ADMIN_ID, message.chat.id, message.id)
            await message.reply("📨 Sent to Admin.")

async def file_auto_delete(chat_id, message_id):
    await asyncio.sleep(1800)
    try: await bot.delete_messages(chat_id, message_id)
    except: pass

if __name__ == "__main__":
    Thread(target=run_flask).start()
    bot.run()
