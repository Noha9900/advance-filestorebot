import logging
import asyncio
import os
import uuid
import pytz
import certifi
import re
from datetime import datetime, timedelta

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ContextTypes, JobQueue, MessageHandler, filters, ConversationHandler
)

from motor.motor_asyncio import AsyncIOMotorClient
from aiohttp import web

# ================= CONFIGURATION =================
BOT_TOKEN = os.getenv("BOT_TOKEN") 
ADMIN_ID = int(os.getenv("ADMIN_ID"))
MONGO_URL = os.getenv("MONGO_URL")
IST = pytz.timezone('Asia/Kolkata')

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= DATABASE =================
class Database:
    def __init__(self, uri):
        self.client = AsyncIOMotorClient(uri, tlsCAFile=certifi.where())
        self.db = self.client['ultra_bot_db']
        
        self.users = self.db.users
        self.content = self.db.content
        self.settings = self.db.settings
        self.channels = self.db.channels
        self.support = self.db.support_sessions

    async def add_user(self, user_id, name, username):
        await self.users.update_one(
            {'_id': user_id}, 
            {'$set': {'name': name, 'username': username, 'last_active': datetime.now()}}, 
            upsert=True
        )
        
    async def update_admin_activity(self):
        await self.settings.update_one({'_id': 'admin_seen'}, {'$set': {'time': datetime.now()}}, upsert=True)

    async def is_admin_online(self):
        doc = await self.settings.find_one({'_id': 'admin_seen'})
        if not doc: return False
        # Admin considered online if active in last 10 mins
        return (datetime.now() - doc['time']).total_seconds() < 600

    async def get_all_users(self):
        cursor = self.users.find({})
        return [doc['_id'] async for doc in cursor]

    async def get_stats(self):
        return await self.users.count_documents({})

    async def save_content(self, uid, ctype, chat_id, msg_id, end_id=None, caption=""):
        await self.content.insert_one({
            '_id': uid, 'type': ctype, 'source_chat': chat_id, 
            'msg_id': msg_id, 'end_id': end_id, 'caption': caption,
            'time': datetime.now()
        })

    async def get_content(self, uid):
        return await self.content.find_one({'_id': uid})

    async def set_setting(self, key, value):
        await self.settings.update_one({'_id': key}, {'$set': {'val': value}}, upsert=True)

    async def get_setting(self, key):
        doc = await self.settings.find_one({'_id': key})
        return doc['val'] if doc else None

    async def add_force_channel(self, name, link):
        await self.channels.insert_one({'name': name, 'link': link})

    async def get_force_channels(self):
        cursor = self.channels.find({})
        return [{'name': c['name'], 'link': c['link']} async for c in cursor]
        
    async def clear_force_channels(self):
        await self.channels.delete_many({})

db = None

# ================= STATES =================
(
    CONTENT_INPUT,
    BROADCAST_PHOTO, BROADCAST_TEXT, BROADCAST_BUTTONS, BROADCAST_TIME,
    SET_WEL_MEDIA, SET_WEL_TEXT,
    ADD_UPD_LINK,
    ADD_FORCE_MEDIA, ADD_FORCE_TEXT, ADD_FORCE_LINKS
) = range(11)

# ================= HELPER =================
def parse_link(link):
    match = re.search(r't\.me/c/(\d+)/(\d+)', link)
    if match: return int("-100" + match.group(1)), int(match.group(2))
    match = re.search(r't\.me/([\w\d_]+)/(\d+)', link)
    if match: return "@" + match.group(1), int(match.group(2))
    return None, None

