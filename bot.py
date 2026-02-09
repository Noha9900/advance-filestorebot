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
@app.on_callback_query()
async def cb_handler(c, cb: CallbackQuery):
    if cb.from_user.id not in ADMINS:
        return await cb.answer("❌ Admin Only!", show_alert=True)
    
    data = cb.data
    config = await get_config()

    if data == "main_admin":
        await cb.message.edit_text("🛠 **Admin Control Panel**", reply_markup=get_admin_main())
    
    elif data == "stats":
        u_count = await db.users.count_documents({})
        b_count = await db.batches.count_documents({})
        await cb.message.edit_text(f"📊 **Statistics**\n\n👤 Total Users: `{u_count}`\n📂 Total Batches: `{b_count}`", 
                                   reply_markup=glass_markup([[("⬅️ Back", "main_admin")]]))

    elif data == "toggle_support":
        new_val = not config.get("support_active", True)
        await db.settings.update_one({"id": "config"}, {"$set": {"support_active": new_val}})
        await cb.answer(f"Support is now {'ON' if new_val else 'OFF'}", show_alert=True)

    elif data == "set_fjoin":
        admin_states[cb.from_user.id] = "waiting_fjoin"
        await cb.message.edit_text("📢 **Send Channel IDs** separated by commas.\nExample: `-100123,-100456`", reply_markup=glass_markup([[("❌ Cancel", "main_admin")]]))

    elif data == "start_batch":
        batch_temp[cb.from_user.id] = []
        await cb.message.edit_text("📤 **Batch Mode Active**\nSend files/videos now. Click **Done** when finished.", 
                                   reply_markup=glass_markup([[("✅ Done", "save_batch")]]))

    elif data == "save_batch":
        u_id = cb.from_user.id
        if not batch_temp.get(u_id): return await cb.answer("No files added!")
        b_id = str(uuid.uuid4())[:8]
        await db.batches.insert_one({"batch_id": b_id, "files": batch_temp[u_id]})
        link = f"https://t.me/{(await c.get_me()).username}?start=batch_{b_id}"
        await cb.message.edit_text(f"✅ **Batch Saved!**\nLink: `{link}`", reply_markup=get_admin_main())
        del batch_temp[u_id]

    elif data == "bcast_menu":
        admin_states[cb.from_user.id] = "waiting_bcast"
        await cb.message.edit_text("🚀 **Send Broadcast Message**\nAnything you send now will go to all users.", reply_markup=glass_markup([[("❌ Cancel", "main_admin")]]))

    elif data == "delete_msg":
        await cb.message.delete()

# --- ADMIN INPUT PROCESSOR ---
@app.on_message(filters.user(ADMINS) & filters.private & ~filters.command(["admin", "start"]))
async def admin_input_processor(c, m):
    uid = m.from_user.id
    if uid in batch_temp:
        f_id = m.document.file_id if m.document else (m.video.file_id if m.video else m.photo.file_id)
        batch_temp[uid].append(f_id)
        return await m.reply(f"📥 Added. Total: {len(batch_temp[uid])}")

    if uid not in admin_states: return
    state = admin_states[uid]

    if state == "waiting_fjoin":
        try:
            ids = [int(i.strip()) for i in m.text.split(",")]
            await db.settings.update_one({"id": "config"}, {"$set": {"fjoin_channels": ids}})
            await m.reply("✅ Force Join Updated!", reply_markup=get_admin_main())
        except: await m.reply("❌ Invalid IDs.")
    
    elif state == "waiting_bcast":
        users = db.users.find({})
        count = 0
        async for user in users:
            try:
                await m.copy(user['id'])
                count += 1
                await asyncio.sleep(0.1)
            except: pass
        await m.reply(f"✅ Broadcast Finished. Sent to {count} users.")
    
    del admin_states[uid]

# --- COMMANDS ---
@app.on_message(filters.command("admin") & filters.user(ADMINS))
async def admin_cmd(c, m):
    await m.reply_text("🛠 **Admin Control Panel**", reply_markup=get_admin_main())

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(c, m):
    await db.users.update_one({"id": m.from_user.id}, {"$set": {"name": m.from_user.first_name}}, upsert=True)
    # [Insert your Welcome/FJoin Logic here as per previous steps]
    await m.reply("Bot is Active! Send /admin to manage.")

# --- SERVER ---
async def main():
    if not scheduler.running: scheduler.start()
    webapp = web.Application()
    webapp.router.add_get("/", lambda r: web.Response(text="Bot Running"))
    runner = web.AppRunner(webapp)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    await app.start()
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
