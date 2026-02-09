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
            "fjoin_text": "Join our channels to continue!",
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

# --- ADMIN CALLBACK HANDLERS ---
@app.on_callback_query(filters.user(ADMINS))
async def handle_admin_callbacks(c, cb: CallbackQuery):
    data = cb.data
    if data == "main_admin":
        await cb.message.edit_text("🛠 **Admin Control Panel**", reply_markup=get_admin_main())
    elif data == "set_fjoin":
        admin_states[cb.from_user.id] = {"state": "waiting_fjoin_ids"}
        await cb.message.edit_text("📢 **Step 1:** Send Channel IDs separated by commas.\nExample: `-100123,-100456`",
                                   reply_markup=glass_markup([[("❌ Cancel", "main_admin")]]))
    elif data == "set_welcome":
        admin_states[cb.from_user.id] = {"state": "waiting_welcome_photo"}
        await cb.message.edit_text("🖼 **Welcome Step 1:** Send Photo or Skip.",
                                   reply_markup=glass_markup([[("⏩ Skip", "skip_photo")], [("❌ Cancel", "main_admin")]]))
    elif data == "delete_msg":
        await cb.message.delete()

# --- INPUT PROCESSOR (Logic for Admin Settings) ---
@app.on_message(filters.user(ADMINS) & filters.private)
async def admin_input_processor(c, m):
    uid = m.from_user.id
    if uid not in admin_states: return
    state = admin_states[uid]["state"]

    if state == "waiting_fjoin_ids":
        try:
            ids = [int(i.strip()) for i in m.text.split(",")]
            await db.settings.update_one({"id": "config"}, {"$set": {"fjoin_channels": ids}})
            admin_states[uid] = {"state": "waiting_fjoin_text"}
            await m.reply("✅ IDs Saved. Now send the **Force Join Text** (HTML supported).")
        except: await m.reply("❌ Invalid IDs. Use: `-100123, -100456`")
    
    elif state == "waiting_fjoin_text":
        await db.settings.update_one({"id": "config"}, {"$set": {"fjoin_text": m.text}})
        await m.reply("✅ Force Join Updated!", reply_markup=get_admin_main())
        del admin_states[uid]

    elif state == "waiting_welcome_sec":
        if m.text.isdigit():
            await db.settings.update_one({"id": "config"}, {"$set": {"welcome_sec": int(m.text)}})
            await m.reply(f"✅ Timer set to {m.text}s.", reply_markup=get_admin_main())
            del admin_states[uid]

# --- USER GATEWAY (The Start Handler) ---
@app.on_message(filters.command("start") & filters.private)
async def start_handler(c, m):
    config = await get_config()
    user_id = m.from_user.id
    
    # 1. Welcome Phase
    if config.get("welcome_enabled"):
        w_text = config.get("welcome_text", "Welcome!").replace("{name}", m.from_user.first_name)
        w_photo = config.get("welcome_photo")
        
        if w_photo: msg = await m.reply_photo(w_photo, caption=w_text)
        else: msg = await m.reply_text(w_text)
        
        # Immediate timer for disappearance
        async def auto_del(message, delay):
            await asyncio.sleep(delay)
            try: await message.delete()
            except: pass
        
        asyncio.create_task(auto_del(msg, config.get("welcome_sec", 10)))

    # 2. Force Join Check
    if not await is_subscribed(c, user_id):
        # Once welcome disappears, user sees this
        await asyncio.sleep(config.get("welcome_sec", 10))
        buttons = []
        for cid in config.get("fjoin_channels", []):
            try:
                chat = await c.get_chat(cid)
                buttons.append([InlineKeyboardButton(f"Join {chat.title}", url=chat.invite_link or "https://t.me")])
            except: continue
        
        return await m.reply_text(config.get("fjoin_text"), reply_markup=InlineKeyboardMarkup(buttons))

    # 3. Handle Batch File Links
    if "batch_" in m.text:
        b_id = m.text.split("_")[1]
        data = await db.batches.find_one({"batch_id": b_id})
        if data:
            sent = []
            for f in data['files']:
                s = await c.send_cached_media(m.chat.id, f)
                sent.append(s.id)
            
            # 30-minute auto delete
            scheduler.add_job(lambda: c.delete_messages(m.chat.id, sent), "date", 
                              run_date=datetime.now() + timedelta(minutes=30))
            await m.reply("⏳ Files will be deleted in 30 minutes for security.")

# --- SERVER ---
async def main():
    if not scheduler.running: scheduler.start()
    await app.start()
    # Web server logic for Render...
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
