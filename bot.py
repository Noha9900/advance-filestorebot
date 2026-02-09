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

# --- FLASK ---
app = Flask(__name__)
@app.route('/')
def health(): return "Bot Active", 200
def run_flask(): app.run(host="0.0.0.0", port=int(PORT))

# --- UI ---
def main_menu(is_admin=False):
    if is_admin:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📂 File Store", callback_data="file_store"), InlineKeyboardButton("📦 Batch Store", callback_data="batch_store")],
            [InlineKeyboardButton("🎧 Support Chat", callback_data="admin_support_list")],
            [InlineKeyboardButton("⚙️ Admin Panel ⚙️", callback_data="admin_panel")]
        ])
    return InlineKeyboardMarkup([[InlineKeyboardButton("🎧 Support Chat", callback_data="user_support")]])

# --- FSUB ---
async def check_fsub(user_id):
    fsub = await settings_col.find_one({"type": "fsub"})
    if not fsub or not fsub.get("channel"): return True
    try:
        m = await bot.get_chat_member(fsub["channel"], user_id)
        return m.status not in [enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED]
    except: return False

@bot.on_message(filters.command("start"))
async def start_cmd(c, m):
    uid = m.from_user.id
    if len(m.command) > 1:
        if not await check_fsub(uid): return await m.reply("❌ Join channel first!")
        data = await files_col.find_one({"file_id": m.command[1]})
        if data:
            for msg_id in data["msg_ids"]:
                sent = await c.copy_message(uid, ADMIN_ID, msg_id)
                asyncio.create_task(file_auto_delete(uid, sent.id))
            return

    await users_col.update_one({"id": uid}, {"$set": {"name": m.from_user.first_name}}, upsert=True)
    w = await settings_col.find_one({"type": "welcome"})
    if w:
        try:
            sm = await m.reply_photo(w['photo'], caption=w['text']) if w.get('photo') else await m.reply_text(w['text'])
            asyncio.get_event_loop().call_later(w.get('sec', 10), lambda: bot.delete_messages(m.chat.id, sm.id))
        except: pass
    await m.reply_text("💎 **Main Menu** 💎", reply_markup=main_menu(uid == ADMIN_ID))

@bot.on_callback_query()
async def cb_handler(c, cb: CallbackQuery):
    data, uid = cb.data, cb.from_user.id

    if data == "admin_panel" and uid == ADMIN_ID:
        await cb.message.edit_text("🛠 **Admin Panel**", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👋 Set Welcome", callback_data="set_welcome"), InlineKeyboardButton("📢 Set FSub", callback_data="set_fsub")],
            [InlineKeyboardButton("📊 Stats", callback_data="view_stats"), InlineKeyboardButton("📡 Broadcast", callback_data="broadcast_opt")],
            [InlineKeyboardButton("🔙 Back", callback_data="home_menu")]
        ]))

    elif data == "batch_store" and uid == ADMIN_ID:
        await users_col.update_one({"id": uid}, {"$set": {"action": "batch", "batch_ids": []}})
        await cb.message.reply_text("Send multiple files. Send /done when finished.")

    elif data == "set_welcome" and uid == ADMIN_ID:
        await users_col.update_one({"id": uid}, {"$set": {"action": "wel_text"}})
        await cb.message.reply_text("Send the Welcome Text (or /skip).")

    elif data == "set_fsub" and uid == ADMIN_ID:
        await users_col.update_one({"id": uid}, {"$set": {"action": "fsub_set"}})
        await cb.message.reply_text("Send Channel Username (e.g. @MyChannel).")

    elif data == "admin_support_list" and uid == ADMIN_ID:
        users = await users_col.find({"action": "chatting"}).to_list(10)
        btns = [[InlineKeyboardButton(f"{u['name']} ({u['id']})", callback_data=f"chat_{u['id']}")] for u in users]
        btns.append([InlineKeyboardButton("🔙 Back", callback_data="home_menu")])
        await cb.message.edit_text("Select user to chat:", reply_markup=InlineKeyboardMarkup(btns))

    elif data.startswith("chat_") and uid == ADMIN_ID:
        target = int(data.split("_")[1])
        await users_col.update_one({"id": uid}, {"$set": {"action": f"replying_{target}"}})
        await cb.message.reply_text(f"Replying to {target}. Send msg/pic. /end to stop.")

    elif data == "home_menu":
        await cb.message.edit_text("💎 **Main Menu** 💎", reply_markup=main_menu(uid == ADMIN_ID))

@bot.on_message(filters.private & ~filters.command(["start", "done", "end"]))
async def handle_msgs(c, m):
    uid = m.from_user.id
    u = await users_col.find_one({"id": uid})
    action = u.get("action") if u else None

    if action == "batch" and uid == ADMIN_ID:
        await users_col.update_one({"id": uid}, {"$push": {"batch_ids": m.id}})
        await m.reply("✅ Added to batch.")

    elif action == "wel_text" and uid == ADMIN_ID:
        await settings_col.update_one({"type": "welcome"}, {"$set": {"text": m.text}}, upsert=True)
        await users_col.update_one({"id": uid}, {"$set": {"action": "wel_photo"}})
        await m.reply("Now send a photo (or /skip).")

    elif action == "fsub_set" and uid == ADMIN_ID:
        await settings_col.update_one({"type": "fsub"}, {"$set": {"channel": m.text}}, upsert=True)
        await users_col.update_one({"id": uid}, {"$set": {"action": None}})
        await m.reply(f"✅ FSub set to {m.text}")

    elif action and action.startswith("replying_") and uid == ADMIN_ID:
        target = int(action.split("_")[1])
        await c.copy_message(target, ADMIN_ID, m.id)
        await m.reply("✅ Sent.")

    elif action == "chatting":
        if m.video and m.video.duration > 180: return await m.reply("❌ Limit 3 min.")
        await c.forward_messages(ADMIN_ID, m.chat.id, m.id)
        await m.reply("📨 Sent to Admin.")

@bot.on_message(filters.command("done") & filters.user(ADMIN_ID))
async def batch_done(c, m):
    u = await users_col.find_one({"id": ADMIN_ID})
    if u and u.get("batch_ids"):
        fid = str(uuid.uuid4())[:8]
        await files_col.insert_one({"file_id": fid, "msg_ids": u["batch_ids"]})
        await m.reply(f"📦 Batch stored!\n`https://t.me/{(await c.get_me()).username}?start={fid}`")
    await users_col.update_one({"id": ADMIN_ID}, {"$set": {"action": None, "batch_ids": []}})

async def file_auto_delete(cid, mid):
    await asyncio.sleep(1800)
    try: await bot.delete_messages(cid, mid)
    except: pass

if __name__ == "__main__":
    Thread(target=run_flask).start()
    bot.run()
