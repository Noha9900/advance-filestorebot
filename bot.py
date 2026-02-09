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
        config = {"id": "config", "support_active": True, "welcome_enabled": True, "fjoin_channels": [], "welcome_text": "Welcome {name}!", "welcome_sec": 10}
        await db.settings.insert_one(config)
    return config

# --- ADMIN COMMAND HANDLER ---
@app.on_message(filters.command("admin") & filters.user(ADMINS))
async def admin_cmd_handler(c, m):
    await m.reply_text("🛠 **Admin Control Panel**", reply_markup=get_admin_main())

# --- CALLBACK QUERY HANDLER (Buttons Logic) ---
@app.on_callback_query()
async def cb_handler(c, cb: CallbackQuery):
    if cb.from_user.id not in ADMINS:
        return await cb.answer("❌ Access Denied", show_alert=True)
    
    data = cb.data
    if data == "main_admin":
        await cb.message.edit_text("🛠 **Admin Control Panel**", reply_markup=get_admin_main())
    elif data == "delete_msg":
        await cb.message.delete()
    elif data == "stats":
        u_count = await db.users.count_documents({})
        b_count = await db.batches.count_documents({})
        await cb.message.edit_text(f"📊 **Stats**\n\nUsers: `{u_count}`\nBatches: `{b_count}`", reply_markup=glass_markup([[("⬅️ Back", "main_admin")]]))
    # Add other data handlers (set_welcome, toggle_support, etc.) here

# --- USER HANDLER (WELCOME & BATCH) ---
@app.on_message(filters.command("start") & filters.private)
async def start_handler(c, m):
    await db.users.update_one({"id": m.from_user.id}, {"$set": {"name": m.from_user.first_name}}, upsert=True)
    config = await get_config()

    if config.get("welcome_enabled"):
        welcome_text = config.get("welcome_text", "Welcome {name}!").replace("{name}", m.from_user.first_name)
        w_photo = config.get("welcome_photo")
        msg = await (m.reply_photo(w_photo, caption=welcome_text) if w_photo else m.reply_text(welcome_text))
        
        async def auto_del(message, delay):
            await asyncio.sleep(delay)
            try: await message.delete()
            except: pass
        asyncio.create_task(auto_del(msg, config.get("welcome_sec", 10)))

    if len(m.text.split()) > 1:
        payload = m.text.split()[1]
        if payload.startswith("batch_"):
            batch_id = payload.replace("batch_", "")
            batch_data = await db.batches.find_one({"batch_id": batch_id})
            if batch_data:
                sent_files = []
                for f_id in batch_data["files"]:
                    s = await c.send_cached_media(m.chat.id, f_id)
                    sent_files.append(s.id)
                await m.reply("⏳ Files will delete in 30 minutes.")
                scheduler.add_job(lambda: c.delete_messages(m.chat.id, sent_files), "date", run_date=datetime.now() + timedelta(minutes=30))

# --- WEB SERVER & MAIN ---
async def web_handle(request): return web.Response(text="Bot Live")

async def start_web_server():
    webapp = web.Application()
    webapp.router.add_get("/", web_handle)
    runner = web.AppRunner(webapp)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

async def main():
    if not scheduler.running: scheduler.start()
    await start_web_server()
    await app.start()
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
