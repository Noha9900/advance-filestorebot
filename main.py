import logging
import asyncio
import os
import uuid
from datetime import datetime, timedelta

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ContextTypes, JobQueue
)

# --- IMPORTS FOR MONGODB & WEB SERVER ---
from motor.motor_asyncio import AsyncIOMotorClient
from aiohttp import web

# ================= CONFIGURATION =================
BOT_TOKEN = os.getenv("BOT_TOKEN") 
ADMIN_ID = int(os.getenv("ADMIN_ID"))
MONGO_URL = os.getenv("MONGO_URL")

# Logging Setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================= MONGODB MANAGER =================
class Database:
    def __init__(self, uri):
        self.client = AsyncIOMotorClient(uri)
        self.db = self.client['telegram_bot_db']
        
        # Collections
        self.users = self.db.users
        self.channels = self.db.channels
        self.content = self.db.content
        self.settings = self.db.settings

    async def add_user(self, user_id):
        await self.users.update_one({'_id': user_id}, {'$set': {'joined_date': datetime.now()}}, upsert=True)

    async def get_all_users(self):
        cursor = self.users.find({})
        return [user['_id'] async for user in cursor]

    async def count_users(self):
        return await self.users.count_documents({})

    async def set_setting(self, key, value):
        await self.settings.update_one({'_id': key}, {'$set': {'value': value}}, upsert=True)

    async def get_setting(self, key):
        doc = await self.settings.find_one({'_id': key})
        return doc['value'] if doc else None

    async def add_channel(self, chat_id, link):
        await self.channels.update_one({'_id': chat_id}, {'$set': {'link': link}}, upsert=True)

    async def get_channels(self):
        cursor = self.channels.find({})
        return [{'id': ch['_id'], 'link': ch['link']} async for ch in cursor]

    async def save_content(self, unique_id, chat_id, msg_id, caption):
        await self.content.insert_one({
            '_id': unique_id,
            'source_chat': chat_id,
            'msg_id': msg_id,
            'caption': caption
        })

    async def get_content(self, unique_id):
        return await self.content.find_one({'_id': unique_id})

# Initialize DB Global
db = None

# ================= WEB SERVER (KEEP ALIVE) =================
async def health_check(request):
    return web.Response(text="Bot is Alive & Connected to MongoDB!")

async def run_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()

# ================= HELPER FUNCTIONS =================
async def auto_delete_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    try:
        await context.bot.delete_message(chat_id=job.data['chat_id'], message_id=job.data['message_id'])
    except Exception:
        pass

