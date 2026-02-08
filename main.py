import logging
import asyncio
import os
import uuid
import pytz
import certifi
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ContextTypes, JobQueue, MessageHandler, filters, ConversationHandler
)

# --- MongoDB & Web Server ---
from motor.motor_asyncio import AsyncIOMotorClient
from aiohttp import web

# ================= CONFIGURATION =================
BOT_TOKEN = os.getenv("BOT_TOKEN") 
ADMIN_ID = int(os.getenv("ADMIN_ID"))
MONGO_URL = os.getenv("MONGO_URL")

# Timezone for India
IST = pytz.timezone('Asia/Kolkata')

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= DATABASE =================
class Database:
    def __init__(self, uri):
        self.client = AsyncIOMotorClient(uri, tlsCAFile=certifi.where())
        self.db = self.client['pro_bot_db']
        self.users = self.db.users
        self.channels = self.db.channels
        self.content = self.db.content
        self.settings = self.db.settings
        self.buttons = self.db.buttons
        self.support = self.db.support_sessions

    async def add_user(self, user_id, name, username):
        await self.users.update_one(
            {'_id': user_id}, 
            {'$set': {'name': name, 'username': username, 'last_active': datetime.now()}}, 
            upsert=True
        )

    async def get_stats(self):
        total = await self.users.count_documents({})
        # Active in last 24h (rough estimate)
        active = await self.users.count_documents({'last_active': {'$gte': datetime.now() - timedelta(hours=24)}})
        return total, active

    async def save_batch(self, unique_id, source_chat, start_id, end_id, caption):
        await self.content.insert_one({
            '_id': unique_id, 'type': 'batch', 'source_chat': source_chat,
            'start_id': start_id, 'end_id': end_id, 'caption': caption
        })

    async def save_single(self, unique_id, source_chat, msg_id, caption):
        await self.content.insert_one({
            '_id': unique_id, 'type': 'single', 'source_chat': source_chat,
            'msg_id': msg_id, 'caption': caption
        })

db = None

# ================= STATES FOR CONVERSATIONS =================
(
    ADD_CONTENT_CHOICE, GET_SINGLE_FILE, GET_BATCH_START, GET_BATCH_END,
    SET_WELCOME_MEDIA, SET_WELCOME_TEXT,
    BROADCAST_MSG, BROADCAST_BUTTONS, BROADCAST_SCHEDULE,
    ADD_CHANNEL_LINK,
    ADD_BUTTON_TEXT, ADD_BUTTON_LINK,
    SUPPORT_CHAT
) = range(13)

