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
        # Update admin activity if it is admin
        if user_id == ADMIN_ID:
            await self.settings.update_one({'_id': 'admin_status'}, {'$set': {'last_seen': datetime.now()}}, upsert=True)

    async def is_admin_online(self):
        doc = await self.settings.find_one({'_id': 'admin_status'})
        if not doc: return False
        # Online if active in last 10 mins
        return (datetime.now() - doc['last_seen']).total_seconds() < 600

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
    # Update admin last seen
    await db.add_user(ADMIN_ID, "Admin", "Admin")
    
    kb = [
        [InlineKeyboardButton("➕ Add Content", callback_data="menu_add"), InlineKeyboardButton("📢 Broadcast", callback_data="menu_cast")],
        [InlineKeyboardButton("📝 Set Welcome", callback_data="menu_wel"), InlineKeyboardButton("🔔 Update Channel", callback_data="menu_upd")],
        [InlineKeyboardButton("🛡️ Force Join List", callback_data="menu_force"), InlineKeyboardButton("📊 Stats", callback_data="stats")]
    ]
    await update.message.reply_text("<b>🛡️ ADMIN PANEL</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

# ================= 1. ADD CONTENT (INSTANT) =================
async def menu_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "<b>Send Content to Save:</b>\n\n"
        "1. Forward a File\n2. Send a Link (`https://t.me/...`)\n3. Batch: `Batch Link1 Link2`",
        parse_mode=ParseMode.HTML
    )
    return CONTENT_INPUT

