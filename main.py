import os, asyncio, uuid, pytz
from datetime import datetime, timedelta
from pyrogram import Client, filters, types
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from motor.motor_asyncio import AsyncIOMotorClient
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp import web  # Added for Port binding

# --- CONFIG ---
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split(",")]
PORT = int(os.getenv("PORT", 8080)) # Port for Render

app = Client("ComplexBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
db = AsyncIOMotorClient(MONGO_URL).FileStoreBot
scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")

# --- UTILS & KEYBOARDS ---
def glass_markup(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, cb) for text, cb in row] for row in rows])

BACK_DEL = [("⬅️ Back", "main_admin"), ("🗑 Delete", "delete_msg")]

# --- ADMIN PANEL ---
@app.on_message(filters.command("admin") & filters.user(ADMINS))
async def admin_panel(c, m):
    buttons = [
        [("👋 Welcome Set", "set_welcome"), ("📢 Force Join", "set_fjoin")],
        [("🎧 Support Status", "toggle_support"), ("📂 Batch Store", "start_batch")],
        [("🚀 Broadcast", "bcast_menu"), ("📊 Stats", "stats")],
        BACK_DEL
    ]
    await m.reply_text("🛠 **Admin Control Panel**", reply_markup=glass_markup(buttons))

# --- BATCH FILE STORAGE ---
batch_temp = {}

@app.on_callback_query(filters.regex("start_batch"))
async def init_batch(c, cb):
    batch_temp[cb.from_user.id] = []
    await cb.message.edit_text("📤 Send files/videos now. Click **Done** when finished.", 
                               reply_markup=glass_markup([[("✅ Done", "save_batch")]]))

@app.on_message(filters.user(ADMINS) & (filters.document | filters.video | filters.photo))
async def collect_batch(c, m):
    if m.from_user.id in batch_temp:
        f_id = m.document.file_id if m.document else (m.video.file_id if m.video else m.photo.file_id)
        batch_temp[m.from_user.id].append(f_id)
        await m.reply(f"📥 Added. Total: {len(batch_temp[m.from_user.id])}")

@app.on_callback_query(filters.regex("save_batch"))
async def finalize_batch(c, cb):
    u_id = cb.from_user.id
    if not batch_temp.get(u_id): return await cb.answer("No files!")
    
    batch_id = str(uuid.uuid4())[:8]
    await db.batches.insert_one({"batch_id": batch_id, "files": batch_temp[u_id]})
    del batch_temp[u_id]
    
    link = f"https://t.me/{(await c.get_me()).username}?start=batch_{batch_id}"
    await cb.message.edit_text(f"✅ **Batch Saved!**\nLink: `{link}`")

# --- SUPPORT CHAT LOGIC ---
@app.on_message(filters.private & ~filters.user(ADMINS))
async def support_handler(c, m):
    config = await db.settings.find_one({"id": "config"})
    if not config or not config.get("support_active"): return
    
    if m.video and m.video.duration > 180:
        return await m.reply("❌ Videos over 3 mins not allowed.")

    status = "🟢 Online" if config.get("admin_online") else "🔴 Admin Offline. Leave a message."
    for admin in ADMINS:
        await c.copy_message(admin, m.chat.id, m.id, 
                             caption=f"📩 **New Msg**\nFrom: {m.from_user.first_name}\nID: `{m.from_user.id}`",
                             reply_markup=glass_markup([[("Reply", f"reply_{m.from_user.id}"), ("End", "end_chat")]]))
    await m.reply(status)

# --- FILE RETRIEVAL (30 MIN AUTO-DELETE) ---
@app.on_message(filters.command("start"))
async def start_handler(c, m):
    if "batch_" in m.text:
        b_id = m.text.split("_")[1]
        data = await db.batches.find_one({"batch_id": b_id})
        
        sent_msgs = []
        if data:
            for f in data['files']:
                s = await c.send_cached_media(m.chat.id, f)
                sent_msgs.append(s.id)
            
            await m.reply("⏳ These files will be deleted in 30 minutes.")
            scheduler.add_job(delete_batch_msgs, "date", 
                              run_date=datetime.now() + timedelta(minutes=30), 
                              args=[m.chat.id, sent_msgs])

async def delete_batch_msgs(chat_id, msg_ids):
    await app.delete_messages(chat_id, msg_ids)

# --- BROADCAST SCHEDULER ---
@app.on_callback_query(filters.regex("bcast_menu"))
async def bcast_setup(c, cb):
    pass

# --- PORT 8080 SERVER FOR RENDER ---
async def handle(request):
    return web.Response(text="Bot is Running!")

async def start_server():
    server = web.Server(handle)
    runner = web.ServerRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

# --- MAIN RUNNER ---
async def main():
    scheduler.start()
    await start_server() # Start the Port listener
    await app.start()
    print("Bot is alive!")
    await asyncio.Event().wait() # Keeps the script running

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
