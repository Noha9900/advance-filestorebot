import os, asyncio, secrets, logging, html, math, re
from datetime import datetime
from flask import Flask
from threading import Thread
import certifi 
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler, Defaults
)
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

# --- LOGGING ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIG ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
MONGO_URL = os.getenv("MONGO_URL")
PORT = int(os.getenv("PORT", "8080"))

# --- DATABASE ---
client = AsyncIOMotorClient(
    MONGO_URL, 
    maxPoolSize=10, 
    minPoolSize=1, 
    serverSelectionTimeoutMS=5000,
    tlsCAFile=certifi.where()
)
db = client["vault_bot_db"]
col_settings, col_guides, col_vaults = db["settings"], db["guides"], db["vaults"]

# --- STATES ---
(W_TXT, W_PHO, AD_PHO_STATE, AD_TXT_STATE, AD_LNK_STATE, 
 ANI_NA, ANI_ME, ANI_DE, ANI_CHAN, ANI_LI, 
 MOV_NA, MOV_ME, MOV_DE, MOV_CHAN, MOV_LI, 
 A_V_FOLD, A_V_SUB, A_V_POST, A_V_DESC, A_V_FILES, 
 V_KEY_INPUT, U_GUIDE_SELECT, U_V_SUB_SELECT, ADM_DEL_SELECT,
 UPD_MENU, UPD_DESC, UPD_ADD_LINK, UPD_DEL_LINK,
 SEARCH_STATE) = range(29)

# --- HELPERS ---
def get_file_info(message):
    if message.animation: return message.animation.file_id, "animation"
    if message.video: return message.video.file_id, "video"
    if message.photo: return message.photo[-1].file_id, "photo"
    if message.document: return message.document.file_id, "document"
    return None, None

async def del_msg(context: ContextTypes.DEFAULT_TYPE):
    try: await context.bot.delete_message(chat_id=context.job.chat_id, message_id=context.job.data)
    except: pass

async def get_settings():
    w = await col_settings.find_one({"type": "welcome"}) or {"text": "Welcome!", "photo": None}
    a = await col_settings.find_one({"type": "adult"}) or {"text": "Adult Zone", "photo": None, "channels": []}
    u = await col_settings.find_one({"type": "updates"}) or {"desc": "Check our channels!", "links": []}
    return w, a, u

# --- USER START ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear() 
    w, _, _ = await get_settings()
    
    kb = [
        [InlineKeyboardButton("Adult Stream 🔥", callback_data="u_ad_0")], 
        [InlineKeyboardButton("Anime Guide 🎌", callback_data="list_anime_0"), InlineKeyboardButton("Movie Guide 🎬", callback_data="list_movies_0")],
        [InlineKeyboardButton("Secret Vault 🔒", callback_data="u_vault_folders")],
        [InlineKeyboardButton("Updates Channel 📢", callback_data="u_updates")]
    ]
    markup = InlineKeyboardMarkup(kb)
    
    if update.message:
        if w.get("photo"):
            msg = await update.message.reply_photo(w["photo"], caption=w["text"], reply_markup=markup)
        else:
            msg = await update.message.reply_text(w["text"], reply_markup=markup)
        context.job_queue.run_once(del_msg, 60, data=msg.message_id, chat_id=update.effective_chat.id)
    else:
        try:
            if w.get("photo"):
                if update.callback_query.message.photo:
                    await update.callback_query.edit_message_media(media=InputMediaPhoto(media=w["photo"], caption=w["text"]), reply_markup=markup)
                else:
                    await update.callback_query.message.delete()
                    await update.callback_query.message.reply_photo(w["photo"], caption=w["text"], reply_markup=markup)
            else:
                if update.callback_query.message.photo:
                    await update.callback_query.message.delete()
                    await update.callback_query.message.reply_text(w["text"], reply_markup=markup)
                else:
                    await update.callback_query.edit_message_text(w["text"], reply_markup=markup)
        except:
            try: await update.callback_query.message.delete()
            except: pass
            if w.get("photo"): await update.callback_query.message.reply_photo(w["photo"], caption=w["text"], reply_markup=markup)
            else: await update.callback_query.message.reply_text(w["text"], reply_markup=markup)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled. Type /start.")
    return ConversationHandler.END

