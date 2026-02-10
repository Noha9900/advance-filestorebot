import os, asyncio, secrets, logging
from datetime import datetime
from flask import Flask
from threading import Thread
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
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

# Production DB Connection with Keep-Alive
client = AsyncIOMotorClient(MONGO_URL, maxPoolSize=10, minPoolSize=1, serverSelectionTimeoutMS=5000)
db = client["vault_bot_db"]
col_settings, col_guides, col_vaults = db["settings"], db["guides"], db["vaults"]

(W_TXT, W_PHO, AD_PHO_STATE, AD_TXT_STATE, AD_LNK_STATE, ANI_NA, ANI_ME, ANI_DE, ANI_LI, 
 MOV_NA, MOV_ME, MOV_DE, MOV_LI, A_V_FOLD, A_V_SUB, A_V_POST, A_V_DESC, 
 A_V_FILES, V_KEY_INPUT, U_GUIDE_SELECT, U_V_SUB_SELECT, ADM_DEL_SELECT) = range(22)

# --- SAFETY HELPER ---
def get_fid(message):
    if message.photo: return message.photo[-1].file_id
    if message.video: return message.video.file_id
    if message.document: return message.document.file_id
    return None

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
    kb = [[InlineKeyboardButton("Adult Stream 🔥", callback_data="u_ad")],
          [InlineKeyboardButton("Anime Guide 🎌", callback_data="u_list_anime"), InlineKeyboardButton("Movie Guide 🎬", callback_data="u_list_movies")],
          [InlineKeyboardButton("Secret Vault 🔒", callback_data="u_vault_folders")]]
    markup = InlineKeyboardMarkup(kb)
    if update.message:
        msg = await (update.message.reply_photo(w["photo"], caption=w["text"], reply_markup=markup) if w.get("photo") else update.message.reply_text(w["text"], reply_markup=markup))
        context.job_queue.run_once(del_msg, 30, data=msg.message_id, chat_id=update.effective_chat.id)
    else:
        try: await update.callback_query.edit_message_text(w["text"], reply_markup=markup)
        except: await update.callback_query.message.reply_text(w["text"], reply_markup=markup)
    return ConversationHandler.END

async def user_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "main": return await start(update, context)
    
    if query.data == "u_ad":
        _, ad = await get_settings()
        kb = [[InlineKeyboardButton(c["name"], url=c["link"])] for c in ad.get("channels", [])]
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="main")])
        if ad.get("photo"): await query.message.reply_photo(ad["photo"], caption=ad["text"], reply_markup=InlineKeyboardMarkup(kb))
        else: await query.edit_message_text(ad["text"], reply_markup=InlineKeyboardMarkup(kb))
        return ConversationHandler.END

    elif "u_list_" in query.data:
        g_type = query.data.split("_")[-1]
        items = await col_guides.find({"type": g_type}).to_list(100)
        txt = f"📖 **{g_type.upper()} LIST**\nReply with Number to view:\n\n" + "\n".join([f"{i+1}. {x['name']}" for i, x in enumerate(items)])
        context.user_data["view_type"] = g_type
        await query.edit_message_text(txt if items else "List is empty.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main")]]))
        return U_GUIDE_SELECT

    elif query.data == "u_vault_folders":
        folders = await col_vaults.distinct("folder")
        btns = [InlineKeyboardButton(f, callback_data=f"vfold_{f}") for f in folders]
        # Admin request: Buttons side-by-side (2 per row)
        kb = [btns[i:i + 2] for i in range(0, len(btns), 2)]
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="main")])
        await query.edit_message_text("📂 Folders:", reply_markup=InlineKeyboardMarkup(kb))
        return ConversationHandler.END

    elif query.data.startswith("vfold_"):
        fname = query.data.replace("vfold_", "")
        items = await col_vaults.find({"folder": fname}).to_list(100)
        txt = f"📁 **{fname}**\nReply with Number to see album:\n\n" + "\n".join([f"{i+1}. {x['sub_name']}" for i, x in enumerate(items)])
        context.user_data["active_vault_folder"] = fname
        await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="u_vault_folders")]]))
        return U_V_SUB_SELECT

