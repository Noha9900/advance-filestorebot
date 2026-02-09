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

# --- COMMANDS ---
@app.on_message(filters.command("admin") & filters.user(ADMINS))
async def admin_cmd(c, m):
    await m.reply_text("🛠 **Admin Control Panel**", reply_markup=get_admin_main())

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(c, m):
    # Save User to DB
    await db.users.update_one({"id": m.from_user.id}, {"$set": {"name": m.from_user.first_name}}, upsert=True)
    
    config = await get_config()
    
    # 1. SEND WELCOME MESSAGE
    if config.get("welcome_enabled"):
        welcome_text = config.get("welcome_text", "Welcome {name}!").replace("{name}", m.from_user.first_name)
        w_photo = config.get("welcome_photo")
        
        if w_photo:
            welcome_msg = await m.reply_photo(w_photo, caption=welcome_text)
        else:
            welcome_msg = await m.reply_text(welcome_text)
            
        # AUTO-DELETE WELCOME
        async def delete_welcome():
            await asyncio.sleep(config.get("welcome_sec", 10))
            try: await welcome_msg.delete()
            except: pass
        asyncio.create_task(delete_welcome())

    # 2. HANDLE BATCH RETRIEVAL
    if len(m.text.split()) > 1:
        payload = m.text.split()[1]
        if payload.startswith("batch_"):
            batch_id = payload.replace("batch_", "")
            batch_data = await db.batches.find_one({"batch_id": batch_id})
            
            if not batch_data:
                return await m.reply_text("❌ This batch link has expired or is invalid.")
            
            sent_files = []
            for file_id in batch_data["files"]:
                try:
                    # Send as cached media to avoid re-uploading
                    sent = await c.send_cached_media(m.chat.id, file_id)
                    sent_files.append(sent.id)
                    await asyncio.sleep(0.5) # Prevent flood
                except Exception as e:
                    print(f"Error sending file: {e}")

            await m.reply_text("⏳ **Security Alert:** These files will be deleted in 30 minutes.")
            
            # SCHEDULE 30-MIN DELETE
            scheduler.add_job(
                lambda: c.delete_messages(m.chat.id, sent_files),
                "date",
                run_date=datetime.now() + timedelta(minutes=30)
            )

# --- ADMIN INPUT & CALLBACKS ---
# (Keep your existing cb_handler and admin_input_processor logic here)