# --- USER ROUTER ---
async def user_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "main": return await start(update, context)

    # --- UPDATES ---
    if query.data == "u_updates":
        _, _, u = await get_settings()
        txt = f"📢 <b>UPDATES</b>\n\n{html.escape(str(u.get('desc', 'Check our channels!')))}\n\n"
        if u.get('links'):
            txt += "👇 <b>Join Here:</b>\n"
            for link in u['links']:
                txt += f"• {html.escape(str(link.get('name', 'Channel')))} - <a href='{link.get('url', '')}'><b>Click Me</b></a>\n"
        else:
            txt += "No updates yet."
        kb = [[InlineKeyboardButton("🔙 Back", callback_data="main")]]
        if query.message.photo:
            await query.message.delete()
            await query.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), disable_web_page_preview=True)
        else:
            await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), disable_web_page_preview=True)
        return ConversationHandler.END

    # --- ADULT ---
    if query.data.startswith("u_ad"):
        page = int(query.data.split("_")[-1]) if "_" in query.data else 0
        _, ad, _ = await get_settings()
        channels = ad.get("channels", [])
        ITEMS_PER_PAGE = 8
        start_idx = page * ITEMS_PER_PAGE
        end_idx = start_idx + ITEMS_PER_PAGE
        current_batch = channels[start_idx:end_idx]
        kb = [[InlineKeyboardButton(c["name"], url=c["link"])] for c in current_batch]
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"u_ad_{page-1}"))
        if end_idx < len(channels): nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"u_ad_{page+1}"))
        if nav: kb.append(nav)
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="main")])
        markup = InlineKeyboardMarkup(kb)
        try:
            if ad.get("photo"):
                if query.message.photo:
                    await query.edit_message_media(media=InputMediaPhoto(media=ad["photo"], caption=ad["text"]), reply_markup=markup)
                else:
                    await query.message.delete()
                    await query.message.reply_photo(ad["photo"], caption=ad["text"], reply_markup=markup)
            else:
                if query.message.photo:
                    await query.message.delete()
                    await query.message.reply_text(ad["text"], reply_markup=markup)
                else:
                    await query.edit_message_text(ad["text"], reply_markup=markup)
        except:
            await query.message.delete()
            if ad.get("photo"): await query.message.reply_photo(ad["photo"], caption=ad["text"], reply_markup=markup)
            else: await query.message.reply_text(ad["text"], reply_markup=markup)
        return ConversationHandler.END

    # --- LISTS (ANIME/MOVIE) ---
    elif query.data.startswith("list_"):
        parts = query.data.split("_")
        g_type = parts[1]
        page = int(parts[2])
        LIMIT = 50
        skip = page * LIMIT
        
        search_query = context.user_data.get("search_query")
        
        if search_query:
            regex_pattern = re.escape(search_query)
            db_query = {"type": g_type, "name": {"$regex": regex_pattern, "$options": "i"}}
            header = f"🔍 <b>SEARCH: {html.escape(search_query)}</b>\n\n"
        else:
            db_query = {"type": g_type}
            header = f"📖 <b>{g_type.upper()} LIST</b> (Page {page+1})\n\n"

        total_count = await col_guides.count_documents(db_query)
        items = await col_guides.find(db_query).sort("_id", 1).skip(skip).limit(LIMIT).to_list(LIMIT)

        if not items:
            txt = header + "❌ No content found."
        else:
            txt = header + "Reply with <b>Number</b> to watch:\n(Type text to search)\n\n"
            for i, item in enumerate(items):
                if search_query:
                    display_num = i + 1 
                else:
                    display_num = skip + i + 1
                txt += f"<b>{display_num}.</b> {html.escape(str(item.get('name', 'Unknown')))}\n"

        nav_kb = []
        if page > 0: nav_kb.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"list_{g_type}_{page-1}"))
        if skip + LIMIT < total_count: nav_kb.append(InlineKeyboardButton("Next ➡️", callback_data=f"list_{g_type}_{page+1}"))
        
        kb = []
        if nav_kb: kb.append(nav_kb)
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="main")])
        
        context.user_data["view_type"] = g_type
        if not query.data.startswith("list_") and "search_query" in context.user_data:
             del context.user_data["search_query"]

        try:
            if query.message.photo:
                await query.message.delete()
                await query.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))
            else:
                await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))
        except:
             await query.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))
        return U_GUIDE_SELECT

    # --- VAULT FOLDERS ---
    elif query.data == "u_vault_folders":
        folders = await col_vaults.distinct("folder")
        btns = [InlineKeyboardButton(f, callback_data=f"vfold_{f}") for f in folders]
        kb = [btns[i:i + 2] for i in range(0, len(btns), 2)]
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="main")])
        await query.message.delete()
        await query.message.reply_text("📂 Select a Folder:", reply_markup=InlineKeyboardMarkup(kb))
        return ConversationHandler.END

    # --- VAULT CONTENTS ---
    elif query.data.startswith("vfold_"):
        fname = query.data.replace("vfold_", "")
        items = await col_vaults.find({"folder": fname}).sort("_id", 1).to_list(100)
        txt = f"📁 <b>{fname}</b>\n\nReply with <b>Number</b> to unlock:\n"
        for i, x in enumerate(items): txt += f"{i+1}. {x['sub_name']}\n"
        context.user_data["active_vault_folder"] = fname
        await query.message.delete()
        await query.message.reply_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="u_vault_folders")]]))
        return U_V_SUB_SELECT

