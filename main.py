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

    async def is_admin_online(self):
        doc = await self.settings.find_one({'_id': 'admin_stat'})
        if not doc: return False
        return (datetime.now() - doc['seen']).total_seconds() < 600

    async def get_all_users(self):
        cursor = self.users.find({})
        return [doc['_id'] async for doc in cursor]

    async def get_stats(self): return await self.users.count_documents({})

    async def save_content(self, uid, ctype, chat_id, msg_id, end_id=None, caption="", file_id=None, file_type=None):
        await self.content.insert_one({
            '_id': uid, 'type': ctype, 'src': chat_id, 'msg': msg_id, 'end': end_id, 
            'cap': caption, 'fid': file_id, 'ftype': file_type,
            'time': datetime.now()
        })

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
(
    CONTENT_INPUT, 
    BC_PHOTO, BC_TEXT, BC_TIME, 
    WEL_MEDIA, WEL_TEXT, 
    UPD_LINK, 
    F_MEDIA, F_TEXT, F_LINKS, 
    BTN_TXT
) = range(11) # Fixed: range(11) because there are 11 variables above

# ================= ADMIN =================
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    await db.add_user(ADMIN_ID, "Admin", "Admin")
    
    btns = [
        InlineKeyboardButton("➕ Add Content", callback_data="menu_add"), InlineKeyboardButton("📢 Broadcast", callback_data="menu_cast"),
        InlineKeyboardButton("📝 Welcome", callback_data="menu_wel"), InlineKeyboardButton("🔔 Upd Channel", callback_data="menu_upd"),
        InlineKeyboardButton("🛡️ Force Join", callback_data="menu_force"), InlineKeyboardButton("🔘 Buttons", callback_data="menu_btn"),
        InlineKeyboardButton("📊 Stats", callback_data="stats")
    ]
    txt = "<b>🛡️ ADMIN DASHBOARD</b>\nSelect an option:"
    kb = InlineKeyboardMarkup(build_menu(btns, 2))
    
    if update.callback_query: await update.callback_query.edit_message_text(txt, reply_markup=kb, parse_mode=ParseMode.HTML)
    else: await update.message.reply_text(txt, reply_markup=kb, parse_mode=ParseMode.HTML)
    return ConversationHandler.END

async def back_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await cmd_admin(update, context)

# --- ADD CONTENT ---
async def menu_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🔙 Back", callback_data="back")]]
    await update.callback_query.edit_message_text(
        "<b>Send File / Link / Batch:</b>\n\n"
        "1. <b>Forward File</b> (Recommended)\n"
        "2. <b>Single Link:</b> `https://t.me/c/xxx/100`\n"
        "3. <b>Batch:</b> `Link1 Link2`\n\n"
        "⚠️ <i>For links, I must be Admin in that channel!</i>",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
    )
    return CONTENT_INPUT

