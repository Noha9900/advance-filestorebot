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
        self.buttons = self.db.custom_buttons
        self.support = self.db.support_sessions

    async def add_user(self, user_id, name, username):
        await self.users.update_one({'_id': user_id}, {'$set': {'name': name, 'username': username}}, upsert=True)
        if user_id == ADMIN_ID:
            await self.settings.update_one({'_id': 'admin_stat'}, {'$set': {'seen': datetime.now()}}, upsert=True)

    async def get_all_users(self):
        cursor = self.users.find({})
        return [doc['_id'] async for doc in cursor]

    async def get_stats(self): return await self.users.count_documents({})

    async def save_content(self, uid, ctype, chat_id, msg_id, end_id=None, caption=""):
        # Save with UID as the _id for fast retrieval
        await self.content.insert_one({'_id': uid, 'type': ctype, 'src': chat_id, 'msg': msg_id, 'end': end_id, 'cap': caption})

    async def get_content(self, uid): return await self.content.find_one({'_id': uid})
    
    async def set_setting(self, key, val): await self.settings.update_one({'_id': key}, {'$set': {'v': val}}, upsert=True)
    async def get_setting(self, key): 
        d = await self.settings.find_one({'_id': key})
        return d['v'] if d else None

    async def add_force_channel(self, name, link): await self.channels.insert_one({'name': name, 'link': link})
    async def get_force_channels(self): return [{'name': c['name'], 'link': c['link']} async for c in self.channels.find({})]
    async def clear_force_channels(self): await self.channels.delete_many({})
    
    async def add_custom_btn(self, name, link): await self.buttons.insert_one({'name': name, 'link': link})
    async def get_custom_btns(self): return [{'name': b['name'], 'link': b['link']} async for b in self.buttons.find({})]
    async def clear_custom_btns(self): await self.buttons.delete_many({})

db = None

# ================= HELPER =================
def build_menu(buttons, n_cols=2, footer_buttons=None):
    menu = [buttons[i:i + n_cols] for i in range(0, len(buttons), n_cols)]
    if footer_buttons: menu.append(footer_buttons)
    return menu

def parse_link(link):
    m = re.search(r't\.me/c/(\d+)/(\d+)', link)
    if m: return int("-100" + m.group(1)), int(m.group(2))
    m = re.search(r't\.me/([\w\d_]+)/(\d+)', link)
    if m: return "@" + m.group(1), int(m.group(2))
    return None, None

def get_channel_id_from_link(link):
    if "t.me/+" in link or "joinchat" in link: return None 
    m = re.search(r't\.me/([\w\d_]+)', link)
    if m: return "@" + m.group(1)
    return None

# ================= STATES =================
(CONTENT_IN, BC_PHOTO, BC_TEXT, BC_BTNS, BC_TIME, WEL_MEDIA, WEL_TEXT, UPD_LINK, F_MEDIA, F_TEXT, F_LINKS, BTN_TXT) = range(12)

# ================= ADMIN =================
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    await db.add_user(ADMIN_ID, "Admin", "Admin")
    
    btns = [
        InlineKeyboardButton("➕ Content", callback_data="m_add"), InlineKeyboardButton("📢 Broadcast", callback_data="m_cast"),
        InlineKeyboardButton("📝 Welcome", callback_data="m_wel"), InlineKeyboardButton("🔔 Upd Channel", callback_data="m_upd"),
        InlineKeyboardButton("🛡️ Force Join", callback_data="m_force"), InlineKeyboardButton("🔘 Buttons", callback_data="m_btn"),
        InlineKeyboardButton("📊 Stats", callback_data="stats")
    ]
    txt = "<b>🛡️ ADMIN DASHBOARD</b>"
    kb = InlineKeyboardMarkup(build_menu(btns, 2))
    
    if update.callback_query: await update.callback_query.edit_message_text(txt, reply_markup=kb, parse_mode=ParseMode.HTML)
    else: await update.message.reply_text(txt, reply_markup=kb, parse_mode=ParseMode.HTML)
    return ConversationHandler.END