async def handle_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    txt = msg.text or msg.caption or ""
    uid = str(uuid.uuid4())[:8]

    # A. Batch (Fast)
    if txt.lower().startswith("batch"):
        try:
            parts = txt.split()
            c1, m1 = parse_link(parts[1])
            c2, m2 = parse_link(parts[2])
            if c1 != c2: raise ValueError
            
            # Save directly to DB (Instant)
            await db.save_content(uid, 'batch', c1, m1, end_id=m2)
            
            link = f"https://t.me/{context.bot.username}?start={uid}"
            await update.message.reply_text(f"✅ <b>Batch Saved!</b>\n\n{link}", parse_mode=ParseMode.HTML)
            return ConversationHandler.END
        except:
            await update.message.reply_text("❌ Error. Format: `Batch Link1 Link2`")
            return ConversationHandler.END

    # B. Single Link
    c_id, m_id = parse_link(txt)
    if c_id:
        # Save directly (Verify on delivery to be faster here, or verify async)
        # We verify lightly to ensure ID is correct
        await db.save_content(uid, 'single', c_id, m_id)
        link = f"https://t.me/{context.bot.username}?start={uid}"
        await update.message.reply_text(f"✅ <b>Link Saved!</b>\n\n{link}", parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    # C. File Forward
    src = msg.chat_id
    # If forwarded from channel, try to keep source, else use current chat
    if msg.forward_from_chat: src = msg.forward_from_chat.id
        
    await db.save_content(uid, 'single', src, msg.message_id, caption=msg.caption)
    link = f"https://t.me/{context.bot.username}?start={uid}"
    await update.message.reply_text(f"✅ <b>File Saved!</b>\n\n{link}", parse_mode=ParseMode.HTML)
    return ConversationHandler.END

# ================= 2. SETTINGS (WITH DELETE) =================
# --- Update Channel ---
async def menu_upd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    curr = await db.get_setting('upd_link')
    text = f"Current Link: {curr}" if curr else "No link set."
    
    kb = []
    if curr: kb.append([InlineKeyboardButton("🗑️ Delete Link", callback_data="del_upd_link")])
    
    await update.callback_query.edit_message_text(f"{text}\n\nSend new <b>Update Channel Link</b> or Click Delete.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return ADD_UPD_LINK

async def save_upd_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.set_setting('upd_link', update.message.text)
    await update.message.reply_text("✅ Update Link Saved.")
    return ConversationHandler.END

async def del_upd_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.set_setting('upd_link', None)
    await update.callback_query.answer("🗑️ Link Deleted!")
    await update.callback_query.edit_message_text("✅ Link Deleted. Send new one or /cancel.")
    return ADD_UPD_LINK

# --- Force Join ---
async def menu_force(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🗑️ Delete All Links", callback_data="del_force_links")]]
    await update.callback_query.edit_message_text("Send <b>Force Join Photo</b> (or /cancel).", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return ADD_FORCE_MEDIA

async def del_force_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.clear_force_channels()
    await db.set_setting('f_ph', None)
    await db.set_setting('f_txt', None)
    await update.callback_query.answer("🗑️ All Force Links Deleted!")
    await update.callback_query.edit_message_text("✅ Cleared. Send Photo to start fresh.")
    return ADD_FORCE_MEDIA

async def save_force_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['f_ph'] = update.message.photo[-1].file_id
    await update.message.reply_text("Send <b>Force Join Text</b>.")
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
    await update.message.reply_text("✅ Force Join List Saved.")
    return ConversationHandler.END

# --- Welcome ---
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
    await update.message.reply_text("✅ Welcome Saved.")
    return ConversationHandler.END

# ================= BROADCAST =================
async def menu_cast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("<b>Step 1:</b> Send Photo (or /skip).", parse_mode=ParseMode.HTML)
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

# ================= USER FLOW & START =================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = update.effective_user
    await db.add_user(uid, user.first_name, user.username)
    
    # === PATH 1: USER CLICKED A FILE LINK ===
    if context.args:
        context.user_data['pending_content'] = context.args[0]
        # Skip standard welcome, go straight to join check
        await check_join_status(update, context, uid)
        return

    # === PATH 2: STANDARD START ===
    w_txt = await db.get_setting('w_txt') or "Welcome!"
    w_ph = await db.get_setting('w_ph')
    upd_link = await db.get_setting('upd_link')
    
    kb = []
    # Only two buttons as requested
    if upd_link: kb.append([InlineKeyboardButton("🔔 Update Channel", url=upd_link)])
    kb.append([InlineKeyboardButton("🆘 Support Chat", callback_data="start_support")])
    
    markup = InlineKeyboardMarkup(kb)
    if w_ph: await update.message.reply_photo(w_ph, caption=w_txt, reply_markup=markup)
    else: await update.message.reply_text(w_txt, reply_markup=markup)

async def check_join_status(update, context, uid):
    # Check if there are Force Join Channels
    channels = await db.get_force_channels()
    
    # If Channels exist, Show Join List
    if channels:
        f_txt = await db.get_setting('f_txt') or "Join these channels to access files:"
        f_ph = await db.get_setting('f_ph')
        
        kb = []
        for ch in channels:
            kb.append([InlineKeyboardButton(ch['name'], url=ch['link'])])
        
        # Verify Button
        kb.append([InlineKeyboardButton("✅ Verify & Get File", callback_data="verify_access")])
        
        # Show Force Join Msg
        if f_ph:
            msg = await context.bot.send_photo(uid, f_ph, caption=f_txt, reply_markup=InlineKeyboardMarkup(kb))
        else:
            msg = await context.bot.send_message(uid, f_txt, reply_markup=InlineKeyboardMarkup(kb))
            
        # Store message ID to delete later
        context.user_data['force_msg_id'] = msg.message_id
        return

    # If no channels, deliver content immediately
    await deliver_content(update, context, uid)

async def verify_access_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # User clicked "Verify"
    query = update.callback_query
    
    # Delete the Force Join Message (Vanish)
    try: await query.message.delete()
    except: pass
    
    # Deliver content
    await deliver_content(update, context, update.effective_user.id)

async def deliver_content(update, context, uid):
    link_id = context.user_data.get('pending_content')
    if not link_id: return

    data = await db.get_content(link_id)
    if not data:
        await context.bot.send_message(uid, "❌ Link Expired or Invalid.")
        return

    try:
        msgs = []
        if data['type'] == 'single':
            # Send file
            m = await context.bot.copy_message(uid, data['source_chat'], data['msg_id'], caption=data.get('caption',""))
            msgs.append(m.message_id)
            
        elif data['type'] == 'batch':
            await context.bot.send_message(uid, f"📂 <b>Batch Found!</b>\nSending {data['end_id'] - data['msg_id'] + 1} files...", parse_mode=ParseMode.HTML)
            # Loop
            for i in range(data['msg_id'], data['end_id'] + 1):
                try:
                    m = await context.bot.copy_message(uid, data['source_chat'], i)
                    msgs.append(m.message_id)
                    await asyncio.sleep(0.05) # Tiny delay to prevent flood
                except: pass
        
        # 30 Min Auto-Delete
        info = await context.bot.send_message(uid, "⚠️ <b>Files auto-delete in 30 mins.</b>", parse_mode=ParseMode.HTML)
        msgs.append(info.message_id)
        
        # Schedule deletion
        for m_id in msgs:
            context.job_queue.run_once(del_msg, 1800, data={'c': uid, 'm': m_id})
            
    except Exception as e:
        await context.bot.send_message(uid, f"❌ <b>Error:</b> Bot cannot access the file.\nMake sure Bot is Admin in the Source Channel.", parse_mode=ParseMode.HTML)

# ================= SUPPORT SYSTEM (CONTACT REQ) =================
async def start_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await db.support.update_one({'_id': uid}, {'$set': {'active': True}}, upsert=True)
    
    # Check Admin Status
    is_online = await db.is_admin_online()
    status = "🟢 <b>Admin is Online</b>" if is_online else "🔴 <b>Admin is Offline</b> (Will reply ASAP)"
    
    # Request Contact Button
    kb_contact = [[KeyboardButton("📱 Share Contact", request_contact=True)]]
    await context.bot.send_message(uid, "👇 <b>Please Share your Contact</b> so Admin can help you better.", reply_markup=ReplyKeyboardMarkup(kb_contact, one_time_keyboard=True), parse_mode=ParseMode.HTML)
    
    kb_end = [[InlineKeyboardButton("❌ End Chat", callback_data="end_chat_user")]]
    await update.callback_query.message.reply_text(
        f"✅ <b>Connected!</b>\n{status}\n\nSend your message now.", 
        reply_markup=InlineKeyboardMarkup(kb_end), parse_mode=ParseMode.HTML
    )

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    contact = update.message.contact
    
    # Send Contact to Admin
    await context.bot.send_message(
        ADMIN_ID, 
        f"👤 <b>User Info</b>\nName: {contact.first_name}\nID: <code>{uid}</code>\nPhone: <code>{contact.phone_number}</code>", 
        parse_mode=ParseMode.HTML
    )
    await update.message.reply_text("✅ Contact Sent.", reply_markup=ReplyKeyboardRemove())

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message
    
    # Admin Logic
    if uid == ADMIN_ID:
        await db.add_user(ADMIN_ID, "Admin", "Admin") # Update activity
        if msg.reply_to_message and "ID:" in (msg.reply_to_message.text or ""):
            try:
                # Parse ID from "User Info" message format or standard tag
                txt = msg.reply_to_message.text
                if "ID:" in txt:
                    tgt = int(txt.split("ID: ")[1].split("\n")[0])
                    await context.bot.copy_message(tgt, ADMIN_ID, msg.message_id)
                    await msg.reply_text("Sent.")
            except: pass
        return

    # User Logic
    sess = await db.support.find_one({'_id': uid})
    if sess and sess.get('active'):
        if msg.video: return await msg.reply_text("❌ No Videos Allowed.")
        
        # Forward to Admin with End Button
        kb = [[InlineKeyboardButton("End Chat", callback_data=f"end_chat_admin_{uid}")]]
        await context.bot.copy_message(
            ADMIN_ID, uid, msg.message_id, 
            reply_markup=InlineKeyboardMarkup(kb), 
            caption=f"Message from User (ID: {uid})"
        )

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

async def cancel_op(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END

# ================= MAIN =================
def main():
    if not MONGO_URL: return
    global db
    db = Database(MONGO_URL)
    app = Application.builder().token(BOT_TOKEN).build()
    
    # -- Conversations --
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
        entry_points=[CallbackQueryHandler(menu_upd, pattern="menu_upd"), CallbackQueryHandler(del_upd_link_handler, pattern="del_upd_link")],
        states={ADD_UPD_LINK: [MessageHandler(filters.TEXT, save_upd_link)]}, fallbacks=[]))
        
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_force, pattern="menu_force"), CallbackQueryHandler(del_force_handler, pattern="del_force_links")],
        states={
            ADD_FORCE_MEDIA: [MessageHandler(filters.PHOTO, save_force_media)],
            ADD_FORCE_TEXT: [MessageHandler(filters.TEXT, save_force_text)],
            ADD_FORCE_LINKS: [MessageHandler(filters.TEXT, save_force_links)]
        }, fallbacks=[]))

    # Commands & Callbacks
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CallbackQueryHandler(start_support, pattern="start_support"))
    app.add_handler(CallbackQueryHandler(end_chat, pattern="^end_chat"))
    app.add_handler(CallbackQueryHandler(verify_access_cb, pattern="verify_access"))
    app.add_handler(CallbackQueryHandler(stats_cb, pattern="stats"))
    
    # Messages
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
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
