import os
import asyncio
import pytz
import uuid
from datetime import datetime
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from motor.motor_asyncio import AsyncIOMotorClient
from flask import Flask
from threading import Thread

# --- CONFIG ---
API_ID = int(os.environ.get("API_ID", "12345"))
API_HASH = os.environ.get("API_HASH", "your_api_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")
MONGO_URL = os.environ.get("MONGO_URL", "your_mongodb_uri")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "12345678"))
PORT = os.environ.get("PORT", "8080")

bot = Client("FileStoreBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
db_client = AsyncIOMotorClient(MONGO_URL)
db = db_client["TelegramBot"]
users_col, settings_col, files_col = db["users"], db["settings"], db["files"]

IST = pytz.timezone('Asia/Kolkata')

app = Flask(__name__)
@app.route('/')
def health(): return "Bot Active", 200
def run_flask(): app.run(host="0.0.0.0", port=int(PORT))

# --- BUTTON FACTORY ---
def main_menu(is_admin=False):
    if is_admin:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📂 File Store", callback_data="file_store"), InlineKeyboardButton("📦 Batch Store", callback_data="batch_store")],
            [InlineKeyboardButton("🎧 Support Chat", callback_data="admin_support_list")],
            [InlineKeyboardButton("⚙️ Admin Panel ⚙️", callback_data="admin_panel")]
        ])
    return InlineKeyboardMarkup([[InlineKeyboardButton("🎧 Support Chat", callback_data="user_support")]])

# --- FSUB CHECK ---
async def check_fsub(user_id):
    fsub = await settings_col.find_one({"type": "fsub"})
    if not fsub: return True
    for ch in fsub.get("channels", []):
        try:
            m = await bot.get_chat_member(ch['link'], user_id)
            if m.status in [enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED]: return False
        except: return False
    return True

# --- START COMMAND ---
@bot.on_message(filters.command("start"))
async def start_cmd(c, m):
    uid = m.from_user.id
    if len(m.command) > 1:
        if not await check_fsub(uid):
            fsub = await settings_col.find_one({"type": "fsub"})
            return await m.reply_photo(fsub['photo'], caption=fsub['text'], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(ch['name'], url=ch['link'])] for ch in fsub['channels']]))
        data = await files_col.find_one({"file_id": m.command[1]})
        if data:
            for msg_id in data["msg_ids"]:
                sent = await c.copy_message(uid, ADMIN_ID, msg_id)
                asyncio.create_task(file_auto_delete(uid, sent.id))
            return

    await users_col.update_one({"id": uid}, {"$set": {"name": m.from_user.first_name, "last_active": datetime.now()}}, upsert=True)
    w = await settings_col.find_one({"type": "welcome"})
    if w:
        try:
            sm = await m.reply_photo(w['photo'], caption=w['text']) if w.get('photo') else await m.reply_text(w['text'])
            asyncio.get_event_loop().call_later(w.get('sec', 10), lambda: bot.delete_messages(m.chat.id, sm.id))
        except: pass
    await m.reply_text("💎 **Main Menu** 💎", reply_markup=main_menu(uid == ADMIN_ID))

# --- CALLBACK HANDLERS ---
@bot.on_callback_query()
async def cb_handler(c, cb: CallbackQuery):
    data, uid = cb.data, cb.from_user.id

    if data == "admin_panel" and uid == ADMIN_ID:
        await cb.message.edit_text("🛠 **Admin Panel**", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👋 Set Welcome", callback_data="set_welcome"), InlineKeyboardButton("📢 Set FSub", callback_data="set_fsub")],
            [InlineKeyboardButton("📊 Stats", callback_data="view_stats"), InlineKeyboardButton("📡 Broadcast", callback_data="broadcast_opt")],
            [InlineKeyboardButton("🔙 Back", callback_data="home_menu")]
        ]))

    elif data == "set_welcome" and uid == ADMIN_ID:
        await users_col.update_one({"id": uid}, {"$set": {"action": "set_wel_text"}})
        await cb.message.reply_text("Step 1: Send the Welcome Text.")

    elif data == "view_stats" and uid == ADMIN_ID:
        total = await users_col.count_documents({})
        active = await users_col.count_documents({"last_active": {"$gte": datetime.now() - timedelta(days=7)}})
        await cb.answer(f"📊 Total: {total} | Active: {active}", show_alert=True)

    elif data == "admin_support_list" and uid == ADMIN_ID:
        users = await users_col.find({"is_support": True}).to_list(10)
        btns = [[InlineKeyboardButton(f"{u['name']} ({u['id']})", callback_data=f"chat_{u['id']}")] for u in users]
        btns.append([InlineKeyboardButton("🔙 Back", callback_data="home_menu")])
        await cb.message.edit_text("Select user to connect:", reply_markup=InlineKeyboardMarkup(btns))

    elif data.startswith("chat_") and uid == ADMIN_ID:
        target = int(data.split("_")[1])
        await users_col.update_one({"id": uid}, {"$set": {"action": f"replying_{target}"}})
        await cb.message.reply_text(f"Connected to {target}. Type /end to close.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ End Chat", callback_data="end_chat")]]))

    elif data == "home_menu":
        await cb.message.edit_text("💎 **Main Menu** 💎", reply_markup=main_menu(uid == ADMIN_ID))

# --- MESSAGE LOGIC ---
@bot.on_message(filters.private & ~filters.command(["start", "done", "end"]))
async def handle_msgs(c, m):
    uid = m.from_user.id
    u = await users_col.find_one({"id": uid})
    action = u.get("action") if u else None

    if action == "set_wel_text" and uid == ADMIN_ID:
        await settings_col.update_one({"type": "welcome"}, {"$set": {"text": m.text}}, upsert=True)
        await users_col.update_one({"id": uid}, {"$set": {"action": "set_wel_photo"}})
        await m.reply("Step 2: Send any photo for Welcome.")

    elif action == "set_wel_photo" and uid == ADMIN_ID:
        if m.photo:
            await settings_col.update_one({"type": "welcome"}, {"$set": {"photo": m.photo.file_id}}, upsert=True)
            await users_col.update_one({"id": uid}, {"$set": {"action": None}})
            await m.reply("✅ Welcome Message Fixed!")

    elif action and action.startswith("replying_") and uid == ADMIN_ID:
        target = int(action.split("_")[1])
        await c.copy_message(target, ADMIN_ID, m.id)

async def file_auto_delete(cid, mid):
    await asyncio.sleep(1800)
    try: await bot.delete_messages(cid, mid)
    except: pass

if __name__ == "__main__":
    Thread(target=run_flask).start()
    bot.run()