async def handle_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, txt = update.message, update.message.text or ""
    uid = str(uuid.uuid4())[:8]
    kb = [[InlineKeyboardButton("🔙 Back", callback_data="back")]]

    # A. LINKS
    links = re.findall(r'(https?://t\.me/[^\s]+)', txt)
    if links:
        if len(links) >= 2:
            c1, m1 = parse_link(links[0])
            c2, m2 = parse_link(links[1])
            if not c1 or not c2 or c1 != c2:
                await update.message.reply_text("❌ Error: Links must be from the same channel.", reply_markup=InlineKeyboardMarkup(kb))
                return CONTENT_INPUT
            
            try: await context.bot.copy_message(ADMIN_ID, c1, m1)
            except Exception as e:
                await update.message.reply_text(f"❌ <b>Access Denied!</b>\nI cannot access the Start Message.\nMake sure I am Admin in that channel.\nError: {e}", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
                return CONTENT_INPUT

            await db.save_content(uid, 'batch', c1, m1, end_id=m2)
            await update.message.reply_text(f"✅ <b>Batch Saved!</b> ({m2-m1+1} files)\n\nhttps://t.me/{context.bot.username}?start={uid}", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
            return ConversationHandler.END

        elif len(links) == 1:
            c_id, m_id = parse_link(links[0])
            if not c_id:
                await update.message.reply_text("❌ Invalid Link Format.", reply_markup=InlineKeyboardMarkup(kb))
                return CONTENT_INPUT
            
            try: await context.bot.copy_message(ADMIN_ID, c_id, m_id)
            except Exception as e:
                await update.message.reply_text(f"❌ <b>Access Denied!</b>\nBot must be Admin.\nError: {e}", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
                return CONTENT_INPUT

            await db.save_content(uid, 'single', c_id, m_id)
            await update.message.reply_text(f"✅ <b>Link Saved!</b>\n\nhttps://t.me/{context.bot.username}?start={uid}", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
            return ConversationHandler.END

    # B. FILE FORWARD
    if msg.forward_from_chat or msg.chat_id:
        file_id, file_type = None, None
        if msg.document: file_id, file_type = msg.document.file_id, 'doc'
        elif msg.video: file_id, file_type = msg.video.file_id, 'video'
        elif msg.photo: file_id, file_type = msg.photo[-1].file_id, 'photo'
        elif msg.audio: file_id, file_type = msg.audio.file_id, 'audio'
        
        if file_id or msg.chat_id:
            src = msg.chat_id 
            await db.save_content(uid, 'single', src, msg.message_id, caption=msg.caption, file_id=file_id, file_type=file_type)
            await update.message.reply_text(f"✅ <b>File Saved!</b>\n\nhttps://t.me/{context.bot.username}?start={uid}", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
            return ConversationHandler.END
    
    await update.message.reply_text("❌ Unknown format. Send Link or File.", reply_markup=InlineKeyboardMarkup(kb))
    return CONTENT_INPUT

# --- BROADCAST ---
async def menu_cast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("Skip", callback_data="skip"), InlineKeyboardButton("🔙 Back", callback_data="back")]]
    await update.callback_query.edit_message_text("<b>Broadcast: Send Photo</b> (or Skip)", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return BC_PHOTO

async def cast_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query and update.callback_query.data == "skip":
        context.user_data['bc_photo'] = None
        await update.callback_query.answer()
    else:
        context.user_data['bc_photo'] = update.message.photo[-1].file_id if update.message and update.message.photo else None
    
    kb = [[InlineKeyboardButton("Skip", callback_data="skip"), InlineKeyboardButton("🔙 Back", callback_data="back")]]
    txt = "<b>Broadcast: Send Text</b> (or Skip)"
    if update.message: await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    else: await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return BC_TEXT

async def back_to_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("Skip", callback_data="skip"), InlineKeyboardButton("🔙 Back", callback_data="back")]]
    await update.callback_query.edit_message_text("<b>Broadcast: Send Photo</b> (or Skip)", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return BC_PHOTO

async def cast_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query and update.callback_query.data == "skip":
        context.user_data['bc_text'] = None
        await update.callback_query.answer()
    else:
        context.user_data['bc_text'] = update.message.text
    
    kb = [[InlineKeyboardButton("🚀 Send Now", callback_data="now"), InlineKeyboardButton("🔙 Back", callback_data="back")]]
    txt = "<b>Ready to Send?</b>"
    if update.message: await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    else: await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return BC_TIME

async def cast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

# --- FORCE JOIN ---
async def menu_force(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🗑️ Delete All Links", callback_data="del_force")], [InlineKeyboardButton("🔙 Back", callback_data="back")]]
    await update.callback_query.edit_message_text("Send <b>Force Join Photo</b> (or /cancel).", reply_markup=InlineKeyboardMarkup(build_menu(kb[0] + kb[1], 2)), parse_mode=ParseMode.HTML)
    return F_MEDIA

async def del_force(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.clear_force_channels()
    await db.set_setting('f_ph', None)
    await db.set_setting('f_txt', None)
    kb = [[InlineKeyboardButton("🔙 Back", callback_data="back")]]
    await update.callback_query.edit_message_text("🗑️ Deleted.", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

async def save_f_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['f_ph'] = update.message.photo[-1].file_id
    await update.message.reply_text("Send <b>Force Join Text</b>.")
    return F_TEXT

async def save_f_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['f_txt'] = update.message.text
    await update.message.reply_text("Send Channels: `Name Link` (One per line).")
    return F_LINKS

async def save_f_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.clear_force_channels()
    lines = update.message.text.split('\n')
    for line in lines:
        try:
            parts = line.rsplit(maxsplit=1)
            if len(parts) == 2:
                n, l = parts
                await db.add_force_channel(n, l)
        except: pass
    await db.set_setting('f_ph', context.user_data['f_ph'])
    await db.set_setting('f_txt', context.user_data['f_txt'])
    kb = [[InlineKeyboardButton("🔙 Back", callback_data="back")]]
    await update.message.reply_text("✅ Saved.", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

# --- UPDATE CHANNEL ---
async def menu_upd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    curr = await db.get_setting('upd_link')
    txt = f"Current: {curr}" if curr else "None"
    kb = [[InlineKeyboardButton("🗑️ Delete", callback_data="del_upd")], [InlineKeyboardButton("🔙 Back", callback_data="back")]]
    await update.callback_query.edit_message_text(f"{txt}\nSend <b>Update Channel Link</b>.", reply_markup=InlineKeyboardMarkup(build_menu(kb[0] + kb[1], 2)), parse_mode=ParseMode.HTML)
    return UPD_LINK

async def save_upd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.set_setting('upd_link', update.message.text)
    kb = [[InlineKeyboardButton("🔙 Back", callback_data="back")]]
    await update.message.reply_text("✅ Saved.", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

async def del_upd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.set_setting('upd_link', None)
    kb = [[InlineKeyboardButton("🔙 Back", callback_data="back")]]
    await update.callback_query.edit_message_text("🗑️ Deleted.", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

# --- WELCOME ---
async def menu_wel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🗑️ Delete", callback_data="del_wel")], [InlineKeyboardButton("🔙 Back", callback_data="back")]]
    await update.callback_query.edit_message_text("Send <b>Welcome Photo</b>.", reply_markup=InlineKeyboardMarkup(build_menu(kb[0] + kb[1], 2)), parse_mode=ParseMode.HTML)
    return WEL_MEDIA

async def del_wel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.set_setting('w_txt', None)
    await db.set_setting('w_ph', None)
    kb = [[InlineKeyboardButton("🔙 Back", callback_data="back")]]
    await update.callback_query.edit_message_text("🗑️ Deleted.", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

async def save_w_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['w_ph'] = update.message.photo[-1].file_id
    await update.message.reply_text("Send <b>Welcome Text</b>.")
    return WEL_TEXT

async def save_w_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.set_setting('w_txt', update.message.text)
    await db.set_setting('w_ph', context.user_data['w_ph'])
    kb = [[InlineKeyboardButton("🔙 Back", callback_data="back")]]
    await update.message.reply_text("✅ Saved.", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

# --- CUSTOM BTNS ---
async def menu_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🗑️ Clear", callback_data="del_btn")], [InlineKeyboardButton("🔙 Back", callback_data="back")]]
    await update.callback_query.edit_message_text("Send: `Name - Link`", reply_markup=InlineKeyboardMarkup(build_menu(kb[0] + kb[1], 2)), parse_mode=ParseMode.HTML)
    return BTN_TXT

async def save_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        n, l = update.message.text.split('-', 1)
        await db.add_custom_btn(n.strip(), l.strip())
        kb = [[InlineKeyboardButton("🔙 Back", callback_data="back")]]
        await update.message.reply_text("✅ Added.", reply_markup=InlineKeyboardMarkup(kb))
    except: await update.message.reply_text("Error: `Name - Link`")
    return ConversationHandler.END

async def del_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.clear_custom_btns()
    kb = [[InlineKeyboardButton("🔙 Back", callback_data="back")]]
    await update.callback_query.edit_message_text("🗑️ Cleared.", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

async def stats_cb(u, c): await u.callback_query.answer(f"Users: {await db.get_stats()}", show_alert=True)

# ================= START FLOW =================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await db.add_user(uid, update.effective_user.first_name, update.effective_user.username)
    
    # Store deep link payload
    if context.args:
        context.user_data['pl'] = context.args[0]

    # --- CHECK UPDATE CHANNEL STATUS ---
    upd_link = await db.get_setting('upd_link')
    is_member = True
    
    if upd_link:
        if not context.user_data.get('upd_verified'):
            cid = get_channel_id_from_link(upd_link)
            if cid:
                try:
                    m = await context.bot.get_chat_member(cid, uid)
                    if m.status in ['left', 'kicked']: is_member = False
                except: is_member = False # Assume not joined if cant check
            else: is_member = False

    if not is_member and upd_link:
        w_txt = await db.get_setting('w_txt') or "Welcome!"
        w_ph = await db.get_setting('w_ph')
        
        btns = [InlineKeyboardButton("🔔 Join Update Channel", url=upd_link), InlineKeyboardButton("✅ I Have Joined", callback_data="chk_upd")]
        cust = await db.get_custom_btns()
        c_btns = [InlineKeyboardButton(b['name'], url=b['link']) for b in cust]
        footer = [InlineKeyboardButton("🆘 Support Chat", callback_data="supp")]
        
        markup = InlineKeyboardMarkup(build_menu(btns, 2) + build_menu(c_btns, 2) + [footer])
        
        if w_ph: msg = await update.message.reply_photo(w_ph, caption=w_txt, reply_markup=markup)
        else: msg = await update.message.reply_text(w_txt, reply_markup=markup)
        context.user_data['welcome_msg_id'] = msg.message_id
        return ConversationHandler.END

    await flow_step_2(update, context)

async def chk_upd_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['upd_verified'] = True
    try: await update.callback_query.message.delete()
    except: pass
    await flow_step_2(update, context)

async def flow_step_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    
    # 1. Support Msg (1 min)
    kb = [[InlineKeyboardButton("🆘 Contact Admin Now", callback_data="supp")]]
    msg = await context.bot.send_message(uid, "ℹ️ <b>Contact admin now for any query.</b>\n(Disappears in 1 min)", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    context.job_queue.run_once(del_msg, 60, data={'c': uid, 'm': msg.message_id})
    
    # 2. Check Force Join
    await check_force_join(update, context)

async def check_force_join(update, context):
    uid = update.effective_user.id
    
    channels = await db.get_force_channels()
    # Force Join shows for EVERYONE (if set)
    if channels:
        f_txt = await db.get_setting('f_txt') or "Join these to access files:"
        f_ph = await db.get_setting('f_ph')
        
        btns = [InlineKeyboardButton(c['name'], url=c['link']) for c in channels]
        footer = [InlineKeyboardButton("✅ Verify & Get File", callback_data="chk_force")]
        markup = InlineKeyboardMarkup(build_menu(btns, 2, footer))
        
        if f_ph: await context.bot.send_photo(uid, f_ph, caption=f_txt, reply_markup=markup)
        else: await context.bot.send_message(uid, f_txt, reply_markup=markup)
        return

    await deliver(update, context, uid)

async def chk_force_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: await update.callback_query.message.delete()
    except: pass
    await deliver(update, context, update.effective_user.id)

async def deliver(update, context, uid):
    lid = context.user_data.get('pl')
    if not lid: return await context.bot.send_message(uid, "✅ <b>Welcome!</b> Use buttons to explore.", parse_mode=ParseMode.HTML)
    
    data = await db.get_content(lid)
    if not data: return await context.bot.send_message(uid, "❌ Link Expired.")
    
    try:
        msgs = []
        if data['type'] == 'single':
            # Priority: Try File ID
            if data.get('fid'):
                ftype, cap = data.get('ftype', 'doc'), data.get('cap', "")
                if ftype == 'video': m = await context.bot.send_video(uid, data['fid'], caption=cap)
                elif ftype == 'photo': m = await context.bot.send_photo(uid, data['fid'], caption=cap)
                elif ftype == 'audio': m = await context.bot.send_audio(uid, data['fid'], caption=cap)
                else: m = await context.bot.send_document(uid, data['fid'], caption=cap)
                msgs.append(m.message_id)
            else:
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
        
        for m_id in msgs: context.job_queue.run_once(del_msg, 1800, data={'c': uid, 'm': m_id})
    except Exception as e: await context.bot.send_message(uid, f"❌ <b>Error:</b> {e}", parse_mode=ParseMode.HTML)

# ================= SUPPORT =================
async def start_supp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await db.support.update_one({'_id': uid}, {'$set': {'on': True}}, upsert=True)
    kb = [[InlineKeyboardButton("❌ End Chat", callback_data="end_supp")]]
    await context.bot.send_message(uid, "✅ <b>Connected!</b>\nSend msg to Admin.", reply_markup=InlineKeyboardMarkup(kb))
    u = update.effective_user
    akb = [[InlineKeyboardButton("End Chat", callback_data=f"end_admin_{uid}")]]
    await context.bot.send_message(ADMIN_ID, f"🚨 <b>Support:</b> {u.first_name} (ID: {uid})", reply_markup=InlineKeyboardMarkup(akb), parse_mode=ParseMode.HTML)

async def handle_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message
    
    if uid == ADMIN_ID:
        if msg.reply_to_message:
            txt = msg.reply_to_message.text or msg.reply_to_message.caption or ""
            m = re.search(r'ID: (\d+)', txt)
            if m:
                try:
                    await context.bot.copy_message(int(m.group(1)), ADMIN_ID, msg.message_id)
                    await msg.reply_text("✅ Sent.")
                except: await msg.reply_text("❌ Failed.")
        return

    s = await db.support.find_one({'_id': uid})
    if s and s.get('on'):
        cap = f"Message from User\n[ID: {uid}]"
        if msg.text: await context.bot.send_message(ADMIN_ID, f"{msg.text}\n\n{cap}")
        else: await context.bot.copy_message(ADMIN_ID, uid, msg.message_id, caption=cap)

async def end_supp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = update.callback_query.data
    if d == "end_supp":
        await db.support.update_one({'_id': update.effective_user.id}, {'$set': {'on': False}})
        await update.callback_query.edit_message_text("❌ Chat Ended.")
        await context.bot.send_message(ADMIN_ID, f"User {update.effective_user.id} ended chat.")
    else:
        tgt = int(d.split("_")[2])
        await db.support.update_one({'_id': tgt}, {'$set': {'on': False}})
        await update.callback_query.edit_message_text("❌ Ended.")
        await context.bot.send_message(tgt, "❌ Admin ended the chat.")

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    contact = update.message.contact
    await context.bot.send_message(ADMIN_ID, f"👤 <b>User Info</b>\nName: {contact.first_name}\nID: <code>{uid}</code>\nPhone: <code>{contact.phone_number}</code>", parse_mode=ParseMode.HTML)
    await update.message.reply_text("✅ Contact Sent.", reply_markup=ReplyKeyboardRemove())

async def del_msg(context: ContextTypes.DEFAULT_TYPE):
    try: await context.bot.delete_message(context.job.data['c'], context.job.data['m'])
    except: pass

async def stats_cb(u, c): await u.callback_query.answer(f"Users: {await db.get_stats()}", show_alert=True)

# ================= MAIN =================
def main():
    if not MONGO_URL: return
    global db
    db = Database(MONGO_URL)
    app = Application.builder().token(BOT_TOKEN).build()
    
    fallback = [CommandHandler("start", cmd_start), CommandHandler("admin", cmd_admin), CallbackQueryHandler(back_home, pattern="back")]

    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(menu_add, pattern="menu_add")], states={CONTENT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_content), MessageHandler(filters.ALL & ~filters.COMMAND, handle_content)]}, fallbacks=fallback, allow_reentry=True))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(menu_cast, pattern="menu_cast")], states={BC_PHOTO: [MessageHandler(filters.ALL & ~filters.COMMAND, cast_photo), CallbackQueryHandler(cast_photo, pattern="skip")], BC_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cast_text), CallbackQueryHandler(cast_text, pattern="skip"), CallbackQueryHandler(back_to_photo, pattern="back_photo")], BC_TIME: [CallbackQueryHandler(cast_send)]}, fallbacks=fallback, allow_reentry=True))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(menu_wel, pattern="menu_wel")], states={WEL_MEDIA: [MessageHandler(filters.PHOTO, save_w_media)], WEL_TEXT: [MessageHandler(filters.TEXT, save_w_text)]}, fallbacks=fallback, allow_reentry=True))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(menu_upd, pattern="menu_upd"), CallbackQueryHandler(del_upd, pattern="del_upd")], states={UPD_LINK: [MessageHandler(filters.TEXT, save_upd)]}, fallbacks=fallback, allow_reentry=True))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(menu_force, pattern="menu_force"), CallbackQueryHandler(del_force, pattern="del_force")], states={F_MEDIA: [MessageHandler(filters.PHOTO, save_f_media)], F_TEXT: [MessageHandler(filters.TEXT, save_f_text)], F_LINKS: [MessageHandler(filters.TEXT, save_f_links)]}, fallbacks=fallback, allow_reentry=True))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(menu_btn, pattern="menu_btn"), CallbackQueryHandler(del_btn, pattern="del_btn")], states={BTN_TXT: [MessageHandler(filters.TEXT, save_btn)]}, fallbacks=fallback, allow_reentry=True))

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CallbackQueryHandler(back_home, pattern="back"))
    
    app.add_handler(CallbackQueryHandler(start_supp, pattern="supp"))
    app.add_handler(CallbackQueryHandler(end_supp, pattern="^end_"))
    
    app.add_handler(CallbackQueryHandler(chk_upd_cb, pattern="chk_upd"))
    app.add_handler(CallbackQueryHandler(chk_force_cb, pattern="chk_force"))
    app.add_handler(CallbackQueryHandler(stats_cb, pattern="stats"))
    
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.ALL, handle_chat))

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
