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

admin_states = {} # Track admin inputs
batch_temp = {}   # Track file batching

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
        config = {"id": "config", "support_active": True, "welcome_enabled": False, "fjoin_channels": []}
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
    config = await get_config()

    if data == "main_admin":
        await cb.message.edit_text("🛠 **Admin Control Panel**", reply_markup=get_admin_main())

    elif data == "stats":
        u_count = await db.users.count_documents({})
        b_count = await db.batches.count_documents({})
        await cb.message.edit_text(f"📊 **Stats**\n\nUsers: `{u_count}`\nBatches: `{b_count}`", 
                                   reply_markup=glass_markup([[("⬅️ Back", "main_admin")]]))

    elif data == "toggle_support":
        new_val = not config.get("support_active", True)
        await db.settings.update_one({"id": "config"}, {"$set": {"support_active": new_val}})
        await cb.answer(f"Support is now {'ON' if new_val else 'OFF'}", show_alert=True)

    elif data == "set_fjoin":
        admin_states[cb.from_user.id] = "awaiting_fjoin"
        await cb.message.edit_text("📢 **Send the Channel ID(s)** separated by commas.\nExample: `-100123,-100456`",
                                   reply_markup=glass_markup([[("❌ Cancel", "main_admin")]]))

    elif data == "bcast_menu":
        admin_states[cb.from_user.id] = "awaiting_bcast"
        await cb.message.edit_text("🚀 **Send the message you want to broadcast.**\n(Text, Photo, or Video)",
                                   reply_markup=glass_markup([[("❌ Cancel", "main_admin")]]))

    elif data == "delete_msg":
        await cb.message.delete()

# --- ADMIN INPUT LOGIC ---
@app.on_message(filters.user(ADMINS) & filters.private)
async def admin_input_processor(c, m):
    uid = m.from_user.id
    if uid not in admin_states: return

    state = admin_states[uid]
    if state == "awaiting_fjoin":
        ch_list = [int(i.strip()) for i in m.text.split(",")]
        await db.settings.update_one({"id": "config"}, {"$set": {"fjoin_channels": ch_list}})
        await m.reply("✅ Force Join Channels Updated!", reply_markup=get_admin_main())
    
    elif state == "awaiting_bcast":
        users = db.users.find({})
        count = 0
        async for user in users:
            try:
                await m.copy(user['id'])
                count += 1
                await asyncio.sleep(0.05)
            except: pass
        await m.reply(f"✅ Broadcast Sent to {count} users.")
    
    del admin_states[uid]

# --- FILE RETRIEVAL & AUTO-DELETE ---
@app.on_message(filters.command("start") & filters.private)
async def start_handler(c, m):
    # Track User
    await db.users.update_one({"id": m.from_user.id}, {"$set": {"active": True}}, upsert=True)
    
    if not await is_subscribed(c, m.from_user.id):
        return await m.reply("🚫 **Access Denied!**\nPlease join our channels first to use the bot.",
                             reply_markup=glass_markup([[("Join Now", "https://t.me/YourChannel")]]))

    if "batch_" in m.text:
        b_id = m.text.split("_")[1]
        data = await db.batches.find_one({"batch_id": b_id})
        if data:
            sent = []
            for f in data['files']:
                s = await c.send_cached_media(m.chat.id, f)
                sent.append(s.id)
            await m.reply("⏳ Files will be deleted in 30 minutes.")
            scheduler.add_job(lambda: c.delete_messages(m.chat.id, sent), "date", 
                              run_date=datetime.now() + timedelta(minutes=30))

# --- PORT & SERVER ---
async def keep_alive(request): return web.Response(text="Bot Active")
async def main():
    scheduler.start()
    server = web.AppRunner(web.Application())
    await server.setup()
    await web.TCPSite(server, "0.0.0.0", PORT).start()
    await app.start()
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