# --- ADMIN PANEL ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    kb = [[InlineKeyboardButton("Set Welcome", callback_data="a_w"), InlineKeyboardButton("Set Adult", callback_data="a_ad")],
          [InlineKeyboardButton("Add Anime", callback_data="a_ani"), InlineKeyboardButton("Add Movie", callback_data="a_mov")],
          [InlineKeyboardButton("Create Vault Content 🔒", callback_data="a_v")],
          [InlineKeyboardButton("🗑 Delete Mode", callback_data="a_del")]]
    await update.message.reply_text("🛠 **ADMIN PANEL**", reply_markup=InlineKeyboardMarkup(kb))
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
    if query.data == "a_panel_back": return await admin_panel(update, context)

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
    await update.message.reply_text("Send Media (Photo/Video):"); return ANI_ME if context.user_data["p"]=="anime" else MOV_ME

async def save_g_media(update, context):
    fid = get_fid(update.message)
    if not fid: await update.message.reply_text("❌ Send Photo/Video:"); return
    context.user_data["gtmp"]["file"] = fid
    await update.message.reply_text("Send Description:"); return ANI_DE if context.user_data["p"]=="anime" else MOV_DE

async def save_g_desc(update, context):
    context.user_data["gtmp"]["desc"] = update.message.text
    await update.message.reply_text("Send Link:"); return ANI_LI if context.user_data["p"]=="anime" else MOV_LI

async def save_g_final(update, context):
    context.user_data["gtmp"]["link"] = update.message.text
    await col_guides.insert_one(context.user_data["gtmp"])
    await update.message.reply_text("✅ Added!"); return ConversationHandler.END

async def v_sub(update, context):
    context.user_data["v_data"] = {"folder": update.message.text, "files": []}
    await update.message.reply_text("📝 Sub-Name:"); return A_V_SUB

async def v_post(update, context):
    context.user_data["v_data"]["sub_name"] = update.message.text
    await update.message.reply_text("🖼 Send Poster:"); return A_V_POST

async def v_desc(update, context):
    fid = get_fid(update.message)
    if not fid: await update.message.reply_text("❌ Send Media:"); return
    context.user_data["v_data"]["poster"] = fid
    await update.message.reply_text("✍️ Description:"); return A_V_DESC

async def v_files_start(update, context):
    context.user_data["v_data"]["desc"] = update.message.text
    await update.message.reply_text("📎 Send Files. Send /done when finished:"); return A_V_FILES

async def v_collect(update, context):
    if update.message.text and update.message.text.lower() == "/done":
        key = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%^&*") for _ in range(12))
        context.user_data["v_data"]["key"] = key
        await col_vaults.insert_one(context.user_data["v_data"])
        await update.message.reply_text(f"✅ Saved! Key: `{key}`"); return ConversationHandler.END
    fid = get_fid(update.message)
    if fid: context.user_data["v_data"]["files"].append(fid)
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
        await update.message.reply_text("✅ Added! Send next (Name | Link) or /start to finish."); return AD_LNK_STATE
    except: await update.message.reply_text("Err: Name | Link"); return AD_LNK_STATE

# --- ACCESS ---
async def vault_select_sub(update, context):
    try:
        idx = int(update.message.text) - 1
        items = await col_vaults.find({"folder": context.user_data["active_vault_folder"]}).to_list(100)
        if 0 <= idx < len(items):
            item = items[idx]
            context.user_data["target_v"] = item["_id"]
            await update.message.reply_photo(item["poster"], caption=f"📁 **{item['sub_name']}**\n\n{item['desc']}\n\n🔐 **Enter Key:**")
            return V_KEY_INPUT
        else: await update.message.reply_text(f"❌ Enter 1-{len(items)}")
    except: await update.message.reply_text("Invalid Input")
    return U_V_SUB_SELECT

async def vault_key_check(update, context):
    v = await col_vaults.find_one({"_id": ObjectId(context.user_data["target_v"])})
    if v and update.message.text == v["key"]:
        await update.message.reply_text("🔓 Unlocked! Files delete in 30 mins.")
        for f in v["files"]:
            msg = await update.message.reply_document(f) if "document" in str(f) else await update.message.reply_video(f)
            context.job_queue.run_once(del_msg, 1800, data=msg.message_id, chat_id=update.effective_chat.id)
    else: await update.message.reply_text("❌ Wrong Key")
    return ConversationHandler.END