async def back_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await cmd_admin(update, context)

# --- ADD CONTENT ---
async def m_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🔙 Back", callback_data="back")]]
    await update.callback_query.edit_message_text("<b>Send File / Link / Batch:</b>\n<i>For files, simply forward them here.</i>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return CONTENT_IN

async def h_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, txt = update.message, update.message.text or ""
    uid = str(uuid.uuid4())[:8]
    kb = [[InlineKeyboardButton("🔙 Back", callback_data="back")]]

    # Batch
    if txt.lower().startswith("batch"):
        try:
            parts = txt.split()
            c1, m1 = parse_link(parts[1])
            c2, m2 = parse_link(parts[2])
            if c1 != c2: raise ValueError
            await db.save_content(uid, 'batch', c1, m1, end_id=m2)
            await update.message.reply_text(f"Batch: https://t.me/{context.bot.username}?start={uid}", reply_markup=InlineKeyboardMarkup(kb))
            return ConversationHandler.END
        except: return await update.message.reply_text("Error: `Batch Link1 Link2`", reply_markup=InlineKeyboardMarkup(kb))

    # Single
    c_id, m_id = parse_link(txt)
    if c_id:
        await db.save_content(uid, 'single', c_id, m_id)
    elif msg.chat_id:
        # Save File Forwarded to Bot
        src = msg.chat_id # Use Current Chat ID as source (Admin DM)
        await db.save_content(uid, 'single', src, msg.message_id, caption=msg.caption)
    
    await update.message.reply_text(f"Link: https://t.me/{context.bot.username}?start={uid}", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

# --- BROADCAST ---
async def m_cast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("Skip", callback_data="skip"), InlineKeyboardButton("🔙 Back", callback_data="back")]]
    await update.callback_query.edit_message_text("<b>Broadcast: Send Photo</b> (or Skip)", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return BC_PHOTO

async def h_bc_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['bc_photo'] = update.message.photo[-1].file_id if update.message and update.message.photo else None
    if update.callback_query: await update.callback_query.answer()
    
    kb = [[InlineKeyboardButton("Skip", callback_data="skip"), InlineKeyboardButton("🔙 Back", callback_data="back")]]
    txt = "<b>Broadcast: Send Text</b> (or Skip)"
    if update.message: await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    else: await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return BC_TEXT

async def h_bc_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['bc_text'] = update.message.text if update.message and update.message.text else None
    if update.callback_query: await update.callback_query.answer()
    
    kb = [[InlineKeyboardButton("🚀 Send Now", callback_data="now"), InlineKeyboardButton("🔙 Back", callback_data="back")]]
    txt = "<b>Ready to Send?</b>"
    if update.message: await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    else: await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return BC_TIME

async def h_bc_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "now":
        asyncio.create_task(run_broadcast(context))
        await query.edit_message_text("🚀 Broadcasting started...")
        return ConversationHandler.END

async def run_broadcast(context):
    data = context.user_data
    users = await db.get_all_users()
    photo = data.get('bc_photo')
    text = data.get('bc_text')
    
    for uid in users:
        try:
            if photo: await context.bot.send_photo(uid, photo, caption=text)
            elif text: await context.bot.send_message(uid, text)
            await asyncio.sleep(0.05)
        except: pass

# --- START FLOW ---
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await db.add_user(uid, update.effective_user.first_name, update.effective_user.username)
    
    if context.args: context.user_data['pl'] = context.args[0]
    
    # 1. Check Update Channel
    upd_link = await db.get_setting('upd_link')
    is_member = True
    
    if upd_link:
        if not context.user_data.get('upd_verified'):
            cid = get_channel_id_from_link(upd_link)
            if cid:
                try:
                    m = await context.bot.get_chat_member(cid, uid)
                    if m.status in ['left', 'kicked']: is_member = False
                except: is_member = False
            else: is_member = False

    if not is_member and upd_link:
        w_txt = await db.get_setting('w_txt') or "Welcome!"
        w_ph = await db.get_setting('w_ph')
        
        btns = [InlineKeyboardButton("🔔 Join Update Channel", url=upd_link), InlineKeyboardButton("✅ I Have Joined", callback_data="chk_upd")]
        cust = await db.get_custom_btns()
        c_btns = [InlineKeyboardButton(b['name'], url=b['link']) for b in cust]
        footer = [InlineKeyboardButton("🆘 Support Chat", callback_data="supp")]
        
        full_kb = build_menu(btns, 2) + build_menu(c_btns, 2) + [footer]
        
        if w_ph: await update.message.reply_photo(w_ph, caption=w_txt, reply_markup=InlineKeyboardMarkup(full_kb))
        else: await update.message.reply_text(w_txt, reply_markup=InlineKeyboardMarkup(full_kb))
        return

    # If already member, proceed
    await flow_step_2(update, context)

async def chk_upd_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['upd_verified'] = True
    try: await update.callback_query.message.delete()
    except: pass
    await flow_step_2(update, context)

async def flow_step_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    
    # 1. Show Support Msg (1 min vanish)
    kb = [[InlineKeyboardButton("🆘 Contact Admin Now", callback_data="supp")]]
    msg = await context.bot.send_message(uid, "ℹ️ <b>Contact admin now for any query.</b>\n(Disappears in 1 min)", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    context.job_queue.run_once(del_job, 60, data={'c': uid, 'm': msg.message_id})
    
    # 2. Check Force Join immediately
    await check_force_join(update, context)

async def check_force_join(update, context):
    uid = update.effective_user.id
    channels = await db.get_force_channels()
    
    # Show Force Join ONLY if content is pending or always if set
    if channels:
        f_txt = await db.get_setting('f_txt') or "Join these to access files:"
        f_ph = await db.get_setting('f_ph')
        
        btns = [InlineKeyboardButton(c['name'], url=c['link']) for c in channels]
        footer = [InlineKeyboardButton("✅ Verify & Get File", callback_data="chk_force")]
        
        markup = InlineKeyboardMarkup(build_menu(btns, 2, footer))
        
        if f_ph: await context.bot.send_photo(uid, f_ph, caption=f_txt, reply_markup=markup)
        else: await context.bot.send_message(uid, f_txt, reply_markup=markup)
        return

    # Deliver Content
    await deliver(update, context)

async def chk_force_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: await update.callback_query.message.delete()
    except: pass
    await deliver(update, context)

async def deliver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lid = context.user_data.get('pl')
    
    if not lid: return await context.bot.send_message(uid, "✅ <b>Welcome!</b> Use buttons to explore.", parse_mode=ParseMode.HTML)
    
    data = await db.get_content(lid)
    if not data: return await context.bot.send_message(uid, "❌ Link Expired/Invalid.")
    
    try:
        msgs = []
        if data['type'] == 'single':
            # Critical: Copy from Source Chat ID (Admin or Channel)
            m = await context.bot.copy_message(uid, data['src'], data['msg'], caption=data.get('cap', ""))
            msgs.append(m.message_id)
        elif data['type'] == 'batch':
            await context.bot.send_message(uid, "📂 <b>Sending Batch...</b>", parse_mode=ParseMode.HTML)
            for i in range(data['msg'], data['end'] + 1):
                try:
                    m = await context.bot.copy_message(uid, data['src'], i)
                    msgs.append(m.message_id)
                    await asyncio.sleep(0.05)
                except: pass
        
        info = await context.bot.send_message(uid, "⚠️ <b>Files auto-delete in 30 mins.</b>\nLink works permanently.", parse_mode=ParseMode.HTML)
        msgs.append(info.message_id)
        
        for m in msgs: context.job_queue.run_once(del_job, 1800, data={'c': uid, 'm': m})
    except Exception as e: 
        await context.bot.send_message(uid, f"❌ <b>Error:</b> Bot cannot access file. Ensure Bot is Admin in source or file exists.\nDebug: {e}", parse_mode=ParseMode.HTML)

# ================= SUPPORT =================
async def start_supp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await db.support.update_one({'_id': uid}, {'$set': {'on': True}}, upsert=True)
    
    kb = [[InlineKeyboardButton("❌ End Chat", callback_data="end_supp")]]
    await context.bot.send_message(uid, "✅ <b>Connected to Admin!</b>\nSend Text/Media.\nTap 'End Chat' to close.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    
    u = update.effective_user
    # Admin gets text message they can Reply to
    await context.bot.send_message(ADMIN_ID, f"🚨 <b>Support:</b> {u.first_name} (ID: {uid})", parse_mode=ParseMode.HTML)

async def handle_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message
    
    # Admin Reply Logic
    if uid == ADMIN_ID:
        # Check if Admin is replying to a message
        if msg.reply_to_message:
            original = msg.reply_to_message
            # Try to extract ID from text "Support: Name (ID: 12345)"
            target_id = None
            
            # Case 1: Text message reply
            if original.text and "ID:" in original.text:
                try: target_id = int(re.search(r'ID: (\d+)', original.text).group(1))
                except: pass
            
            # Case 2: Caption reply (if user sent photo)
            if not target_id and original.caption and "ID:" in original.caption:
                try: target_id = int(re.search(r'ID: (\d+)', original.caption).group(1))
                except: pass

            if target_id:
                try:
                    await context.bot.copy_message(target_id, ADMIN_ID, msg.message_id)
                    await msg.reply_text("✅ Sent.")
                except: await msg.reply_text("❌ Failed. User blocked bot?")
            else:
                await msg.reply_text("❌ Could not find User ID in message. Reply to the alert message.")
        return

    # User Logic
    s = await db.support.find_one({'_id': uid})
    if s and s.get('on'):
        # Forward user message to Admin with ID in caption/text so Admin can reply
        caption_txt = f"Message from User (ID: {uid})"
        if msg.text:
            await context.bot.send_message(ADMIN_ID, f"{msg.text}\n\n[ID: {uid}]")
        elif msg.photo or msg.video or msg.document:
            await context.bot.copy_message(ADMIN_ID, uid, msg.message_id, caption=f"[ID: {uid}]")

async def end_supp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await db.support.update_one({'_id': uid}, {'$set': {'on': False}})
    await update.callback_query.edit_message_text("❌ Chat Ended.")
    await context.bot.send_message(ADMIN_ID, f"User {uid} ended chat.")

async def del_job(context: ContextTypes.DEFAULT_TYPE):
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
    
    # Universal Fallback
    fallback = [CallbackQueryHandler(back_home, pattern="back"), CommandHandler("admin", cmd_admin), CommandHandler("start", cmd_start)]

    # Add Content
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(m_add, pattern="m_add")],
        states={CONTENT_IN: [MessageHandler(filters.TEXT & ~filters.COMMAND, h_content)]},
        fallbacks=fallback, allow_reentry=True
    ))

    # Broadcast
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(m_cast, pattern="m_cast")],
        states={
            BC_PHOTO: [MessageHandler(filters.ALL & ~filters.COMMAND, h_bc_photo), CallbackQueryHandler(h_bc_photo, pattern="skip")],
            BC_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, h_bc_text)],
            BC_TIME: [CallbackQueryHandler(h_bc_send)]
        }, fallbacks=fallback, allow_reentry=True
    ))

    # Support
    app.add_handler(CallbackQueryHandler(start_supp, pattern="supp"))
    app.add_handler(CallbackQueryHandler(end_supp, pattern="end_supp"))
    
    # Start Flows
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(chk_upd_cb, pattern="chk_upd"))
    app.add_handler(CallbackQueryHandler(chk_force_cb, pattern="chk_force"))
    
    # Admin
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CallbackQueryHandler(back_home, pattern="back"))
    app.add_handler(CallbackQueryHandler(stats_cb, pattern="stats"))
    
    # Chat Handler
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_chat))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(svr())
    app.run_polling()

async def svr():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

if __name__ == "__main__":
    main()