# ================= ADMIN PANEL =================
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Content (Single/Batch)", callback_data="menu_add_content")],
        [InlineKeyboardButton("📢 Broadcast / Schedule", callback_data="menu_broadcast")],
        [InlineKeyboardButton("📝 Set Welcome", callback_data="menu_welcome"), InlineKeyboardButton("🔗 Add Channel", callback_data="menu_channel")],
        [InlineKeyboardButton("🔘 Add Custom Button", callback_data="menu_btn"), InlineKeyboardButton("🗑️ Clear Buttons", callback_data="menu_clear")],
        [InlineKeyboardButton("📊 Stats", callback_data="stats"), InlineKeyboardButton("🆘 Support Settings", callback_data="menu_support")]
    ]
    await update.message.reply_text("<b>🛡️ ULTIMATE ADMIN PANEL</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

# ================= CONTENT SYSTEM (SINGLE & BATCH) =================
async def menu_add_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = [
        [InlineKeyboardButton("📄 Single File", callback_data="add_single"), InlineKeyboardButton("xh Batch (Range)", callback_data="add_batch")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_back")]
    ]
    await query.edit_message_text("<b>Choose Content Type:</b>\n\n1. <b>Single:</b> One file/video.\n2. <b>Batch:</b> Forward 1st and Last message to create a bulk link.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return ADD_CONTENT_CHOICE

async def start_single(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("<b>Send/Forward the Single File/Video/Photo now.</b>")
    return GET_SINGLE_FILE

async def save_single_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    unique_id = str(uuid.uuid4())[:8]
    # If forwarded from restricted channel, we use that chat_id
    src_chat = msg.forward_from_chat.id if msg.forward_from_chat else msg.chat_id
    
    await db.save_single(unique_id, src_chat, msg.message_id, msg.caption or "")
    link = f"https://t.me/{context.bot.username}?start={unique_id}"
    await update.message.reply_text(f"✅ <b>Single File Saved!</b>\nLink: <code>{link}</code>", parse_mode=ParseMode.HTML)
    return ConversationHandler.END

async def start_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("<b>BATCH MODE</b>\n\nForward the <b>FIRST</b> message from the restricted channel.")
    return GET_BATCH_START

async def get_batch_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.forward_from_chat:
        await update.message.reply_text("❌ You must forward this from a channel.")
        return GET_BATCH_START
    context.user_data['batch_chat'] = msg.forward_from_chat.id
    context.user_data['batch_start'] = msg.forward_from_message_id
    await update.message.reply_text("✅ Start ID recorded.\n\nNow forward the <b>LAST</b> message.")
    return GET_BATCH_END

async def get_batch_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.forward_from_chat.id != context.user_data['batch_chat']:
        await update.message.reply_text("❌ Msg must be from the SAME channel.")
        return ConversationHandler.END
    
    start_id = context.user_data['batch_start']
    end_id = msg.forward_from_message_id
    unique_id = str(uuid.uuid4())[:8]
    
    await db.save_batch(unique_id, context.user_data['batch_chat'], start_id, end_id, "Batch Files")
    link = f"https://t.me/{context.bot.username}?start={unique_id}"
    await update.message.reply_text(f"✅ <b>Batch Saved!</b>\nContains {end_id - start_id + 1} files.\nLink: <code>{link}</code>", parse_mode=ParseMode.HTML)
    return ConversationHandler.END

# ================= BROADCAST & SCHEDULE =================
async def menu_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("<b>Send the post (Text/Image/Video) you want to broadcast.</b>")
    return BROADCAST_MSG

async def get_broadcast_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Copy message to a temp storage ID (or just keep object in memory if small, but DB is safer)
    # For simplicity, we assume immediate processing or keeping in context
    context.user_data['broadcast_msg'] = update.message
    await update.message.reply_text(
        "<b>Add Buttons?</b>\nSend buttons in format:\n`Name - Link`\n`Name2 - Link2`\n\nType /skip to send without buttons.", 
        parse_mode=ParseMode.HTML
    )
    return BROADCAST_BUTTONS

async def get_broadcast_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    buttons = []
    if text != '/skip':
        for line in text.split('\n'):
            if '-' in line:
                name, url = line.split('-', 1)
                buttons.append([InlineKeyboardButton(name.strip(), url=url.strip())])
    context.user_data['broadcast_btns'] = buttons
    
    kb = [[InlineKeyboardButton("🚀 Send Now", callback_data="cast_now"), InlineKeyboardButton("⏰ Schedule", callback_data="cast_sched")]]
    await update.message.reply_text("Send now or Schedule?", reply_markup=InlineKeyboardMarkup(kb))
    return BROADCAST_SCHEDULE

async def execute_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "cast_now":
        await query.edit_message_text("⏳ Broadcast started...")
        asyncio.create_task(run_broadcast(context.user_data['broadcast_msg'], context.user_data['broadcast_btns'], context))
        return ConversationHandler.END
    elif data == "cast_sched":
        await query.edit_message_text("Type time in format `YYYY-MM-DD HH:MM` (IST Time).\nEx: `2026-02-10 14:30`")
        return BROADCAST_SCHEDULE # Re-use state for time input

async def schedule_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    time_str = update.message.text
    try:
        # Parse time in IST
        local_dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
        local_dt = IST.localize(local_dt)
        utc_dt = local_dt.astimezone(pytz.utc)
        
        # Add to Scheduler
        context.job_queue.run_once(
            scheduled_broadcast_job, 
            utc_dt, 
            data={'msg': context.user_data['broadcast_msg'], 'btns': context.user_data['broadcast_btns']}
        )
        await update.message.reply_text(f"✅ Scheduled for {time_str} IST")
    except ValueError:
        await update.message.reply_text("❌ Invalid Format. Use YYYY-MM-DD HH:MM")
    return ConversationHandler.END

async def run_broadcast(message, buttons, context):
    users = await db.get_all_users()
    markup = InlineKeyboardMarkup(buttons) if buttons else None
    success = 0
    for uid in users:
        try:
            await message.copy(chat_id=uid, reply_markup=markup)
            success += 1
            await asyncio.sleep(0.05)
        except: pass
    # Notify Admin
    await context.bot.send_message(ADMIN_ID, f"✅ Broadcast finished. Sent to {success} users.")

async def scheduled_broadcast_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    msg = job.data['msg']
    btns = job.data['btns']
    await run_broadcast(msg, btns, context)

# ================= SUPPORT CHAT =================
async def menu_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("To enable support chat button, use /setsupport in bot settings or main menu.")
    # This just toggles UI visibility, actual logic is in message handler
    return ConversationHandler.END

async def start_support_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Create 24h session
    end_time = datetime.now() + timedelta(hours=24)
    await db.support.update_one(
        {'_id': user_id}, 
        {'$set': {'end_time': end_time, 'user_name': update.effective_user.first_name, 'user_id_val': user_id}}, 
        upsert=True
    )
    await update.callback_query.answer("✅ Connected to Admin!", show_alert=True)
    await update.callback_query.message.reply_text("👨‍💻 <b>Support Chat Active (24h)</b>\n\nYou can send Text/Photos. Videos are NOT allowed.")
    await context.bot.send_message(ADMIN_ID, f"🚨 <b>New Support Chat</b>\nUser: {update.effective_user.first_name}\nID: <code>{user_id}</code>\n\nReply to their messages to answer.")

async def handle_support_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = update.message
    
    # 1. Check if it's Admin replying
    if user_id == ADMIN_ID:
        if msg.reply_to_message:
            # Need to extract original user ID from the forwarded/alert message
            # This is complex without storing message mapping.
            # Simplified: Admin should use /reply <id> <msg> OR we rely on topic/quote.
            # Robust way: 
            try:
                # We assume Admin replies to a message forwarded by bot which has User ID in it?
                # Actually, easier:
                pass
            except: pass
        return

    # 2. Check if User has active session
    session = await db.support.find_one({'_id': user_id})
    if session and session['end_time'] > datetime.now():
        # Check restrictions
        if msg.video or msg.video_note or msg.document:
            await msg.reply_text("❌ <b>Videos/Files Not Allowed!</b>\nOnly Text and Photos.")
            return
        
        # Forward to Admin
        fwd = await msg.forward(ADMIN_ID)
        await context.bot.send_message(ADMIN_ID, f"⬆️ Message from <code>{user_id}</code> ({update.effective_user.first_name})", reply_to_message_id=fwd.message_id)
    else:
        # No session, ignore or standard handler
        pass

async def admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    # Usage: Reply to a forwarded message with text
    if update.message.reply_to_message:
        # We try to extract ID from the helper text we sent "Message from 12345"
        # This requires the admin to reply to the TEXT notification, not the forwarded photo
        reply_to = update.message.reply_to_message
        if "Message from" in reply_to.text:
            try:
                target_id = int(reply_to.text.split('<code>')[1].split('</code>')[0])
                await context.bot.copy_message(chat_id=target_id, from_chat_id=ADMIN_ID, message_id=update.message.message_id)
                await update.message.reply_text("✅ Sent.")
            except:
                await update.message.reply_text("❌ Couldn't find User ID. Reply to the 'Message from...' text.")

# ================= WELCOME & CHANNELS =================
async def menu_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Send the Photo/Gif/Sticker for welcome message.")
    return SET_WELCOME_MEDIA

async def set_welcome_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Store file_id
    file_id = None
    if update.message.photo: file_id = update.message.photo[-1].file_id
    elif update.message.animation: file_id = update.message.animation.file_id
    elif update.message.sticker: file_id = update.message.sticker.file_id
    
    context.user_data['wel_media'] = file_id
    await update.message.reply_text("Now send the Caption/Text for welcome.")
    return SET_WELCOME_TEXT

async def set_welcome_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    media = context.user_data.get('wel_media')
    await db.settings.update_one({'_id': 'welcome'}, {'$set': {'text': text, 'media': media}}, upsert=True)
    await update.message.reply_text("✅ Welcome Set!")
    return ConversationHandler.END

async def menu_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Send Channel ID and Link.\nEx: `-10012345 https://t.me/join`")
    return ADD_CHANNEL_LINK

async def add_channel_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        cid, link = update.message.text.split()
        await db.add_channel(cid, link)
        await update.message.reply_text("✅ Channel Added.")
    except:
        await update.message.reply_text("Error. Format: ID Link")
    return ConversationHandler.END

# ================= USER HANDLERS =================
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
        kb = [[InlineKeyboardButton(f"Join Channel {i+1}", url=link)] for i, link in enumerate(not_joined)]
        kb.append([InlineKeyboardButton("✅ Checked Joined", callback_data="check_join_retry")])
        
        msg = await update.message.reply_text("🚧 <b>Join Required</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        
        # Vanish Logic (1 min to vanish join list)
        context.job_queue.run_once(auto_delete_job, 60, data={'chat_id': user_id, 'msg_id': msg.message_id})
        return False
    return True

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await db.add_user(user_id, update.effective_user.first_name, update.effective_user.username)
    
    # 1. Welcome
    wel = await db.settings.find_one({'_id': 'welcome'})
    text = wel['text'] if wel else "Welcome!"
    media = wel['media'] if wel else None
    
    # Custom Buttons
    custom_btns = await db.buttons.find({}).to_list(length=100)
    kb = []
    for btn in custom_btns:
        kb.append([InlineKeyboardButton(btn['name'], url=btn['link'])])
    
    # Add Support Button
    kb.append([InlineKeyboardButton("🆘 Support Chat", callback_data="start_support")])
    
    markup = InlineKeyboardMarkup(kb)
    
    if media:
        # Send Media
        try:
            msg = await update.message.reply_photo(media, caption=text, reply_markup=markup, parse_mode=ParseMode.HTML)
        except:
             msg = await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    else:
        msg = await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        
    # Vanish Welcome (15s)
    context.job_queue.run_once(auto_delete_job, 15, data={'chat_id': user_id, 'msg_id': msg.message_id})
    
    # 2. Force Join Check
    if not await check_join(update, context): return
    
    # 3. Handle Content Link
    if context.args:
        unique_id = context.args[0]
        data = await db.content.find_one({'_id': unique_id})
        if data:
            if data['type'] == 'single':
                await context.bot.copy_message(chat_id=user_id, from_chat_id=data['source_chat'], message_id=data['msg_id'], caption=data['caption'])
            elif data['type'] == 'batch':
                # Batch Delivery
                start = data['start_id']
                end = data['end_id']
                await update.message.reply_text(f"📂 <b>Sending Batch ({end-start+1} files)...</b>", parse_mode=ParseMode.HTML)
                for i in range(start, end + 1):
                    try:
                        await context.bot.copy_message(chat_id=user_id, from_chat_id=data['source_chat'], message_id=i)
                        await asyncio.sleep(0.05) # Prevent flood
                    except: pass
                await update.message.reply_text("✅ Batch Delivered.")

async def auto_delete_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.delete_message(context.job.data['chat_id'], context.job.data['msg_id'])
    except: pass

# ================= BUTTON MGMT =================
async def menu_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Send Button Name and Link.\nEx: `Movies https://t.me/movies`", parse_mode=ParseMode.HTML)
    return ADD_BUTTON_TEXT

async def add_btn_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text
        parts = text.split()
        link = parts[-1]
        name = " ".join(parts[:-1])
        await db.buttons.insert_one({'name': name, 'link': link})
        await update.message.reply_text("✅ Button Added.")
    except:
        await update.message.reply_text("Error.")
    return ConversationHandler.END

async def menu_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.buttons.delete_many({})
    await update.callback_query.message.reply_text("🗑️ All buttons cleared.")

async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END

# ================= MAIN =================
def main():
    if not MONGO_URL:
        print("❌ MONGO_URL Missing")
        return
        
    global db
    db = Database(MONGO_URL)
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # 1. Content Conversation
    conv_content = ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_add_content, pattern="menu_add_content")],
        states={
            ADD_CONTENT_CHOICE: [
                CallbackQueryHandler(start_single, pattern="add_single"),
                CallbackQueryHandler(start_batch, pattern="add_batch")
            ],
            GET_SINGLE_FILE: [MessageHandler(filters.ALL, save_single_file)],
            GET_BATCH_START: [MessageHandler(filters.FORWARDED, get_batch_start)],
            GET_BATCH_END: [MessageHandler(filters.FORWARDED, get_batch_end)]
        },
        fallbacks=[CallbackQueryHandler(menu_add_content, pattern="admin_back")]
    )
    
    # 2. Broadcast Conversation
    conv_cast = ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_broadcast, pattern="menu_broadcast")],
        states={
            BROADCAST_MSG: [MessageHandler(filters.ALL, get_broadcast_msg)],
            BROADCAST_BUTTONS: [MessageHandler(filters.TEXT, get_broadcast_buttons)],
            BROADCAST_SCHEDULE: [
                CallbackQueryHandler(execute_broadcast, pattern="^cast_"),
                MessageHandler(filters.TEXT, schedule_input)
            ]
        },
        fallbacks=[]
    )
    
    # 3. Welcome Setup
    conv_wel = ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_welcome, pattern="menu_welcome")],
        states={
            SET_WELCOME_MEDIA: [MessageHandler(filters.ALL, set_welcome_media)],
            SET_WELCOME_TEXT: [MessageHandler(filters.TEXT, set_welcome_text)]
        },
        fallbacks=[]
    )
    
    # 4. Add Channel
    conv_chan = ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_channel, pattern="menu_channel")],
        states={ADD_CHANNEL_LINK: [MessageHandler(filters.TEXT, add_channel_link)]},
        fallbacks=[]
    )
    
    # 5. Add Button
    conv_btn = ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_btn, pattern="menu_btn")],
        states={ADD_BUTTON_TEXT: [MessageHandler(filters.TEXT, add_btn_save)]},
        fallbacks=[]
    )

    # Register Handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    
    app.add_handler(conv_content)
    app.add_handler(conv_cast)
    app.add_handler(conv_wel)
    app.add_handler(conv_chan)
    app.add_handler(conv_btn)
    
    # Simple callbacks
    app.add_handler(CallbackQueryHandler(menu_clear, pattern="menu_clear"))
    app.add_handler(CallbackQueryHandler(start_support_session, pattern="start_support"))
    
    # Stats
    async def show_stats(update, context):
        t, a = await db.get_stats()
        await update.callback_query.edit_message_text(f"📊 <b>Stats</b>\nTotal: {t}\nActive (24h): {a}", parse_mode=ParseMode.HTML)
    app.add_handler(CallbackQueryHandler(show_stats, pattern="stats"))

    # Support Chat & Admin Reply
    app.add_handler(MessageHandler(filters.REPLY & filters.User(ADMIN_ID), admin_reply))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_support_msg))

    # Keep Alive Server
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(run_web_server())
    
    print("🚀 PRO Bot Started...")
    app.run_polling()

async def run_web_server():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

if __name__ == "__main__":
    main()