# ================= ADMIN PANEL =================
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await db.update_admin_activity() # Mark admin online
    
    kb = [
        [InlineKeyboardButton("➕ Add Content", callback_data="menu_add"), InlineKeyboardButton("📢 Broadcast", callback_data="menu_cast")],
        [InlineKeyboardButton("📝 Set Welcome", callback_data="menu_wel"), InlineKeyboardButton("🔔 Set Update Channel", callback_data="menu_upd")],
        [InlineKeyboardButton("🛡️ Set Force Join List", callback_data="menu_force"), InlineKeyboardButton("📊 Stats", callback_data="stats")]
    ]
    await update.message.reply_text("<b>🛡️ ADMIN PANEL</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

# ================= 1. ADD CONTENT =================
async def menu_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("<b>Send Content to Save:</b>\n1. Forward File\n2. Link\n3. Batch: `Batch Link1 Link2`", parse_mode=ParseMode.HTML)
    return CONTENT_INPUT

async def handle_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    txt = msg.text or msg.caption or ""
    uid = str(uuid.uuid4())[:8]

    # Batch
    if txt.lower().startswith("batch"):
        try:
            parts = txt.split()
            c1, m1 = parse_link(parts[1])
            c2, m2 = parse_link(parts[2])
            if c1 != c2: raise ValueError
            await db.save_content(uid, 'batch', c1, m1, end_id=m2)
            link = f"https://t.me/{context.bot.username}?start={uid}"
            await update.message.reply_text(f"✅ Batch Saved: <code>{link}</code>", parse_mode=ParseMode.HTML)
            return ConversationHandler.END
        except:
            await update.message.reply_text("Error. Format: `Batch Link1 Link2`")
            return ConversationHandler.END

    # Single Link
    c_id, m_id = parse_link(txt)
    if c_id:
        try:
            await context.bot.copy_message(ADMIN_ID, c_id, m_id) 
            await db.save_content(uid, 'single', c_id, m_id)
            link = f"https://t.me/{context.bot.username}?start={uid}"
            await update.message.reply_text(f"✅ Link Saved: <code>{link}</code>", parse_mode=ParseMode.HTML)
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        return ConversationHandler.END

    # File
    src = msg.chat_id
    await db.save_content(uid, 'single', src, msg.message_id, caption=msg.caption)
    link = f"https://t.me/{context.bot.username}?start={uid}"
    await update.message.reply_text(f"✅ File Saved: <code>{link}</code>", parse_mode=ParseMode.HTML)
    return ConversationHandler.END

# ================= 2. BROADCAST =================
async def menu_cast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("<b>Step 1:</b> Send Photo (or /skip).", parse_mode=ParseMode.HTML)
    return BROADCAST_PHOTO

async def cast_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo: context.user_data['bc_photo'] = update.message.photo[-1].file_id
    else: context.user_data['bc_photo'] = None
    await update.message.reply_text("<b>Step 2:</b> Send Text (or /skip).", parse_mode=ParseMode.HTML)
    return BROADCAST_TEXT

async def cast_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    context.user_data['bc_text'] = text if text != "/skip" else None
    await update.message.reply_text("<b>Step 3:</b> Buttons `Name - Link` (or /skip).", parse_mode=ParseMode.HTML)
    return BROADCAST_BUTTONS

async def cast_btns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    btns = []
    if text != "/skip":
        for line in text.split("\n"):
            if "-" in line:
                n, l = line.split("-", 1)
                btns.append([InlineKeyboardButton(n.strip(), url=l.strip())])
    context.user_data['bc_btns'] = btns
    kb = [[InlineKeyboardButton("Now", callback_data="now"), InlineKeyboardButton("Schedule", callback_data="sched")]]
    await update.message.reply_text("Now or Schedule?", reply_markup=InlineKeyboardMarkup(kb))
    return BROADCAST_TIME

async def cast_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "now":
        asyncio.create_task(run_broadcast(context))
        await query.edit_message_text("🚀 Broadcasting...")
        return ConversationHandler.END
    else:
        await query.edit_message_text("Time (IST): `YYYY-MM-DD HH:MM`", parse_mode=ParseMode.HTML)
        return BROADCAST_TIME

async def cast_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        t_str = update.message.text
        loc = IST.localize(datetime.strptime(t_str, "%Y-%m-%d %H:%M"))
        utc = loc.astimezone(pytz.utc)
        context.job_queue.run_once(run_scheduled, utc, data=context.user_data.copy())
        await update.message.reply_text(f"✅ Scheduled: {t_str}")
    except:
        await update.message.reply_text("❌ Error format.")
    return ConversationHandler.END

async def run_broadcast(context, data=None):
    if not data: data = context.user_data
    users = await db.get_all_users()
    photo = data.get('bc_photo')
    text = data.get('bc_text')
    btns = data.get('bc_btns')
    markup = InlineKeyboardMarkup(btns) if btns else None
    for uid in users:
        try:
            if photo: await context.bot.send_photo(uid, photo, caption=text, reply_markup=markup)
            elif text: await context.bot.send_message(uid, text, reply_markup=markup)
            await asyncio.sleep(0.05)
        except: pass

async def run_scheduled(context: ContextTypes.DEFAULT_TYPE):
    await run_broadcast(context, context.job.data)

# ================= 3. SETTINGS =================
async def menu_wel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("Send Welcome Photo.")
    return SET_WEL_MEDIA

async def save_wel_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['w_ph'] = update.message.photo[-1].file_id
    await update.message.reply_text("Send Welcome Text.")
    return SET_WEL_TEXT

async def save_wel_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.set_setting('w_txt', update.message.text)
    await db.set_setting('w_ph', context.user_data['w_ph'])
    await update.message.reply_text("✅ Saved.")
    return ConversationHandler.END

async def menu_upd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("Send Update Channel Link.")
    return ADD_UPD_LINK

async def save_upd_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.set_setting('upd_link', update.message.text)
    await update.message.reply_text("✅ Saved.")
    return ConversationHandler.END

async def menu_force(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("Send Force Join List Photo.")
    return ADD_FORCE_MEDIA

async def save_force_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['f_ph'] = update.message.photo[-1].file_id
    await update.message.reply_text("Send Force Join Text.")
    return ADD_FORCE_TEXT

async def save_force_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['f_txt'] = update.message.text
    await update.message.reply_text("Send Channels: `Name Link` (One per line).")
    return ADD_FORCE_LINKS

async def save_force_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.clear_force_channels()
    lines = update.message.text.split('\n')
    for line in lines:
        try:
            n, l = line.rsplit(maxsplit=1)
            await db.add_force_channel(n, l)
        except: pass
    await db.set_setting('f_ph', context.user_data['f_ph'])
    await db.set_setting('f_txt', context.user_data['f_txt'])
    await update.message.reply_text("✅ Force List Saved.")
    return ConversationHandler.END

# ================= USER FLOW & START =================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = update.effective_user
    await db.add_user(uid, user.first_name, user.username)
    
    # 1. CHECK IF DEEP LINK (PAYLOAD) EXISTS
    # If args exist (e.g. /start 12345), we want to deliver content
    if context.args:
        context.user_data['pending_content'] = context.args[0]
        # Check Force Join Status immediately
        await check_join_status(update, context, uid)
        return

    # 2. STANDARD WELCOME (No Payload)
    w_txt = await db.get_setting('w_txt') or "Welcome!"
    w_ph = await db.get_setting('w_ph')
    upd_link = await db.get_setting('upd_link')
    
    kb = []
    if upd_link: kb.append([InlineKeyboardButton("🔔 Update Channel", url=upd_link)])
    kb.append([InlineKeyboardButton("🆘 Support Chat", callback_data="start_support")])
    
    markup = InlineKeyboardMarkup(kb)
    if w_ph: await update.message.reply_photo(w_ph, caption=w_txt, reply_markup=markup)
    else: await update.message.reply_text(w_txt, reply_markup=markup)

async def check_join_status(update, context, uid):
    # This function handles the logic: Update Channel -> Force List -> Content
    
    # A. Check Update Channel
    upd_link = await db.get_setting('upd_link')
    # (Simplified: In a real bot, we would check membership API here using Chat ID)
    # If not joined, we would show a button.
    # For now, we assume if they clicked the link, they are ready or we show the force list.

    # B. Check Force Join List
    channels = await db.get_force_channels()
    if channels:
        f_txt = await db.get_setting('f_txt') or "Join these channels first:"
        f_ph = await db.get_setting('f_ph')
        
        kb = []
        for ch in channels:
            kb.append([InlineKeyboardButton(ch['name'], url=ch['link'])])
        kb.append([InlineKeyboardButton("✅ I have Joined", callback_data="verify_access")])
        
        if f_ph:
            if update.callback_query:
                await update.callback_query.message.reply_photo(f_ph, caption=f_txt, reply_markup=InlineKeyboardMarkup(kb))
            else:
                await update.message.reply_photo(f_ph, caption=f_txt, reply_markup=InlineKeyboardMarkup(kb))
        else:
            if update.callback_query:
                await update.callback_query.message.reply_text(f_txt, reply_markup=InlineKeyboardMarkup(kb))
            else:
                await update.message.reply_text(f_txt, reply_markup=InlineKeyboardMarkup(kb))
        return

    # C. If No Force Join, Grant Access Directly
    await deliver_content(update, context, uid)

async def verify_access_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.message.delete()
    await deliver_content(update, context, update.effective_user.id)

async def deliver_content(update, context, uid):
    link_id = context.user_data.get('pending_content')
    if not link_id: return

    data = await db.get_content(link_id)
    if not data:
        await context.bot.send_message(uid, "❌ Link Expired.")
        return

    msgs = []
    try:
        if data['type'] == 'single':
            m = await context.bot.copy_message(uid, data['source_chat'], data['msg_id'], caption=data.get('caption',""))
            msgs.append(m.message_id)
        elif data['type'] == 'batch':
            await context.bot.send_message(uid, "📂 Sending Batch...")
            for i in range(data['msg_id'], data['end_id'] + 1):
                try:
                    m = await context.bot.copy_message(uid, data['source_chat'], i)
                    msgs.append(m.message_id)
                    await asyncio.sleep(0.05)
                except: pass
        
        # 30 Min Auto-Delete
        info = await context.bot.send_message(uid, "⚠️ <b>Files auto-delete in 30 mins.</b>", parse_mode=ParseMode.HTML)
        msgs.append(info.message_id)
        
        for m_id in msgs:
            context.job_queue.run_once(del_msg, 1800, data={'c': uid, 'm': m_id})
            
    except Exception as e:
        await context.bot.send_message(uid, f"❌ Error: {e}")

# ================= SUPPORT SYSTEM =================
async def start_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await db.support.update_one({'_id': uid}, {'$set': {'active': True}}, upsert=True)
    
    is_online = await db.is_admin_online()
    status_emoji = "🟢" if is_online else "🔴"
    status_text = "Admin is Online" if is_online else "Admin is Offline (will reply ASAP)"
    
    msg = (f"<b>Bot connected with Admin</b>\n"
           f"{status_emoji} {status_text}\n\n"
           "Send your message now.")
           
    kb = [[InlineKeyboardButton("❌ End Chat", callback_data="end_chat_user")]]
    await update.callback_query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    
    # Admin Alert with User Info
    user = update.effective_user
    admin_text = (f"🚨 <b>Support Request</b>\n"
                  f"User: {user.first_name}\n"
                  f"ID: <code>{uid}</code>\n"
                  f"Username: @{user.username if user.username else 'None'}")
    
    kb_admin = [[InlineKeyboardButton("End Chat", callback_data=f"end_chat_admin_{uid}")]]
    await context.bot.send_message(ADMIN_ID, admin_text, reply_markup=InlineKeyboardMarkup(kb_admin), parse_mode=ParseMode.HTML)

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message
    
    # Update Admin Activity if Admin speaks
    if uid == ADMIN_ID:
        await db.update_admin_activity()
        if msg.reply_to_message and "ID:" in (msg.reply_to_message.text or ""):
            try:
                # Extract ID from the alert message text
                txt = msg.reply_to_message.text
                tgt = int(txt.split("ID: ")[1].split("\n")[0])
                await context.bot.copy_message(tgt, ADMIN_ID, msg.message_id)
                await msg.reply_text("Sent.")
            except: pass
        return

    # User Logic
    sess = await db.support.find_one({'_id': uid})
    if sess and sess.get('active'):
        kb = [[InlineKeyboardButton("End", callback_data=f"end_chat_admin_{uid}")]]
        await context.bot.copy_message(ADMIN_ID, uid, msg.message_id, reply_markup=InlineKeyboardMarkup(kb), caption=f"Message from {uid}")

async def end_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = update.callback_query.data
    if "user" in d:
        await db.support.update_one({'_id': update.effective_user.id}, {'$set': {'active': False}})
        await update.callback_query.edit_message_text("❌ Chat Ended.")
    else:
        tgt = int(d.split("_")[-1])
        await db.support.update_one({'_id': tgt}, {'$set': {'active': False}})
        await update.callback_query.edit_message_text("❌ Ended.")
        await context.bot.send_message(tgt, "❌ Admin ended the chat.")

async def del_msg(context: ContextTypes.DEFAULT_TYPE):
    try: await context.bot.delete_message(context.job.data['c'], context.job.data['m'])
    except: pass

async def stats_cb(u, c):
    await u.callback_query.answer(f"Users: {await db.get_stats()}", show_alert=True)

# ================= MAIN =================
def main():
    if not MONGO_URL: return
    global db
    db = Database(MONGO_URL)
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    
    # Support
    app.add_handler(CallbackQueryHandler(start_support, pattern="start_support"))
    app.add_handler(CallbackQueryHandler(end_chat, pattern="^end_chat"))
    
    # Verification & Content
    app.add_handler(CallbackQueryHandler(verify_access_cb, pattern="verify_access"))
    
    # Admin Menus
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_add, pattern="menu_add")],
        states={CONTENT_INPUT: [MessageHandler(filters.TEXT, handle_content)]}, fallbacks=[]))
        
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_cast, pattern="menu_cast")],
        states={
            BROADCAST_PHOTO: [MessageHandler(filters.ALL, cast_photo)],
            BROADCAST_TEXT: [MessageHandler(filters.TEXT, cast_text)],
            BROADCAST_BUTTONS: [MessageHandler(filters.TEXT, cast_btns)],
            BROADCAST_TIME: [CallbackQueryHandler(cast_decision), MessageHandler(filters.TEXT, cast_schedule)]
        }, fallbacks=[]))
        
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_wel, pattern="menu_wel")],
        states={SET_WEL_MEDIA: [MessageHandler(filters.PHOTO, save_wel_media)], SET_WEL_TEXT: [MessageHandler(filters.TEXT, save_wel_text)]}, fallbacks=[]))
        
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_upd, pattern="menu_upd")],
        states={ADD_UPD_LINK: [MessageHandler(filters.TEXT, save_upd_link)]}, fallbacks=[]))
        
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_force, pattern="menu_force")],
        states={
            ADD_FORCE_MEDIA: [MessageHandler(filters.PHOTO, save_force_media)],
            ADD_FORCE_TEXT: [MessageHandler(filters.TEXT, save_force_text)],
            ADD_FORCE_LINKS: [MessageHandler(filters.TEXT, save_force_links)]
        }, fallbacks=[]))
        
    app.add_handler(CallbackQueryHandler(stats_cb, pattern="stats"))
    app.add_handler(MessageHandler(filters.ALL, handle_msg))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(run_web_server())
    print("Bot Running...")
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
