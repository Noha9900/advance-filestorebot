import os, asyncio, secrets, logging
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

# Production DB Connection
client = AsyncIOMotorClient(
    MONGO_URL, 
    maxPoolSize=10, 
    minPoolSize=1, 
    serverSelectionTimeoutMS=5000,
    tlsCAFile=certifi.where()
)
db = client["vault_bot_db"]
col_settings, col_guides, col_vaults = db["settings"], db["guides"], db["vaults"]

(W_TXT, W_PHO, AD_PHO_STATE, AD_TXT_STATE, AD_LNK_STATE, ANI_NA, ANI_ME, ANI_DE, ANI_LI, 
 MOV_NA, MOV_ME, MOV_DE, MOV_LI, A_V_FOLD, A_V_SUB, A_V_POST, A_V_DESC, 
 A_V_FILES, V_KEY_INPUT, U_GUIDE_SELECT, U_V_SUB_SELECT, ADM_DEL_SELECT) = range(22)

# --- SAFETY HELPER ---
def get_file_info(message):
    if message.animation: return message.animation.file_id, "animation"
    if message.video: return message.video.file_id, "video"
    if message.photo: return message.photo[-1].file_id, "photo"
    if message.document: return message.document.file_id, "document"
    return None, None

def get_fid(message):
    fid, _ = get_file_info(message)
    return fid

# --- UTILS ---
async def del_msg(context: ContextTypes.DEFAULT_TYPE):
    try: await context.bot.delete_message(chat_id=context.job.chat_id, message_id=context.job.data)
    except: pass

async def get_settings():
    w = await col_settings.find_one({"type": "welcome"}) or {"text": "Welcome!", "photo": None}
    a = await col_settings.find_one({"type": "adult"}) or {"text": "Adult Zone", "photo": None, "channels": []}
    return w, a

# --- USER SIDE ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear() 
    w, _ = await get_settings()
    kb = [[InlineKeyboardButton("Adult Stream 🔥", callback_data="u_ad_0")], 
          [InlineKeyboardButton("Anime Guide 🎌", callback_data="u_list_anime"), InlineKeyboardButton("Movie Guide 🎬", callback_data="u_list_movies")],
          [InlineKeyboardButton("Secret Vault 🔒", callback_data="u_vault_folders")]]
    markup = InlineKeyboardMarkup(kb)
    
    if update.message:
        if w.get("photo"):
            msg = await update.message.reply_photo(w["photo"], caption=w["text"], reply_markup=markup)
        else:
            msg = await update.message.reply_text(w["text"], reply_markup=markup)
        context.job_queue.run_once(del_msg, 60, data=msg.message_id, chat_id=update.effective_chat.id)
    else:
        # Check if we need to swap media back to welcome photo
        try:
            if w.get("photo"):
                if update.callback_query.message.photo:
                    # If it's already a photo, swap it (Smoother)
                    await update.callback_query.edit_message_media(
                        media=InputMediaPhoto(media=w["photo"], caption=w["text"]),
                        reply_markup=markup
                    )
                else:
                    # Text to Photo
                    await update.callback_query.message.delete()
                    await update.callback_query.message.reply_photo(w["photo"], caption=w["text"], reply_markup=markup)
            else:
                # Text only
                if update.callback_query.message.photo:
                    await update.callback_query.message.delete()
                    await update.callback_query.message.reply_text(w["text"], reply_markup=markup)
                else:
                    await update.callback_query.edit_message_text(w["text"], reply_markup=markup)
        except:
            # Absolute fallback
            try: await update.callback_query.message.delete()
            except: pass
            if w.get("photo"):
                await update.callback_query.message.reply_photo(w["photo"], caption=w["text"], reply_markup=markup)
            else:
                await update.callback_query.message.reply_text(w["text"], reply_markup=markup)

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled. Type /start.")
    return ConversationHandler.END