# --- SEARCH HANDLER ---
async def perform_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.message.text
    # Determine type from previous state or default to anime
    g_type = context.user_data.get("view_type", "anime") 
    
    context.user_data["search_query"] = query_text
    context.user_data["view_type"] = g_type 
    
    LIMIT = 50
    regex_pattern = re.escape(query_text)
    db_query = {"type": g_type, "name": {"$regex": regex_pattern, "$options": "i"}}
    
    items = await col_guides.find(db_query).sort("_id", 1).limit(LIMIT).to_list(LIMIT)
    
    txt = f"🔍 <b>RESULTS FOR: '{html.escape(query_text)}'</b>\n\n"
    if not items:
        txt += "❌ No matches found.\nTry a simpler name."
        kb = [[InlineKeyboardButton("🔙 Back to List", callback_data=f"list_{g_type}_0")]]
    else:
        txt += "Reply with the <b>Number</b> to watch:\n\n"
        for i, item in enumerate(items):
            txt += f"<b>{i+1}.</b> {html.escape(str(item['name']))}\n"
        kb = [[InlineKeyboardButton("🔙 Back to List", callback_data=f"list_{g_type}_0")]]

    await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))
    return U_GUIDE_SELECT

# --- ADMIN PANEL ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    kb = [[InlineKeyboardButton("Set Welcome", callback_data="a_w"), InlineKeyboardButton("Set Adult", callback_data="a_ad")],
          [InlineKeyboardButton("Add Anime", callback_data="a_ani"), InlineKeyboardButton("Add Movie", callback_data="a_mov")],
          [InlineKeyboardButton("Set Updates 📢", callback_data="a_upd")],
          [InlineKeyboardButton("Create Vault Content 🔒", callback_data="a_v")],
          [InlineKeyboardButton("🗑 Delete Mode", callback_data="a_del")]]
    await update.message.reply_text("🛠 <b>ADMIN PANEL</b>", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

async def admin_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "a_w": await query.edit_message_text("Send Welcome Text:"); return W_TXT
    if query.data == "a_ad": await query.edit_message_text("Adult Photo (or /skip):"); return AD_PHO_STATE
    if query.data == "a_ani": context.user_data["p"]="anime"; await query.edit_message_text("Anime Name:"); return ANI_NA
    if query.data == "a_mov": context.user_data["p"]="movies"; await query.edit_message_text("Movie Name:"); return MOV_NA
    if query.data == "a_v": await query.edit_message_text("📂 Folder Name:"); return A_V_FOLD
    if query.data == "a_del": return await admin_del_menu(update, context)
    if query.data == "a_upd": 
        kb = [[InlineKeyboardButton("Set Description", callback_data="upd_desc")],
              [InlineKeyboardButton("Add Channel Link", callback_data="upd_add")],
              [InlineKeyboardButton("Remove Channel", callback_data="upd_rem")],
              [InlineKeyboardButton("🔙 Back", callback_data="a_panel_back")]]
        await query.edit_message_text("📢 <b>Updates Management</b>", reply_markup=InlineKeyboardMarkup(kb))
        return UPD_MENU
    if query.data == "a_back": return await admin_panel(update, context)

# --- SAVING LOGICS ---
async def save_w_txt(update, context):
    context.user_data["wt"] = update.message.text
    await update.message.reply_text("Send Photo (or /skip):"); return W_PHO

async def save_w_pho(update, context):
    fid = get_fid(update.message)
    await col_settings.update_one({"type": "welcome"}, {"$set": {"text": context.user_data["wt"], "photo": fid}}, upsert=True)
    await update.message.reply_text("✅ Welcome Set!"); return ConversationHandler.END

# --- ANIME/MOVIE LOGIC ---
async def save_g_name(update, context):
    context.user_data["gtmp"] = {"name": update.message.text, "type": context.user_data["p"]}
    await update.message.reply_text("Send Media (Photo/Video/GIF):"); return ANI_ME if context.user_data["p"]=="anime" else MOV_ME

async def save_g_media(update, context):
    fid, ftype = get_file_info(update.message)
    if not fid: await update.message.reply_text("❌ Send valid Media:"); return
    context.user_data["gtmp"]["file"] = fid
    context.user_data["gtmp"]["media_type"] = ftype 
    await update.message.reply_text("Send Description:"); return ANI_DE if context.user_data["p"]=="anime" else MOV_DE

async def save_g_desc(update, context):
    context.user_data["gtmp"]["desc"] = update.message.text
    await update.message.reply_text("📢 <b>Channel Info</b>\nSend: Name | Link\n(e.g., My Channel | https://t.me/xyz)")
    return ANI_CHAN if context.user_data["p"]=="anime" else MOV_CHAN

async def save_g_chan(update, context):
    try:
        parts = update.message.text.split("|")
        context.user_data["gtmp"]["chan_name"] = parts[0].strip()
        context.user_data["gtmp"]["chan_link"] = parts[1].strip()
        await update.message.reply_text("🔗 <b>Where to Watch Link:</b>")
        return ANI_LI if context.user_data["p"]=="anime" else MOV_LI
    except:
        await update.message.reply_text("❌ Format Error. Send: Name | Link")
        return ANI_CHAN if context.user_data["p"]=="anime" else MOV_CHAN

async def save_g_final(update, context):
    context.user_data["gtmp"]["link"] = update.message.text
    await col_guides.insert_one(context.user_data["gtmp"])
    await update.message.reply_text("✅ Content Added!"); return ConversationHandler.END

# --- UPDATES LOGIC ---
async def upd_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "upd_desc":
        await query.edit_message_text("📝 Send new Description:")
        return UPD_DESC
    if query.data == "upd_add":
        await query.edit_message_text("➕ Send: Name | Link")
        return UPD_ADD_LINK
    if query.data == "upd_rem":
        _, _, u = await get_settings()
        if not u.get('links'): 
            await query.edit_message_text("No links to remove.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="a_upd")]]))
            return UPD_MENU
        kb = []
        for i, l in enumerate(u['links']):
            kb.append([InlineKeyboardButton(f"❌ {l['name']}", callback_data=f"upd_del_{i}")])
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="a_upd")])
        await query.edit_message_text("Select to remove:", reply_markup=InlineKeyboardMarkup(kb))
        return UPD_DEL_LINK
    if query.data == "a_panel_back": return await admin_panel(update, context)
    if query.data == "a_upd": 
        kb = [[InlineKeyboardButton("Set Description", callback_data="upd_desc")],
              [InlineKeyboardButton("Add Channel Link", callback_data="upd_add")],
              [InlineKeyboardButton("Remove Channel", callback_data="upd_rem")],
              [InlineKeyboardButton("🔙 Back", callback_data="a_panel_back")]]
        await query.edit_message_text("📢 <b>Updates Management</b>", reply_markup=InlineKeyboardMarkup(kb))
        return UPD_MENU