# ================= ADMIN COMMANDS & PANELS =================

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    
    # Glass Button Menu for Admin
    keyboard = [
        [
            InlineKeyboardButton("➕ Add Content", callback_data="help_add"),
            InlineKeyboardButton("📢 Broadcast", callback_data="help_cast")
        ],
        [
            InlineKeyboardButton("📝 Set Welcome", callback_data="help_welcome"),
            InlineKeyboardButton("🔗 Add Channel", callback_data="help_channel")
        ],
        [
            InlineKeyboardButton("📊 Bot Stats", callback_data="admin_stats"),
            InlineKeyboardButton("❌ Close", callback_data="close_msg")
        ]
    ]
    
    await update.message.reply_text(
        "<b>🛡️ Admin Control Panel</b>\nSelect an option below to manage your bot:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID: 
        await query.answer("Admin only!", show_alert=True)
        return

    await query.answer()
    data = query.data

    if data == "close_msg":
        await query.message.delete()
        
    elif data == "admin_stats":
        count = await db.count_users()
        await query.message.edit_text(
            f"<b>📊 Live Statistics</b>\n\n👥 Total Users: <b>{count}</b>\n\n(Back to /admin)",
            parse_mode=ParseMode.HTML
        )
        
    elif data == "help_add":
        await query.edit_message_text(
            "<b>➕ How to Add Content</b>\n\n1. Upload or Forward a file to me.\n2. Reply to it with <code>/add</code>\n3. I will give you the shareable link.",
            parse_mode=ParseMode.HTML
        )
        
    elif data == "help_cast":
        await query.edit_message_text(
            "<b>📢 How to Broadcast</b>\n\n1. Send the message you want to broadcast.\n2. Reply to it with <code>/broadcast</code>",
            parse_mode=ParseMode.HTML
        )
        
    elif data == "help_welcome":
        await query.edit_message_text(
            "<b>📝 Set Welcome Message</b>\n\nUse command:\n<code>/setwelcome Your Message Here</code>",
            parse_mode=ParseMode.HTML
        )
        
    elif data == "help_channel":
        await query.edit_message_text(
            "<b>🔗 Force Join Channel</b>\n\nUse command:\n<code>/addchannel -100xxxxxxx https://t.me/...</code>",
            parse_mode=ParseMode.HTML
        )

# --- Admin Functional Commands ---

async def cmd_add_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    reply = update.message.reply_to_message
    if not reply: return await update.message.reply_text("❌ Reply to a file.")
    
    unique_code = str(uuid.uuid4())[:8]
    await db.save_content(unique_code, reply.chat_id, reply.message_id, reply.caption or "")
    
    link = f"https://t.me/{context.bot.username}?start={unique_code}"
    
    # Glass button for the result
    kb = [[InlineKeyboardButton("↗️ Share Link", url=f"https://t.me/share/url?url={link}")]]
    
    await update.message.reply_text(
        f"✅ <b>Content Saved!</b>\n\n<code>{link}</code>", 
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    reply = update.message.reply_to_message
    if not reply: return await update.message.reply_text("Reply to a message.")
    
    msg = await update.message.reply_text("⏳ Broadcast started...")
    users = await db.get_all_users()
    
    success = 0
    for user_id in users:
        try:
            await reply.copy(chat_id=user_id)
            success += 1
            if success % 20 == 0: await asyncio.sleep(1)
        except: pass
        
    await msg.edit_text(f"✅ Broadcast sent to {success} users.")

async def cmd_set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    msg = update.message.text.partition(' ')[2]
    await db.set_setting('welcome_msg', msg)
    await update.message.reply_text("✅ Welcome updated.")

async def cmd_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        _, cid, link = update.message.text.split()
        await db.add_channel(cid, link)
        await update.message.reply_text("✅ Channel added.")
    except:
        await update.message.reply_text("Usage: /addchannel -100xxx https://t.me/...")

# ================= USER FLOW =================

async def check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    channels = await db.get_channels()
    
    not_joined = []
    for ch in channels:
        try:
            m = await context.bot.get_chat_member(ch['id'], user_id)
            if m.status not in ['member', 'administrator', 'creator']:
                not_joined.append(ch['link'])
        except: pass
        
    if not_joined:
        # Glass Buttons for Join Links
        btns = []
        for i, link in enumerate(not_joined):
            btns.append([InlineKeyboardButton(f"🔔 Join Channel {i+1}", url=link)])
        
        btns.append([InlineKeyboardButton("🔄 Refresh / Try Again", callback_data="check_join_user")])
        
        msg = await update.message.reply_text(
            "🛑 <b>Access Denied</b>\n\nYou must join our channels to access the files.",
            reply_markup=InlineKeyboardMarkup(btns), 
            parse_mode=ParseMode.HTML
        )
        context.job_queue.run_once(auto_delete_job, 60, data={'chat_id': user_id, 'message_id': msg.message_id})
        return False
    return True

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await db.add_user(user_id)
    
    # 1. Welcome with Glass Buttons
    w_text = await db.get_setting('welcome_msg') or "Welcome to the Bot!"
    
    # Add a cool keyboard to welcome message
    welcome_kb = [
        [InlineKeyboardButton("📢 Updates", url="https://t.me/YourUpdatesChannel"), InlineKeyboardButton("🆘 Support", url="https://t.me/YourAdminUser")]
    ]
    
    w_msg = await update.message.reply_text(
        w_text, 
        reply_markup=InlineKeyboardMarkup(welcome_kb),
        parse_mode=ParseMode.HTML
    )
    context.job_queue.run_once(auto_delete_job, 15, data={'chat_id': user_id, 'message_id': w_msg.message_id})

    # 2. Force Join
    if not await check_join(update, context): return

    # 3. Deep Link Content
    if context.args:
        code = context.args[0]
        data = await db.get_content(code)
        if data:
            try:
                # Send Content
                msg = await context.bot.copy_message(
                    chat_id=user_id, from_chat_id=data['source_chat'], 
                    message_id=data['msg_id'], caption=data.get('caption', "")
                )
                
                # Vanish Content (30 mins)
                context.job_queue.run_once(auto_delete_job, 1800, data={'chat_id': user_id, 'message_id': msg.message_id})
                
                info = await update.message.reply_text("⚠️ This file deletes in 30 mins.")
                context.job_queue.run_once(auto_delete_job, 1800, data={'chat_id': user_id, 'message_id': info.message_id})
            except Exception as e:
                await update.message.reply_text("❌ File not found (Check Bot Admin Status).")

async def user_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    # Route Admin vs User callbacks
    if query.data.startswith(("admin_", "help_", "close_")):
        await admin_callback_handler(update, context)
        return

    await query.answer()
    if query.data == "check_join_user":
        if await check_join(update, context):
            await query.message.delete()
            await query.message.reply_text("✅ <b>Verified!</b>\n\nPlease click the link again or type /start", parse_mode=ParseMode.HTML)

# ================= MAIN =================
def main():
    global db
    # Check Env Vars
    if not MONGO_URL:
        print("❌ ERROR: MONGO_URL is missing in Environment Variables!")
        return

    # Init DB
    db = Database(MONGO_URL)
    
    # Init Bot
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("add", cmd_add_content))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("setwelcome", cmd_set_welcome))
    app.add_handler(CommandHandler("addchannel", cmd_add_channel))
    
    # Central Callback Handler for Glass Buttons
    app.add_handler(CallbackQueryHandler(user_callback_handler))

    # Run Web Server + Bot
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(run_web_server())
    
    print("🚀 Bot is Starting with Glass Buttons...")
    app.run_polling()

if __name__ == "__main__":
    main()