async def guide_show(update, context):
    try:
        idx = int(update.message.text) - 1
        items = await col_guides.find({"type": context.user_data["view_type"]}).to_list(100)
        if 0 <= idx < len(items):
            item = items[idx]
            await update.message.reply_photo(item["file"], caption=f"⭐ **{item['name']}**\n\n{item['desc']}\n\n🔗 Watch: {item['link']}")
        else: await update.message.reply_text(f"❌ Enter 1-{len(items)}")
    except: await update.message.reply_text("Invalid Input")
    return U_GUIDE_SELECT

# --- DELETE & ADULT ---
async def admin_del_menu(update, context):
    kb = [[InlineKeyboardButton("Anime", callback_data="del_anime"), InlineKeyboardButton("Movie", callback_data="del_movies")],
          [InlineKeyboardButton("Vault", callback_data="del_vault"), InlineKeyboardButton("Adult Link", callback_data="del_adult")],
          [InlineKeyboardButton("🔙 Back to Admin", callback_data="a_panel_back")]]
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
    app = Application.builder().token(TOKEN).build()
    
    async def init(): await col_vaults.create_index("key", unique=True)
    asyncio.get_event_loop().run_until_complete(init())

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("admin", admin_panel),
            CallbackQueryHandler(admin_router, pattern="^a_"), 
            CallbackQueryHandler(user_router, pattern="^u_"), 
            CallbackQueryHandler(user_router, pattern="^vfold_"),
            CallbackQueryHandler(start, pattern="^main$"),
            CallbackQueryHandler(admin_panel, pattern="^a_panel_back$")
        ],
        states={
            W_TXT: [MessageHandler(filters.TEXT, save_w_txt)], W_PHO: [MessageHandler(filters.PHOTO | filters.Regex("/skip"), save_w_pho)],
            ANI_NA: [MessageHandler(filters.TEXT, save_g_name)], ANI_ME: [MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, save_g_media)], ANI_DE: [MessageHandler(filters.TEXT, save_g_desc)], ANI_LI: [MessageHandler(filters.TEXT, save_g_final)],
            MOV_NA: [MessageHandler(filters.TEXT, save_g_name)], MOV_ME: [MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, save_g_media)], MOV_DE: [MessageHandler(filters.TEXT, save_g_desc)], MOV_LI: [MessageHandler(filters.TEXT, save_g_final)],
            A_V_FOLD: [MessageHandler(filters.TEXT, v_sub)], A_V_SUB: [MessageHandler(filters.TEXT, v_post)], A_V_POST: [MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, v_desc)], A_V_DESC: [MessageHandler(filters.TEXT, v_files_start)], A_V_FILES: [MessageHandler(filters.ALL, v_collect)],
            AD_PHO_STATE: [MessageHandler(filters.PHOTO | filters.Regex("/skip"), ad_pho_fn)], AD_TXT_STATE: [MessageHandler(filters.TEXT, ad_txt_fn)], AD_LNK_STATE: [MessageHandler(filters.TEXT, ad_lnk_fn)],
            U_GUIDE_SELECT: [MessageHandler(filters.Regex(r'^\d+$'), guide_show)], 
            U_V_SUB_SELECT: [MessageHandler(filters.Regex(r'^\d+$'), vault_select_sub)],
            V_KEY_INPUT: [MessageHandler(filters.TEXT, vault_key_check)], 
            ADM_DEL_SELECT: [CallbackQueryHandler(admin_del_process, pattern="^del_"), CallbackQueryHandler(admin_confirm_delete, pattern="^confirm_del_"), CallbackQueryHandler(admin_del_menu, pattern="^a_del$")],
        },
        fallbacks=[CommandHandler("start", start), CallbackQueryHandler(start, pattern="main")], allow_reentry=True
    )
    app.add_handler(conv); app.add_handler(CommandHandler("start", start)); app.add_error_handler(error_handler)
    Thread(target=lambda: server.run(host='0.0.0.0', port=PORT)).start()
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
