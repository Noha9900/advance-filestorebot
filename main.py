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
        await self.users.update_one(
            {'_id': user_id}, 
            {'$set': {'name': name, 'username': username, 'last_active': datetime.now()}}, 
            upsert=True
        )
        if user_id == ADMIN_ID:
            await self.settings.update_one({'_id': 'admin_status'}, {'$set': {'last_seen': datetime.now()}}, upsert=True)

    async def is_admin_online(self):
        doc = await self.settings.find_one({'_id': 'admin_status'})
        if not doc: return False
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

    # --- Force Join ---
    async def add_force_channel(self, name, link):
        await self.channels.insert_one({'name': name, 'link': link})

    async def get_force_channels(self):
        cursor = self.channels.find({})
        return [{'name': c['name'], 'link': c['link']} async for c in cursor]
        
    async def clear_force_channels(self):
        await self.channels.delete_many({})

    # --- Custom Start Buttons ---
    async def add_custom_btn(self, name, link):
        await self.buttons.insert_one({'name': name, 'link': link})

    async def get_custom_btns(self):
        cursor = self.buttons.find({})
        return [{'name': b['name'], 'link': b['link']} async for b in cursor]

    async def clear_custom_btns(self):
        await self.buttons.delete_many({})

db = None

# ================= STATES =================
(
    CONTENT_INPUT,
    BROADCAST_PHOTO, BROADCAST_TEXT, BROADCAST_BUTTONS, BROADCAST_TIME,
    SET_WEL_MEDIA, SET_WEL_TEXT,
    ADD_UPD_LINK,
    ADD_FORCE_MEDIA, ADD_FORCE_TEXT, ADD_FORCE_LINKS,
    ADD_BTN_TXT 
) = range(12)

# ================= HELPER FUNCTIONS =================
def parse_link(link):
    match = re.search(r't\.me/c/(\d+)/(\d+)', link)
    if match: return int("-100" + match.group(1)), int(match.group(2))
    match = re.search(r't\.me/([\w\d_]+)/(\d+)', link)
    if match: return "@" + match.group(1), int(match.group(2))
    return None, None

def get_channel_id_from_link(link):
    if "t.me/+" in link or "joinchat" in link: return None
    match = re.search(r't\.me/([\w\d_]+)', link)
    if match: return "@" + match.group(1)
    return None

def build_menu(buttons, n_cols, header_buttons=None, footer_buttons=None):
    menu = [buttons[i:i + n_cols] for i in range(0, len(buttons), n_cols)]
    if header_buttons:
        menu.insert(0, header_buttons if isinstance(header_buttons, list) else [header_buttons])
    if footer_buttons:
        menu.append(footer_buttons if isinstance(footer_buttons, list) else [footer_buttons])
    return menu

# ================= ADMIN PANEL =================
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    await db.add_user(ADMIN_ID, "Admin", "Admin") 
    
    buttons = [
        InlineKeyboardButton("➕ Add Content", callback_data="menu_add"), InlineKeyboardButton("📢 Broadcast", callback_data="menu_cast"),
        InlineKeyboardButton("📝 Set Welcome", callback_data="menu_wel"), InlineKeyboardButton("🔔 Update Channel", callback_data="menu_upd"),
        InlineKeyboardButton("🛡️ Force Join List", callback_data="menu_force"), InlineKeyboardButton("🔘 Custom Buttons", callback_data="menu_btn"),
        InlineKeyboardButton("📊 Stats", callback_data="stats")
    ]
    
    msg_text = "<b>🛡️ ADMIN PANEL</b>\nSelect an option:"
    reply_markup = InlineKeyboardMarkup(build_menu(buttons, n_cols=2))
    
    if update.callback_query:
        await update.callback_query.edit_message_text(msg_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(msg_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    
    return ConversationHandler.END

# ================= 1. ADD CONTENT (FIXED) =================
async def menu_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]]
    await query.edit_message_text(
        "<b>➕ ADD CONTENT</b>\n\n"
        "1. <b>Forward File</b> (Recommended)\n"
        "2. <b>Send Link</b> (Bot must be admin in channel)\n"
        "3. <b>Batch:</b> `Batch Link1 Link2`",
        parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb)
    )
    return CONTENT_INPUT