async def user_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "main": return await start(update, context)

    # --- ADULT STREAM (FIXED MEDIA SWAP) ---
    if query.data.startswith("u_ad"):
        page = int(query.data.split("_")[-1]) if "_" in query.data else 0
        _, ad = await get_settings()
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
        
        # LOGIC FIX: Explicitly swap media using InputMediaPhoto
        try:
            if ad.get("photo"):
                if query.message.photo:
                    # Photo -> New Photo (Swap)
                    await query.edit_message_media(
                        media=InputMediaPhoto(media=ad["photo"], caption=ad["text"]),
                        reply_markup=markup
                    )
                else:
                    # Text -> Photo (Delete & Send)
                    await query.message.delete()
                    await query.message.reply_photo(ad["photo"], caption=ad["text"], reply_markup=markup)
            else:
                # Target is Text
                if query.message.photo:
                    # Photo -> Text (Delete & Send)
                    await query.message.delete()
                    await query.message.reply_text(ad["text"], reply_markup=markup)
                else:
                    # Text -> Text (Edit)
                    await query.edit_message_text(ad["text"], reply_markup=markup)
        except Exception as e:
            # Fallback for any API errors
            await query.message.delete()
            if ad.get("photo"):
                await query.message.reply_photo(ad["photo"], caption=ad["text"], reply_markup=markup)
            else:
                await query.message.reply_text(ad["text"], reply_markup=markup)
                
        return ConversationHandler.END

    # --- LISTS (ANIME/MOVIE) ---
    elif "u_list_" in query.data:
        g_type = query.data.split("_")[-1]
        items = await col_guides.find({"type": g_type}).sort("_id", 1).to_list(100)
        
        txt = f"📖 <b>{g_type.upper()} LIST</b>\n\n"
        if not items: txt += "No content added yet."
        else: txt += "Reply with the <b>Number</b> to watch:\n\n"
        for i, x in enumerate(items): txt += f"{i+1}. {x['name']}\n"
        
        context.user_data["view_type"] = g_type
        await query.message.delete()
        await query.message.reply_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main")]]))
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

# --- ADMIN PANEL ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    
    kb = [[InlineKeyboardButton("Set Welcome", callback_data="a_w"), InlineKeyboardButton("Set Adult", callback_data="a_ad")],
          [InlineKeyboardButton("Add Anime", callback_data="a_ani"), InlineKeyboardButton("Add Movie", callback_data="a_mov")],
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
    if query.data == "a_back": return await admin_panel(update, context)

# --- SAVING LOGICS ---
async def save_w_txt(update, context):
    context.user_data["wt"] = update.message.text
    await update.message.reply_text("Send Photo (or /skip):"); return W_PHO

async def save_w_pho(update, context):
    fid = get_fid(update.message)
    await col_settings.update_one({"type": "welcome"}, {"$set": {"text": context.user_data["wt"], "photo": fid}}, upsert=True)
    await update.message.reply_text("✅ Welcome Set!"); return ConversationHandler.END

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
    await update.message.reply_text("Send Watch Link:"); return ANI_LI if context.user_data["p"]=="anime" else MOV_LI

async def save_g_final(update, context):
    context.user_data["gtmp"]["link"] = update.message.text
    await col_guides.insert_one(context.user_data["gtmp"])
    await update.message.reply_text("✅ Added!"); return ConversationHandler.END

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
        # SHOW PREVIEW TO ADMIN
        await update.message.reply_text("✅ <b>Saved! Preview:</b>")
        if context.user_data["ad_tmp"]["photo"]:
            await update.message.reply_photo(context.user_data["ad_tmp"]["photo"], caption=context.user_data["ad_tmp"]["text"])
        else:
            await update.message.reply_text(context.user_data["ad_tmp"]["text"])
        await update.message.reply_text("Send next (Name | Link) or /start to finish.")
        return AD_LNK_STATE
    except: await update.message.reply_text("Err: Name | Link"); return AD_LNK_STATE

# --- CONTENT DELIVERY ---
async def vault_select_sub(update, context):
    try:
        idx = int(update.message.text) - 1
        items = await col_vaults.find({"folder": context.user_data.get("active_vault_folder")}).sort("_id", 1).to_list(100)
        
        if 0 <= idx < len(items):
            item = items[idx]
            context.user_data["target_v"] = item["_id"]
            if item.get("poster"):
                await update.message.reply_photo(item["poster"], caption=f"📁 <b>{item['sub_name']}</b>\n\n{item['desc']}\n\n🔐 <b>Enter Key:</b>")
            else:
                await update.message.reply_text(f"📁 <b>{item['sub_name']}</b>\n\n{item['desc']}\n\n🔐 <b>Enter Key:</b>")
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
                
                context.job_queue.run_once(del_msg, 1800, data=msg.message_id, chat_id=update.effective_chat.id)
            except Exception:
                try: 
                    msg = await update.message.reply_document(fid)
                    context.job_queue.run_once(del_msg, 1800, data=msg.message_id, chat_id=update.effective_chat.id)
                except: pass 
                
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=status_msg.message_id)
        await update.message.reply_text("✅ All files sent! They will disappear in 30 mins.")
    else: await update.message.reply_text("❌ Wrong Key")
    return ConversationHandler.END