async def save_upd_desc(update, context):
    await col_settings.update_one({"type": "updates"}, {"$set": {"desc": update.message.text}}, upsert=True)
    await update.message.reply_text("✅ Description Updated!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="a_upd")]]))
    return UPD_MENU

async def save_upd_link(update, context):
    try:
        parts = update.message.text.split("|")
        await col_settings.update_one({"type": "updates"}, {"$push": {"links": {"name": parts[0].strip(), "url": parts[1].strip()}}}, upsert=True)
        await update.message.reply_text("✅ Link Added!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="a_upd")]]))
        return UPD_ADD_LINK
    except:
        await update.message.reply_text("❌ Error. Format: Name | Link")
        return UPD_ADD_LINK

async def del_upd_link(update, context):
    idx = int(update.callback_query.data.split("_")[-1])
    await col_settings.update_one({"type": "updates"}, {"$unset": {f"links.{idx}": 1}})
    await col_settings.update_one({"type": "updates"}, {"$pull": {"links": None}})
    await update.callback_query.edit_message_text("✅ Removed!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="a_upd")]]))
    return UPD_MENU

# --- VAULT SAVING ---
async def v_sub(update, context):
    context.user_data["v_data"] = {"folder": update.message.text, "files": []}
    await update.message.reply_text("📝 Sub-Name (e.g. Episode 1):"); return A_V_SUB

async def v_post(update, context):
    context.user_data["v_data"]["sub_name"] = update.message.text
    await update.message.reply_text("🖼 Send Poster Image:"); return A_V_POST

async def v_desc(update, context):
    fid = get_fid(update.message)
    if not fid: await update.message.reply_text("❌ Poster required (Photo):"); return
    context.user_data["v_data"]["poster"] = fid
    await update.message.reply_text("✍️ Description:"); return A_V_DESC

async def v_files_start(update, context):
    context.user_data["v_data"]["desc"] = update.message.text
    await update.message.reply_text("📎 <b>BULK UPLOAD MODE</b>\n\nSend videos, photos, or files one by one.\nWhen finished, type <code>/done</code> to save all under one key."); return A_V_FILES

async def v_collect(update, context):
    msg_text = update.message.text or ""
    if msg_text.lower() == "/done":
        if "v_data" not in context.user_data:
            await update.message.reply_text("❌ Session expired. Start over."); return ConversationHandler.END
        if not context.user_data["v_data"]["files"]:
            await update.message.reply_text("❌ No files added! Send files first."); return A_V_FILES
        key = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%^&*") for _ in range(12))
        context.user_data["v_data"]["key"] = key
        await col_vaults.insert_one(context.user_data["v_data"])
        await update.message.reply_text(f"✅ <b>Bulk Saved!</b>\n\n📂 Folder: {context.user_data['v_data']['folder']}\n📄 Files: {len(context.user_data['v_data']['files'])}\n🔑 Key: <code>{key}</code>"); return ConversationHandler.END
    fid, ftype = get_file_info(update.message)
    if fid: 
        context.user_data["v_data"]["files"].append({"id": fid, "type": ftype})
        count = len(context.user_data['v_data']['files'])
        if count % 5 == 0 or count == 1:
            await update.message.reply_text(f"✅ {count} files queued. Send more or /done")
    else: 
        await update.message.reply_text("❌ Not a file. Send file or /done")
    return A_V_FILES

async def ad_pho_fn(update, context):
    fid = get_fid(update.message)
    context.user_data["ad_tmp"] = {"photo": fid}
    await update.message.reply_text("Adult Welcome Text:"); return AD_TXT_STATE

async def ad_txt_fn(update, context):
    context.user_data["ad_tmp"]["text"] = update.message.text
    await update.message.reply_text("Channel (Name | Link):"); return AD_LNK_STATE

async def ad_lnk_fn(update, context):
    try:
        parts = update.message.text.split("|")
        await col_settings.update_one({"type": "adult"}, {"$set": {"photo": context.user_data["ad_tmp"]["photo"], "text": context.user_data["ad_tmp"]["text"]}, "$push": {"channels": {"name": parts[0].strip(), "link": parts[1].strip()}}}, upsert=True)
        await update.message.reply_text("✅ <b>Saved!</b>")
        await update.message.reply_text("Send next (Name | Link) or /start to finish.")
        return AD_LNK_STATE
    except: await update.message.reply_text("Err: Name | Link"); return AD_LNK_STATE

# --- CONTENT DELIVERY (VAULT) ---
async def vault_select_sub(update, context):
    try:
        idx = int(update.message.text) - 1
        items = await col_vaults.find({"folder": context.user_data.get("active_vault_folder")}).sort("_id", 1).to_list(100)
        if 0 <= idx < len(items):
            item = items[idx]
            context.user_data["target_v"] = item["_id"]
            if item.get("poster"):
                await update.message.reply_photo(item["poster"], caption=f"📁 <b>{item['sub_name']}</b>\n\n{item['desc']}\n\n🔐 <b>Enter Key:</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="u_vault_folders")]]))
            else:
                await update.message.reply_text(f"📁 <b>{item['sub_name']}</b>\n\n{item['desc']}\n\n🔐 <b>Enter Key:</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="u_vault_folders")]]))
            return V_KEY_INPUT
        else: await update.message.reply_text(f"❌ Invalid Number. 1-{len(items)}")
    except ValueError: await update.message.reply_text("❌ Send a Number.")
    return U_V_SUB_SELECT

async def vault_key_check(update, context):
    v = await col_vaults.find_one({"_id": ObjectId(context.user_data.get("target_v"))})
    if v and update.message.text.strip() == v["key"]:
        count = len(v['files'])
        status_msg = await update.message.reply_text(f"🔓 Key Accepted! Sending {count} files...\nPlease wait.")
        for f in v["files"]:
            if isinstance(f, dict): fid, ftype = f['id'], f.get('type', 'document')
            else: fid, ftype = f, 'unknown'
            try:
                await asyncio.sleep(0.05) 
                if ftype == 'video': msg = await update.message.reply_video(fid)
                elif ftype == 'photo': msg = await update.message.reply_photo(fid)
                elif ftype == 'animation': msg = await update.message.reply_animation(fid)
                else: msg = await update.message.reply_document(fid) 
                context.job_queue.run_once(del_msg, 600, data=msg.message_id, chat_id=update.effective_chat.id)
            except Exception:
                try: 
                    msg = await update.message.reply_document(fid)
                    context.job_queue.run_once(del_msg, 600, data=msg.message_id, chat_id=update.effective_chat.id)
                except: pass 
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=status_msg.message_id)
        await update.message.reply_text("✅ All files sent! They will disappear in 10 mins.")
    else: await update.message.reply_text("❌ Wrong Key")
    return ConversationHandler.END

# --- GUIDE SHOW (FINAL FIX: NO SYSTEM ERROR ON SUCCESS) ---
async def guide_show(update, context):
    try:
        view_type = context.user_data.get("view_type")
        if not view_type:
            await update.message.reply_text("❌ Session expired. Click buttons again.")
            return ConversationHandler.END

        # 1. CHECK IF INPUT IS TEXT (SEARCH)
        if not update.message.text.isdigit():
             return await perform_search(update, context)

        # 2. HANDLE NUMBER SELECTION
        try:
            user_input = int(update.message.text)
            search_query = context.user_data.get("search_query")
            
            if search_query:
                # Search mode: Get specific result by index (1-based)
                regex_pattern = re.escape(search_query)
                items = await col_guides.find({"type": view_type, "name": {"$regex": regex_pattern, "$options": "i"}}).sort("_id", 1).limit(50).to_list(50)
                target_idx = user_input - 1
            else:
                # Normal list: Get global item
                target_idx = user_input - 1
                items = await col_guides.find({"type": view_type}).sort("_id", 1).skip(target_idx).limit(1).to_list(1)
                target_idx = 0 

        except ValueError:
            await update.message.reply_text("❌ Send a valid number or text to search.")
            return U_GUIDE_SELECT

        msg = await update.message.reply_text("⏳ Processing...")
        
        if items and 0 <= target_idx < len(items):
            item = items[target_idx]
            
            # SAFE DATA EXTRACTION (STR + ESCAPE)
            safe_name = html.escape(str(item.get('name', 'Unknown')))
            safe_desc = html.escape(str(item.get('desc', '')))
            chan_name = html.escape(str(item.get('chan_name', 'Channel')))
            chan_link = item.get('chan_link', '')
            watch_link = item.get('link', '') 
            
            if len(safe_desc) > 800: safe_desc = safe_desc[:800] + "..."
            
            caption = f"⭐ <b>{safe_name}</b>\n\n{safe_desc}\n\n"
            if chan_link:
                caption += f"📣 {chan_name} - <a href='{chan_link}'><b>Click Me</b></a>\n\n"
            caption += f"🔗 <b>Watch Here:</b> {watch_link}"
            
            mtype = item.get("media_type", "photo") 
            fid = item["file"]
            
            success = False
            sent_msg = None
            
            # --- ROBUST SENDING LOGIC ---
            try:
                if mtype == "video": sent_msg = await update.message.reply_video(fid, caption=caption)
                elif mtype == "animation": sent_msg = await update.message.reply_animation(fid, caption=caption)
                elif mtype == "document": sent_msg = await update.message.reply_document(fid, caption=caption)
                else: sent_msg = await update.message.reply_photo(fid, caption=caption)
                success = True
            except Exception:
                # Fallback to Document if type failed
                try:
                    sent_msg = await update.message.reply_document(fid, caption=caption)
                    success = True
                except: pass
            
            # Delete "Processing" message
            try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg.message_id)
            except: pass
            
            if success and sent_msg:
                # SCHEDULE DELETE (10 MIN)
                context.job_queue.run_once(del_msg, 600, data=sent_msg.message_id, chat_id=update.effective_chat.id)
                # SEND SEPARATE WARNING
                await update.message.reply_text("⚠️ Content will disappear in 10 minutes.")
                # EXIT FUNCTION TO PREVENT ERROR FALLTHROUGH
                return U_GUIDE_SELECT
            else:
                await update.message.reply_text("❌ Error: File is deleted or invalid.")
        else:
            try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg.message_id)
            except: pass
            await update.message.reply_text("❌ Invalid Number.")
            
    except Exception as e:
        logger.error(f"Guide Error: {e}")
        await update.message.reply_text("❌ System Error. Try again.")
    return U_GUIDE_SELECT

# --- DELETE & MISC ---
async def admin_del_menu(update, context):
    kb = [[InlineKeyboardButton("Anime", callback_data="del_anime"), InlineKeyboardButton("Movie", callback_data="del_movies")],
          [InlineKeyboardButton("Vault", callback_data="del_vault"), InlineKeyboardButton("Adult Link", callback_data="del_adult")],
          [InlineKeyboardButton("🔙 Back", callback_data="a_back")]]
    await update.callback_query.edit_message_text("🗑 Select Category:", reply_markup=InlineKeyboardMarkup(kb)); return ADM_DEL_SELECT

async def admin_del_process(update, context):
    dtype = update.callback_query.data.split("_")[1]
    context.user_data["del_type"] = dtype
    if dtype == "adult":
        _, ad, _ = await get_settings()
        kb = [[InlineKeyboardButton(c["name"], callback_data=f"confirm_del_{i}")] for i, c in enumerate(ad.get("channels", []))]
    else:
        col = col_guides if dtype in ["anime", "movies"] else col_vaults
        items = await col.find({"type": dtype} if dtype != "vault" else {}).to_list(100)
        kb = [[InlineKeyboardButton(x.get("name") or x.get("sub_name"), callback_data=f"confirm_del_{x['_id']}")] for x in items]
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="a_del")])
    await update.callback_query.edit_message_text("Select item to delete:", reply_markup=InlineKeyboardMarkup(kb))