async def handle_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    txt = msg.text or msg.caption or ""
    uid = str(uuid.uuid4())[:8]
    kb = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]]

    # A. BATCH
    if txt.lower().startswith("batch"):
        try:
            parts = txt.split()
            c1, m1 = parse_link(parts[1])
            c2, m2 = parse_link(parts[2])
            if c1 != c2: raise ValueError
            await db.save_content(uid, 'batch', c1, m1, end_id=m2)
            link = f"https://t.me/{context.bot.username}?start={uid}"
            await update.message.reply_text(f"✅ <b>Batch Saved!</b>\n\n{link}", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
            return ConversationHandler.END
        except:
            await update.message.reply_text("❌ Error. Format: `Batch Link1 Link2`", reply_markup=InlineKeyboardMarkup(kb))
            return CONTENT_INPUT

    # B. SINGLE LINK
    c_id, m_id = parse_link(txt)
    if c_id:
        await db.save_content(uid, 'single', c_id, m_id)
        link = f"https://t.me/{context.bot.username}?start={uid}"
        await update.message.reply_text(f"✅ <b>Link Saved!</b>\n\n{link}", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        return ConversationHandler.END

    # C. FILE FORWARD (FIXED: Uses Bot's Chat Copy)
    # We use msg.chat_id to ensure we reference the file currently accessible to the bot.
    # We ignore forward_from_chat to prevent permission errors.
    src = msg.chat_id 
    await db.save_content(uid, 'single', src, msg.message_id, caption=msg.caption)
    link = f"https://t.me/{context.bot.username}?start={uid}"
    await update.message.reply_text(f"✅ <b>File Saved!</b>\n\n{link}", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

# ================= 2. BROADCAST =================
async def menu_cast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("Skip", callback_data="skip"), InlineKeyboardButton("🔙 Back", callback_data="back_home")]]
    await update.callback_query.edit_message_text("<b>📢 Step 1: Send Photo</b> (or Click Skip).", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return BROADCAST_PHOTO

async def cast_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query and update.callback_query.data == "skip":
        context.user_data['bc_photo'] = None
        await update.callback_query.answer()
    elif update.message and update.message.photo:
        context.user_data['bc_photo'] = update.message.photo[-1].file_id
    else:
        context.user_data['bc_photo'] = None

    kb = [[InlineKeyboardButton("Skip", callback_data="skip"), InlineKeyboardButton("🔙 Back", callback_data="back_photo")]]
    msg = await (update.message.reply_text if update.message else update.callback_query.edit_message_text)
    await msg("<b>📢 Step 2: Send Text</b> (or Click Skip).", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return BROADCAST_TEXT

async def back_to_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("Skip", callback_data="skip"), InlineKeyboardButton("🔙 Back", callback_data="back_home")]]
    await update.callback_query.edit_message_text("<b>📢 Step 1: Send Photo</b> (or Click Skip).", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return BROADCAST_PHOTO

async def cast_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query and update.callback_query.data == "skip":
        context.user_data['bc_text'] = None
        await update.callback_query.answer()
    elif update.message and update.message.text:
        context.user_data['bc_text'] = update.message.text
    
    kb = [[InlineKeyboardButton("Skip", callback_data="skip"), InlineKeyboardButton("🔙 Back", callback_data="back_text")]]
    msg = await (update.message.reply_text if update.message else update.callback_query.edit_message_text)
    await msg("<b>📢 Step 3: Send Buttons</b>\nFormat: `Name - Link`\nEx: `Join - https://t.me/join`", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return BROADCAST_BUTTONS

async def back_to_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("Skip", callback_data="skip"), InlineKeyboardButton("🔙 Back", callback_data="back_photo")]]
    await update.callback_query.edit_message_text("<b>📢 Step 2: Send Text</b> (or Click Skip).", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return BROADCAST_TEXT

async def cast_btns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    layout = []
    if update.message and update.message.text:
        try:
            lines = update.message.text.split('\n')
            for line in lines:
                row = []
                btns = line.split('|')
                for btn in btns:
                    name, link = btn.split('-', 1)
                    row.append(InlineKeyboardButton(name.strip(), url=link.strip()))
                layout.append(row)
        except:
            await update.message.reply_text("❌ Format Error. Use `Name - Link`")
            return BROADCAST_BUTTONS
    
    context.user_data['bc_btns'] = layout
    kb = [
        [InlineKeyboardButton("🚀 Send Now", callback_data="now"), InlineKeyboardButton("⏰ Schedule", callback_data="sched")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_btns")]
    ]
    msg = await (update.message.reply_text if update.message else update.callback_query.edit_message_text)
    await msg("<b>✅ Ready! Send Now or Schedule?</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return BROADCAST_TIME

async def back_to_btns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("Skip", callback_data="skip"), InlineKeyboardButton("🔙 Back", callback_data="back_text")]]
    await update.callback_query.edit_message_text("<b>📢 Step 3: Send Buttons</b>\nFormat: `Name - Link`", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return BROADCAST_BUTTONS

async def cast_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "now":
        asyncio.create_task(run_broadcast(context))
        await query.edit_message_text("🚀 Broadcasting started...")
        return ConversationHandler.END
    else:
        await query.edit_message_text("Send Time (IST): `YYYY-MM-DD HH:MM`", parse_mode=ParseMode.HTML)
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
# --- Custom Buttons ---
async def menu_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🗑️ Delete All Buttons", callback_data="clr_btns")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]
    ]
    await update.callback_query.edit_message_text("<b>🔘 Add Custom Buttons</b>\n\nSend: `Name - Link`", reply_markup=InlineKeyboardMarkup(build_menu(kb[0] + kb[1], 2)), parse_mode=ParseMode.HTML)
    return ADD_BTN_TXT

async def save_custom_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]]
    try:
        name, link = update.message.text.split('-', 1)
        await db.add_custom_btn(name.strip(), link.strip())
        await update.message.reply_text("✅ Button Added.", reply_markup=InlineKeyboardMarkup(kb))
    except:
        await update.message.reply_text("❌ Error. Use `Name - Link`", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

async def clear_custom_btns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.clear_custom_btns()
    kb = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]]
    await update.callback_query.edit_message_text("🗑️ All Custom Buttons Deleted.", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

# --- Update Channel ---
async def menu_upd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    curr = await db.get_setting('upd_link')
    text = f"Current: {curr}" if curr else "No link set."
    kb = [[InlineKeyboardButton("🗑️ Delete Link", callback_data="del_upd_link")], [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]]
    await update.callback_query.edit_message_text(f"{text}\n\nSend new <b>Update Channel Link</b>.", reply_markup=InlineKeyboardMarkup(build_menu(kb[0] + kb[1], 2)), parse_mode=ParseMode.HTML)
    return ADD_UPD_LINK

async def save_upd_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.set_setting('upd_link', update.message.text)
    kb = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]]
    await update.message.reply_text("✅ Update Link Saved.", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

async def del_upd_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.set_setting('upd_link', None)
    kb = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]]
    await update.callback_query.edit_message_text("🗑️ Link Deleted.", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

# --- Force Join ---
async def menu_force(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🗑️ Delete All Links", callback_data="del_force_links")], [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]]
    await update.callback_query.edit_message_text("Send <b>Force Join Photo</b> (or /cancel).", reply_markup=InlineKeyboardMarkup(build_menu(kb[0] + kb[1], 2)), parse_mode=ParseMode.HTML)
    return ADD_FORCE_MEDIA

async def del_force_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.clear_force_channels()
    await db.set_setting('f_ph', None)
    await db.set_setting('f_txt', None)
    kb = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]]
    await update.callback_query.edit_message_text("🗑️ All Force Links Deleted.", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

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
    kb = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]]
    await update.message.reply_text("✅ Force Join List Saved.", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

# --- Welcome ---
async def menu_wel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🗑️ Delete Welcome", callback_data="del_wel")], [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]]
    await update.callback_query.edit_message_text("Send <b>Welcome Photo</b>.", reply_markup=InlineKeyboardMarkup(build_menu(kb[0] + kb[1], 2)), parse_mode=ParseMode.HTML)
    return SET_WEL_MEDIA

async def del_wel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.set_setting('w_txt', None)
    await db.set_setting('w_ph', None)
    kb = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]]
    await update.callback_query.edit_message_text("🗑️ Welcome Deleted.", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

async def save_wel_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['w_ph'] = update.message.photo[-1].file_id
    await update.message.reply_text("Send <b>Welcome Text</b>.")
    return SET_WEL_TEXT

async def save_wel_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.set_setting('w_txt', update.message.text)
    await db.set_setting('w_ph', context.user_data['w_ph'])
    kb = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]]
    await update.message.reply_text("✅ Welcome Saved.", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

# ================= START & FLOW (FIXED) =================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = update.effective_user
    await db.add_user(uid, user.first_name, user.username)
    
    # Check if payload (Deep Link)
    if context.args:
        context.user_data['pending_content'] = context.args[0]

    # --- CHECK UPDATE CHANNEL STATUS ---
    upd_link = await db.get_setting('upd_link')
    is_member = True
    
    if upd_link:
        channel_id = get_channel_id_from_link(upd_link)
        if channel_id:
            try:
                member = await context.bot.get_chat_member(chat_id=channel_id, user_id=uid)
                if member.status in ['left', 'kicked']: is_member = False
            except: is_member = False
        else:
            if not context.user_data.get('upd_verified'): is_member = False

    # Get Welcome Data
    w_txt = await db.get_setting('w_txt') or "Welcome!"
    w_ph = await db.get_setting('w_ph')
    
    # --- BUILD BUTTONS ---
    buttons = []
    
    # 1. Update Channel (If not member)
    if not is_member and upd_link:
        buttons.append(InlineKeyboardButton("🔔 Join Update Channel", url=upd_link))
        buttons.append(InlineKeyboardButton("✅ I Have Joined", callback_data="verify_upd"))
    
    # 2. Custom Buttons
    custom_btns = await db.get_custom_btns()
    for btn in custom_btns:
        buttons.append(InlineKeyboardButton(btn['name'], url=btn['link']))
    
    # 3. Support (Footer)
    footer = [InlineKeyboardButton("🆘 Support Chat", callback_data="start_support")]
    
    reply_markup = InlineKeyboardMarkup(build_menu(buttons, n_cols=2, footer_buttons=footer))
    
    # Send Welcome
    msg = None
    if w_ph: msg = await update.message.reply_photo(w_ph, caption=w_txt, reply_markup=reply_markup)
    else: msg = await update.message.reply_text(w_txt, reply_markup=reply_markup)
        
    context.user_data['welcome_msg_id'] = msg.message_id
    return ConversationHandler.END

async def verify_upd_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    
    try: await query.message.delete()
    except: pass
    
    context.user_data['upd_verified'] = True
    
    # SHOW SUPPORT MSG (1 min)
    kb = [[InlineKeyboardButton("🆘 Contact Admin Now", callback_data="start_support")]]
    msg = await context.bot.send_message(uid, "ℹ️ <b>Contact admin now for any query.</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    context.job_queue.run_once(del_msg, 60, data={'c': uid, 'm': msg.message_id})
    
    # Proceed to Force Join
    await check_join_status(update, context, uid)

async def check_join_status(update, context, uid):
    # Only if deep link exists
    if not context.user_data.get('pending_content'): return

    channels = await db.get_force_channels()
    if channels:
        f_txt = await db.get_setting('f_txt') or "Join these channels to access files:"
        f_ph = await db.get_setting('f_ph')
        
        buttons = []
        for ch in channels:
            buttons.append(InlineKeyboardButton(ch['name'], url=ch['link']))
        
        footer = [InlineKeyboardButton("✅ Verify & Get File", callback_data="verify_access")]
        reply_markup = InlineKeyboardMarkup(build_menu(buttons, 2, footer_buttons=footer))
        
        if f_ph: await context.bot.send_photo(uid, f_ph, caption=f_txt, reply_markup=reply_markup)
        else: await context.bot.send_message(uid, f_txt, reply_markup=reply_markup)
        return

    await deliver_content(update, context, uid)

async def verify_access_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.message.delete()
    except: pass
    await deliver_content(update, context, update.effective_user.id)

async def deliver_content(update, context, uid):
    link_id = context.user_data.get('pending_content')
    if not link_id: return

    data = await db.get_content(link_id)
    if not data: return await context.bot.send_message(uid, "❌ Link Expired.")

    try:
        msgs = []
        if data['type'] == 'single':
            m = await context.bot.copy_message(uid, data['source_chat'], data['msg_id'], caption=data.get('caption',""))
            msgs.append(m.message_id)
        elif data['type'] == 'batch':
            await context.bot.send_message(uid, f"📂 <b>Batch Found!</b>\nSending {data['end_id'] - data['msg_id'] + 1} files...", parse_mode=ParseMode.HTML)
            for i in range(data['msg_id'], data['end_id'] + 1):
                try:
                    m = await context.bot.copy_message(uid, data['source_chat'], i)
                    msgs.append(m.message_id)
                    await asyncio.sleep(0.05)
                except: pass
        
        info = await context.bot.send_message(uid, "⚠️ <b>Files disappear in 30 mins.</b>\nLink works permanently.", parse_mode=ParseMode.HTML)
        msgs.append(info.message_id)
        
        for m_id in msgs: context.job_queue.run_once(del_msg, 1800, data={'c': uid, 'm': m_id})
    except: await context.bot.send_message(uid, f"❌ <b>Error:</b> Bot cannot access file. Ensure Bot is Admin in Channel.", parse_mode=ParseMode.HTML)

# ================= SUPPORT =================
async def start_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await db.support.update_one({'_id': uid}, {'$set': {'active': True}}, upsert=True)
    is_online = await db.is_admin_online()
    status = "🟢 <b>Admin is Online</b>" if is_online else "🔴 <b>Admin is Offline</b>"
    
    kb_contact = [[KeyboardButton("📱 Share Contact", request_contact=True)]]
    await context.bot.send_message(uid, "👇 <b>Share Contact</b> for better support.", reply_markup=ReplyKeyboardMarkup(kb_contact, one_time_keyboard=True), parse_mode=ParseMode.HTML)
    
    kb_end = [[InlineKeyboardButton("❌ End Chat", callback_data="end_chat_user")]]
    await update.callback_query.message.reply_text(f"✅ <b>Connected!</b>\n{status}", reply_markup=InlineKeyboardMarkup(kb_end), parse_mode=ParseMode.HTML)

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    contact = update.message.contact
    await context.bot.send_message(ADMIN_ID, f"👤 <b>User Info</b>\nName: {contact.first_name}\nID: <code>{uid}</code>\nPhone: <code>{contact.phone_number}</code>", parse_mode=ParseMode.HTML)
    await update.message.reply_text("✅ Contact Sent.", reply_markup=ReplyKeyboardRemove())

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message
    if uid == ADMIN_ID:
        await db.add_user(ADMIN_ID, "Admin", "Admin") 
        if msg.reply_to_message and "ID:" in (msg.reply_to_message.text or ""):
            try:
                txt = msg.reply_to_message.text
                if "ID:" in txt:
                    tgt = int(txt.split("ID: ")[1].split("\n")[0])
                    await context.bot.copy_message(tgt, ADMIN_ID, msg.message_id)
                    await msg.reply_text("Sent.")
            except: pass
        return

    sess = await db.support.find_one({'_id': uid})
    if sess and sess.get('active'):
        kb = [[InlineKeyboardButton("End Chat", callback_data=f"end_chat_admin_{uid}")]]
        await context.bot.copy_message(ADMIN_ID, uid, msg.message_id, reply_markup=InlineKeyboardMarkup(kb), caption=f"Message from User (ID: {uid})")

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
    
    fallback = [CommandHandler("start", cmd_start), CommandHandler("admin", cmd_admin), CallbackQueryHandler(cmd_admin, pattern="back_home")]

    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(menu_add, pattern="menu_add")], states={CONTENT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_content)]}, fallbacks=fallback, allow_reentry=True))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(menu_cast, pattern="menu_cast")], states={
        BROADCAST_PHOTO: [MessageHandler(filters.ALL & ~filters.COMMAND, cast_photo), CallbackQueryHandler(cast_photo, pattern="skip")],
        BROADCAST_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cast_text), CallbackQueryHandler(cast_text, pattern="skip"), CallbackQueryHandler(back_to_photo, pattern="back_photo")],
        BROADCAST_BUTTONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, cast_btns), CallbackQueryHandler(cast_btns, pattern="skip"), CallbackQueryHandler(back_to_text, pattern="back_text")],
        BROADCAST_TIME: [CallbackQueryHandler(cast_decision), MessageHandler(filters.TEXT & ~filters.COMMAND, cast_schedule), CallbackQueryHandler(back_to_btns, pattern="back_btns")]}, fallbacks=fallback, allow_reentry=True))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(menu_wel, pattern="menu_wel")], states={SET_WEL_MEDIA: [MessageHandler(filters.PHOTO, save_wel_media), CallbackQueryHandler(del_wel_handler, pattern="del_wel")], SET_WEL_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_wel_text)]}, fallbacks=fallback, allow_reentry=True))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(menu_upd, pattern="menu_upd"), CallbackQueryHandler(del_upd_link_handler, pattern="del_upd_link")], states={ADD_UPD_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_upd_link)]}, fallbacks=fallback, allow_reentry=True))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(menu_force, pattern="menu_force"), CallbackQueryHandler(del_force_handler, pattern="del_force_links")], states={ADD_FORCE_MEDIA: [MessageHandler(filters.PHOTO, save_force_media)], ADD_FORCE_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_force_text)], ADD_FORCE_LINKS: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_force_links)]}, fallbacks=fallback, allow_reentry=True))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(menu_btn, pattern="menu_btn"), CallbackQueryHandler(clear_custom_btns, pattern="clr_btns")], states={ADD_BTN_TXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_custom_btn)]}, fallbacks=fallback, allow_reentry=True))

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CallbackQueryHandler(cmd_admin, pattern="back_home"))
    app.add_handler(CallbackQueryHandler(start_support, pattern="start_support"))
    app.add_handler(CallbackQueryHandler(end_chat, pattern="^end_chat"))
    app.add_handler(CallbackQueryHandler(verify_upd_cb, pattern="verify_upd"))
    app.add_handler(CallbackQueryHandler(verify_access_cb, pattern="verify_access"))
    app.add_handler(CallbackQueryHandler(stats_cb, pattern="stats"))
    
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
