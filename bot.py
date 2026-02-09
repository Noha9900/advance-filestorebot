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
        config = {
            "id": "config", 
            "support_active": True, 
            "welcome_enabled": False, 
            "fjoin_channels": [], 
            "fjoin_text": "<b>Join our channels to continue!</b>",
            "welcome_sec": 10
        }
        await db.settings.insert_one(config)
    return config

# --- FORCE JOIN CHECKER ---
async def is_subscribed(client, user_id):
    config = await get_config()
    channels = config.get("fjoin_channels", [])
    if not channels: return True
    for chat_id in channels:
        try:
            member = await client.get_chat_member(chat_id, user_id)
            if member.status in ["left", "kicked"]: return False
        except Exception: return False
    return True

# --- COMMAND: ADMIN ---
@app.on_message(filters.command("admin") & filters.user(ADMINS))
async def admin_cmd_handler(c, m):
    await m.reply_text("🛠 **Admin Control Panel**", reply_markup=get_admin_main())

# --- COMMAND: START ---
@app.on_message(filters.command("start") & filters.private)
async def start_handler(c, m):
    # Save User to DB
    await db.users.update_one({"id": m.from_user.id}, {"$set": {"name": m.from_user.first_name, "date": datetime.now()}}, upsert=True)
    
    config = await get_config()
    user_id = m.from_user.id
    
    # 1. Welcome Phase
    if config.get("welcome_enabled"):
        w_text = config.get("welcome_text", "Welcome!").replace("{name}", m.from_user.first_name)
        w_photo = config.get("welcome_photo")
        msg = await (m.reply_photo(w_photo, caption=w_text) if w_photo else m.reply_text(w_text))
        
        async def auto_del(message, delay):
            await asyncio.sleep(delay)
            try: await message.delete()
            except: pass
        asyncio.create_task(auto_del(msg, config.get("welcome_sec", 10)))

    # 2. Force Join Check
    if not await is_subscribed(c, user_id):
        await asyncio.sleep(config.get("welcome_sec", 10))
        buttons = []
        for cid in config.get("fjoin_channels", []):
            try:
                chat = await c.get_chat(cid)
                invite = await c.export_chat_invite_link(cid)
                buttons.append([InlineKeyboardButton(f"Join {chat.title}", url=invite)])
            except: continue
        return await m.reply_text(config.get("fjoin_text"), reply_markup=InlineKeyboardMarkup(buttons))

    # 3. Handle Batch File Links
    if len(m.text.split()) > 1 and "batch_" in m.text:
        b_id = m.text.split("_")[1]
        data = await db.batches.find_one({"batch_id": b_id})
        if data:
            sent = []
            for f in data['files']:
                s = await c.send_cached_media(m.chat.id, f)
                sent.append(s.id)
            
            scheduler.add_job(lambda: c.delete_messages(m.chat.id, sent), "date", 
                              run_date=datetime.now() + timedelta(minutes=30))
            await m.reply("⏳ Files will be deleted in 30 minutes for security.")

# --- CALLBACK HANDLER ---
@app.on_callback_query()
async def cb_handler(c, cb: CallbackQuery):
    if cb.data == "main_admin":
        await cb.message.edit_text("🛠 **Admin Control Panel**", reply_markup=get_admin_main())
    elif cb.data == "delete_msg":
        await cb.message.delete()
    # Add other callback logics here...

# --- WEB SERVER ---
async def start_web_server():
    webapp = web.Application()
    webapp.router.add_get("/", lambda r: web.Response(text="Bot Running"))
    runner = web.AppRunner(webapp)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

async def main():
    if not scheduler.running: scheduler.start()
    await start_web_server()
    await app.start()
    await asyncio.Event().wait()

if __name__ == "__main__":
    app.run(main())