async def admin_confirm_delete(update, context):
    oid = update.callback_query.data.split("_")[-1]
    dtype = context.user_data["del_type"]
    if dtype == "adult":
        await col_settings.update_one({"type": "adult"}, {"$unset": {f"channels.{int(oid)}": 1}})
        await col_settings.update_one({"type": "adult"}, {"$pull": {"channels": None}})
    else:
        await (col_guides if dtype in ["anime", "movies"] else col_vaults).delete_one({"_id": ObjectId(oid)})
    await update.callback_query.edit_message_text("✅ Deleted!"); return ConversationHandler.END

# --- APP ---
server = Flask(__name__)
@server.route('/')
def h(): return "OK"

async def error_handler(update, context): logger.error(f"Error {context.error}")

def main():
    defaults = Defaults(parse_mode=ParseMode.HTML)
    app = Application.builder().token(TOKEN).defaults(defaults).build()
    
    async def init(): 
        await col_vaults.create_index("key", unique=True)
        await col_guides.create_index([("name", "text")]) 
    asyncio.get_event_loop().run_until_complete(init())

    global_handlers = [
        CommandHandler("start", start),
        CommandHandler("admin", admin_panel),
        CommandHandler("cancel", cancel),
        CallbackQueryHandler(start, pattern="^main$"),
        CallbackQueryHandler(admin_panel, pattern="^a_panel_back$"),
        CallbackQueryHandler(user_router, pattern="^u_"),
        CallbackQueryHandler(user_router, pattern="^vfold_"),
        CallbackQueryHandler(user_router, pattern="^list_"), 
        CallbackQueryHandler(user_router, pattern="^search_"),
        CallbackQueryHandler(admin_router, pattern="^a_"),
        CallbackQueryHandler(upd_router, pattern="^upd_"),
        CallbackQueryHandler(del_upd_link, pattern="^upd_del_")
    ]

    conv = ConversationHandler(
        entry_points=global_handlers,
        states={
            W_TXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_w_txt)], 
            W_PHO: [MessageHandler((filters.PHOTO | filters.Regex("/skip")) & ~filters.COMMAND, save_w_pho)],
            ANI_NA: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_g_name)], 
            ANI_ME: [MessageHandler((filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.Document.ALL) & ~filters.COMMAND, save_g_media)], 
            ANI_DE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_g_desc)], 
            ANI_CHAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_g_chan)],
            ANI_LI: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_g_final)],
            MOV_NA: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_g_name)], 
            MOV_ME: [MessageHandler((filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.Document.ALL) & ~filters.COMMAND, save_g_media)], 
            MOV_DE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_g_desc)], 
            MOV_CHAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_g_chan)],
            MOV_LI: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_g_final)],
            A_V_FOLD: [MessageHandler(filters.TEXT & ~filters.COMMAND, v_sub)], A_V_SUB: [MessageHandler(filters.TEXT & ~filters.COMMAND, v_post)], A_V_POST: [MessageHandler((filters.PHOTO | filters.VIDEO | filters.Document.ALL) & ~filters.COMMAND, v_desc)], A_V_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, v_files_start)], 
            A_V_FILES: [CommandHandler("done", v_collect), MessageHandler(filters.ALL & ~filters.COMMAND, v_collect)],
            AD_PHO_STATE: [MessageHandler((filters.PHOTO | filters.Regex("/skip")) & ~filters.COMMAND, ad_pho_fn)], AD_TXT_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ad_txt_fn)], AD_LNK_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ad_lnk_fn)],
            UPD_MENU: [CallbackQueryHandler(upd_router, pattern="^upd_"), CallbackQueryHandler(del_upd_link, pattern="^upd_del_"), CallbackQueryHandler(admin_panel, pattern="^a_panel_back$")],
            UPD_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_upd_desc)],
            UPD_ADD_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_upd_link), CallbackQueryHandler(upd_router, pattern="^a_upd$")],
            UPD_DEL_LINK: [CallbackQueryHandler(del_upd_link, pattern="^upd_del_"), CallbackQueryHandler(upd_router, pattern="^a_upd$")],
            # FIXED: guide_show handles both numbers AND text for search
            U_GUIDE_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, guide_show), CallbackQueryHandler(user_router)], 
            U_V_SUB_SELECT: [MessageHandler(filters.Regex(r'^\s*\d+\s*$'), vault_select_sub), CallbackQueryHandler(user_router)],
            V_KEY_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, vault_key_check), CallbackQueryHandler(user_router)], 
            SEARCH_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, perform_search), CallbackQueryHandler(user_router)],
            ADM_DEL_SELECT: [CallbackQueryHandler(admin_del_process, pattern="^del_"), CallbackQueryHandler(admin_confirm_delete, pattern="^confirm_del_"), CallbackQueryHandler(admin_del_menu, pattern="^a_back$"), CallbackQueryHandler(admin_del_menu, pattern="^a_del$")],
        },
        fallbacks=global_handlers,
        allow_reentry=True
    )
    app.add_handler(conv)
    app.add_error_handler(error_handler)
    Thread(target=lambda: server.run(host='0.0.0.0', port=PORT)).start()
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