async def guide_show(update, context):
    try:
        idx = int(update.message.text) - 1
        items = await col_guides.find({"type": context.user_data.get("view_type")}).sort("_id", 1).to_list(100)
        
        if 0 <= idx < len(items):
            item = items[idx]
            caption = f"⭐ <b>{item['name']}</b>\n\n{item['desc']}\n\n🔗 Watch: {item['link']}"
            mtype = item.get("media_type", "photo") 
            
            try:
                if mtype == "video": await update.message.reply_video(item["file"], caption=caption)
                elif mtype == "animation": await update.message.reply_animation(item["file"], caption=caption)
                elif mtype == "document": await update.message.reply_document(item["file"], caption=caption)
                else: await update.message.reply_photo(item["file"], caption=caption)
            except:
                await update.message.reply_document(item["file"], caption=caption)
        else: 
            await update.message.reply_text(f"❌ Invalid Number. 1-{len(items)}")
    except ValueError: 
        await update.message.reply_text("❌ Send a Number.")
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
        _, ad = await get_settings()
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
    
    async def init(): await col_vaults.create_index("key", unique=True)
    asyncio.get_event_loop().run_until_complete(init())

    global_handlers = [
        CommandHandler("start", start),
        CommandHandler("admin", admin_panel),
        CommandHandler("cancel", cancel),
        CallbackQueryHandler(start, pattern="^main$"),
        CallbackQueryHandler(admin_panel, pattern="^a_panel_back$"),
        CallbackQueryHandler(user_router, pattern="^u_"),
        CallbackQueryHandler(user_router, pattern="^vfold_"),
        CallbackQueryHandler(admin_router, pattern="^a_")
    ]

    conv = ConversationHandler(
        entry_points=global_handlers,
        states={
            W_TXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_w_txt)], 
            W_PHO: [MessageHandler((filters.PHOTO | filters.Regex("/skip")) & ~filters.COMMAND, save_w_pho)],
            ANI_NA: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_g_name)], 
            ANI_ME: [MessageHandler((filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.Document.ALL) & ~filters.COMMAND, save_g_media)], 
            ANI_DE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_g_desc)], ANI_LI: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_g_final)],
            MOV_NA: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_g_name)], 
            MOV_ME: [MessageHandler((filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.Document.ALL) & ~filters.COMMAND, save_g_media)], 
            MOV_DE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_g_desc)], MOV_LI: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_g_final)],
            A_V_FOLD: [MessageHandler(filters.TEXT & ~filters.COMMAND, v_sub)], A_V_SUB: [MessageHandler(filters.TEXT & ~filters.COMMAND, v_post)], A_V_POST: [MessageHandler((filters.PHOTO | filters.VIDEO | filters.Document.ALL) & ~filters.COMMAND, v_desc)], A_V_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, v_files_start)], 
            A_V_FILES: [CommandHandler("done", v_collect), MessageHandler(filters.ALL & ~filters.COMMAND, v_collect)],
            AD_PHO_STATE: [MessageHandler((filters.PHOTO | filters.Regex("/skip")) & ~filters.COMMAND, ad_pho_fn)], AD_TXT_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ad_txt_fn)], AD_LNK_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ad_lnk_fn)],
            U_GUIDE_SELECT: [MessageHandler(filters.Regex(r'^\s*\d+\s*$'), guide_show)], 
            U_V_SUB_SELECT: [MessageHandler(filters.Regex(r'^\s*\d+\s*$'), vault_select_sub)],
            V_KEY_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, vault_key_check)], 
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
