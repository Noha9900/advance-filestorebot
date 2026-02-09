import os, asyncio, uuid, pytz
from datetime import datetime, timedelta
from pyrogram import Client, filters, types, errors
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from motor.motor_asyncio import AsyncIOMotorClient
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp import web

# --- CONFIG ---
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split(",")]
PORT = int(os.getenv("PORT", 8080))

app = Client("ComplexBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
db = AsyncIOMotorClient(MONGO_URL).FileStoreBot
scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")

admin_states = {} 
batch_temp = {}   

# --- KEYBOARDS ---
def glass_markup(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, cb) for text, cb in row] for row in rows])

def get_admin_main():
    return glass_markup([
        [("👋 Welcome Set", "set_welcome"), ("📢 Force Join", "set_fjoin")],
        [("🎧 Support Status", "toggle_support"), ("📂 Batch Store", "start_batch")],
        [("🚀 Broadcast", "bcast_menu"), ("📊 Stats", "stats")],
        [("🗑 Delete", "delete_msg")]
    ])

# --- DATABASE HELPERS ---
async def get_config():
    config = await db.settings.find_one({"id": "config"})
    if not config:
        config = {"id": "config", "support_active": True, "welcome_enabled": False, "fjoin_channels": [], "welcome_text": "Welcome {name}!", "welcome_sec": 10}
        await db.settings.insert_one(config)
    return config

# --- ADMIN CALLBACK HANDLERS ---
@app.on_callback_query(filters.user(ADMINS))
async def handle_admin_callbacks(c, cb: CallbackQuery):
    data = cb.data
    config = await get_config()

    if data == "main_admin":
        await cb.message.edit_text("🛠 **Admin Control Panel**", reply_markup=get_admin_main())

    elif data == "set_welcome":
        admin_states[cb.from_user.id] = {"state": "waiting_welcome_photo"}
        await cb.message.edit_text("🖼 **Step 1:** Send the Welcome Photo.\nOr click Skip to use text only.",
                                   reply_markup=glass_markup([[("⏩ Skip", "skip_photo")], [("❌ Cancel", "main_admin")]]))

    elif data == "skip_photo":
        admin_states[cb.from_user.id] = {"state": "waiting_welcome_text"}
        await db.settings.update_one({"id": "config"}, {"$set": {"welcome_photo": None}})
        await cb.message.edit_text("📝 **Step 2:** Send the Welcome Text.\nUse `{name}` for the user's name.",
                                   reply_markup=glass_markup([[("❌ Cancel", "main_admin")]]))

    elif data == "stats":
        u_count = await db.users.count_documents({})
        b_count = await db.batches.count_documents({})
        await cb.message.edit_text(f"📊 **Stats**\n\nUsers: `{u_count}`\nBatches: `{b_count}`", 
                                   reply_markup=glass_markup([[("⬅️ Back", "main_admin")]]))

    elif data == "delete_msg":
        await cb.message.delete()

# --- ADMIN INPUT LOGIC ---
@app.on_message(filters.user(ADMINS) & filters.private)
async def admin_input_processor(c, m):
    uid = m.from_user.id
    if uid not in admin_states: return

    state_data = admin_states[uid]
    current_state = state_data.get("state")

    if current_state == "waiting_welcome_photo":
        if m.photo:
            photo_id = m.photo.file_id
            await db.settings.update_one({"id": "config"}, {"$set": {"welcome_photo": photo_id}})
            admin_states[uid] = {"state": "waiting_welcome_text"}
            await m.reply("✅ Photo saved! Now send the **Welcome Text**.")
        else:
            await m.reply("❌ Please send a photo or click Skip.")

    elif current_state == "waiting_welcome_text":
        await db.settings.update_one({"id": "config"}, {"$set": {"welcome_text": m.text, "welcome_enabled": True}})
        admin_states[uid] = {"state": "waiting_welcome_sec"}
        await m.reply("📝 Text saved! Now send the **Auto-delete time** in seconds (e.g., 10).")

    elif current_state == "waiting_welcome_sec":
        if m.text.isdigit():
            await db.settings.update_one({"id": "config"}, {"$set": {"welcome_sec": int(m.text)}})
            await m.reply(f"✅ Welcome settings completed! (Timer: {m.text}s)", reply_markup=get_admin_main())
            del admin_states[uid]
        else:
            await m.reply("❌ Please send a number.")

# --- USER START & WELCOME ---
@app.on_message(filters.command("start") & filters.private)
async def start_handler(c, m):
    await db.users.update_one({"id": m.from_user.id}, {"$set": {"active": True}}, upsert=True)
    config = await get_config()

    # Welcome Logic
    if config.get("welcome_enabled"):
        welcome_text = config.get("welcome_text", "Welcome!").replace("{name}", m.from_user.first_name)
        photo = config.get("welcome_photo")
        
        if photo:
            msg = await m.reply_photo(photo, caption=welcome_text)
        else:
            msg = await m.reply_text(welcome_text)
            
        # Timer for disappearance
        sec = config.get("welcome_sec", 10)
        scheduler.add_job(lambda: msg.delete(), "date", run_date=datetime.now() + timedelta(seconds=sec))

# --- PORT & SERVER ---
async def keep_alive(request): return web.Response(text="Bot Active")
async def main():
    if not scheduler.running: scheduler.start()
    server = web.AppRunner(web.Application())
    await server.setup()
    await web.TCPSite(server, "0.0.0.0", PORT).start()
    await app.start()
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
