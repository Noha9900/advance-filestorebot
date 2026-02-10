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

# --- LOGGING SETUP ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIG ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
MONGO_URL = os.getenv("MONGO_URL")
PORT = int(os.getenv("PORT", "8080"))

# --- DATABASE SETUP ---
client = AsyncIOMotorClient(MONGO_URL)
db = client["vault_bot_db"]
col_settings = db["settings"]
col_guides = db["guides"]
col_vaults = db["vaults"]

# Create an index for the vault keys to ensure lightning-fast access
async def init_db():
    await col_vaults.create_index("key", unique=True)
    logger.info("Database Indexes Initialized.")

# --- STATES ---
(W_TXT, W_PHO, AD_PHO, AD_TXT, AD_LNK, 
 ANI_NA, ANI_ME, ANI_DE, ANI_LI, 
 MOV_NA, MOV_ME, MOV_DE, MOV_LI,
 A_V_FOLD, A_V_SUB, A_V_POST, A_V_DESC, A_V_FILES, 
 V_KEY_INPUT, U_GUIDE_SELECT) = range(20)

# --- UTILS ---
async def del_msg(context: ContextTypes.DEFAULT_TYPE):
    try: await context.bot.delete_message(chat_id=context.job.chat_id, message_id=context.job.data)
    except: pass

async def get_settings():
    w = await col_settings.find_one({"type": "welcome"}) or {"text": "Welcome!", "photo": None}
    a = await col_settings.find_one({"type": "adult"}) or {"text": "Adult Zone", "photo": None, "channels": []}
    return w, a

# --- USER INTERFACE ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    w, _ = await get_settings()
    kb = [
        [InlineKeyboardButton("Adult Stream 🔥", callback_data="u_ad")],
        [InlineKeyboardButton("Anime Guide 🎌", callback_data="u_list_anime"), 
         InlineKeyboardButton("Movie Guide 🎬", callback_data="u_list_movies")],
        [InlineKeyboardButton("Secret Vault 🔒", callback_data="u_vault_folders")]
    ]
    markup = InlineKeyboardMarkup(kb)
    if update.message:
        if w.get("photo"):
            msg = await update.message.reply_photo(w["photo"], caption=w["text"], reply_markup=markup)
        else:
            msg = await update.message.reply_text(w["text"], reply_markup=markup)
        context.job_queue.run_once(del_msg, 30, data=msg.message_id, chat_id=update.effective_chat.id)
    else:
        try: await update.callback_query.edit_message_text(w["text"], reply_markup=markup)
        except: await update.callback_query.message.reply_text(w["text"], reply_markup=markup)

async def user_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "u_ad":
        _, ad = await get_settings()
        kb = [[InlineKeyboardButton(c["name"], url=c["link"])] for c in ad["channels"]]
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="main")])
        if ad.get("photo"): await query.message.reply_photo(ad["photo"], caption=ad["text"], reply_markup=InlineKeyboardMarkup(kb))
        else: await query.edit_message_text(ad["text"], reply_markup=InlineKeyboardMarkup(kb))

    elif "u_list_" in query.data:
        g_type = query.data.split("_")[-1]
        items = await col_guides.find({"type": g_type}).to_list(length=100)
        txt = f"📖 **{g_type.upper()} LIST**\nReply with the number to get details:\n\n"
        for i, item in enumerate(items, 1): txt += f"{i}. {item['name']}\n"
        context.user_data["view_type"] = g_type
        await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main")]]))
        return U_GUIDE_SELECT

    elif query.data == "u_vault_folders":
        folders = await col_vaults.distinct("folder")
        if not folders:
            await query.edit_message_text("Vault is empty.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main")]]))
            return
        kb = [[InlineKeyboardButton(f, callback_data=f"v_fold_{f}")] for f in folders]
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="main")])
        await query.edit_message_text("📂 Select a Folder:", reply_markup=InlineKeyboardMarkup(kb))

    elif query.data.startswith("v_fold_"):
        folder_name = query.data.replace("v_fold_", "")
        items = await col_vaults.find({"folder": folder_name}).to_list(length=100)
        txt = f"📁 **{folder_name}**\nSelect a sub-entry:\n"
        kb = [[InlineKeyboardButton(i["sub_name"], callback_data=f"v_sub_{str(i['_id'])}")] for i in items]
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="u_vault_folders")])
        await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))

    elif query.data.startswith("v_sub_"):
        context.user_data["v_target_id"] = query.data.replace("v_sub_", "")
        await query.edit_message_text("🔐 Enter the 12-digit Special Key to unlock files:")
        return V_KEY_INPUT

# --- ADMIN PANEL ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    kb = [
        [InlineKeyboardButton("Set Welcome", callback_data="a_w"), InlineKeyboardButton("Set Adult", callback_data="a_ad")],
        [InlineKeyboardButton("Add Anime", callback_data="a_ani"), InlineKeyboardButton("Add Movie", callback_data="a_mov")],
        [InlineKeyboardButton("Create Vault Content 🔒", callback_data="a_v")]
    ]
    await update.message.reply_text("🛠 **ADMIN PANEL**", reply_markup=InlineKeyboardMarkup(kb))

async def admin_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "a_w": await query.edit_message_text("Send Welcome Text:"); return W_TXT
    if query.data == "a_ad": await query.edit_message_text("Adult: Send Photo (or /skip):"); return AD_PHO
    if query.data == "a_ani": context.user_data["p"]="anime"; await query.edit_message_text("Anime Name:"); return ANI_NA
    if query.data == "a_mov": context.user_data["p"]="movies"; await query.edit_message_text("Movie Name:"); return MOV_NA
    if query.data == "a_v": await query.edit_message_text("📂 Enter Folder Name:"); return A_V_FOLD

# --- DYNAMIC SAVE LOGICS (WELCOME, GUIDES, ADULT) ---
async def save_w_txt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["wt"] = update.message.text
    await update.message.reply_text("Send Photo (or /skip):")
    return W_PHO

async def save_w_pho(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fid = update.message.photo[-1].file_id if update.message.photo else None
    await col_settings.update_one({"type": "welcome"}, {"$set": {"text": context.user_data["wt"], "photo": fid}}, upsert=True)
    await update.message.reply_text("✅ Welcome Set!")
    return ConversationHandler.END

async def save_g_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["gtmp"] = {"name": update.message.text, "type": context.user_data["p"]}
    await update.message.reply_text("Send Photo/Video Media:")
    return ANI_ME if context.user_data["p"] == "anime" else MOV_ME

async def save_g_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fid = update.message.photo[-1].file_id if update.message.photo else update.message.video.file_id
    context.user_data["gtmp"]["file"] = fid
    await update.message.reply_text("Send Description:")
    return ANI_DE if context.user_data["p"] == "anime" else MOV_DE

async def save_g_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["gtmp"]["desc"] = update.message.text
    await update.message.reply_text("Send Final Link:")
    return ANI_LI if context.user_data["p"] == "anime" else MOV_LI

async def save_g_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["gtmp"]["link"] = update.message.text
    await col_guides.insert_one(context.user_data["gtmp"])
    await update.message.reply_text(f"✅ Guide Added to {context.user_data['p']}!")
    return ConversationHandler.END

async def save_ad_pho(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fid = update.message.photo[-1].file_id if update.message.photo else None
    context.user_data["ad_tmp"] = {"photo": fid}
    await update.message.reply_text("Send Adult Welcome Text:")
    return AD_TXT

async def save_ad_txt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["ad_tmp"]["text"] = update.message.text
    await update.message.reply_text("Send Channel (Format: Name | Link):")
    return AD_LNK

async def save_ad_lnk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.split("|")
    if len(parts) == 2:
        await col_settings.update_one({"type": "adult"}, {"$set": {"photo": context.user_data["ad_tmp"]["photo"], "text": context.user_data["ad_tmp"]["text"]}, "$push": {"channels": {"name": parts[0].strip(), "link": parts[1].strip()}}}, upsert=True)
        await update.message.reply_text("✅ Updated!")
    return ConversationHandler.END

# --- VAULT ADMIN LOGIC ---
async def v_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["v_data"] = {"folder": update.message.text, "files": []}
    await update.message.reply_text("📝 Enter Sub-Name:")
    return A_V_SUB

async def v_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["v_data"]["sub_name"] = update.message.text
    await update.message.reply_text("🖼 Send the Poster Media (Photo/Video):")
    return A_V_POST

async def v_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["v_data"]["poster"] = update.message.photo[-1].file_id if update.message.photo else update.message.video.file_id
    await update.message.reply_text("✍️ Send Description for this entry:")
    return A_V_DESC

async def v_files_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["v_data"]["desc"] = update.message.text
    await update.message.reply_text("📎 Send Multiple Files. Use /done when finished.")
    return A_V_FILES

async def v_collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    f_id = update.message.document.file_id if update.message.document else (update.message.video.file_id if update.message.video else update.message.photo[-1].file_id)
    context.user_data["v_data"]["files"].append(f_id)
    return A_V_FILES

async def v_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%^&*"
    complex_key = "".join(secrets.choice(chars) for _ in range(12))
    data = context.user_data["v_data"]
    data["key"] = complex_key
    await col_vaults.insert_one(data)
    await update.message.reply_text(f"✅ **Vault Saved!**\n🗝 Key: `{complex_key}`")
    return ConversationHandler.END

# --- USER: ACCESS LOGICS ---
async def show_guide_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        idx = int(update.message.text) - 1
        g_type = context.user_data.get("view_type")
        items = await col_guides.find({"type": g_type}).to_list(length=100)
        if 0 <= idx < len(items):
            target = items[idx]
            await update.message.reply_photo(target["file"], caption=f"⭐ **{target['name']}**\n\n{target['desc']}\n\n🔗 Link: {target['link']}")
        else: await update.message.reply_text("❌ Invalid selection.")
    except: await update.message.reply_text("❓ Please send a number from the list.")
    return ConversationHandler.END

async def vault_key_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = await col_vaults.find_one({"_id": ObjectId(context.user_data["v_target_id"])})
    if target and update.message.text == target["key"]:
        await update.message.reply_photo(target["poster"], caption=f"🔓 **UNLOCKED**\n\n{target['desc']}")
        for f in target["files"]:
            msg = await update.message.reply_document(f) if "document" in str(f) else await update.message.reply_video(f)
            context.job_queue.run_once(del_msg, 1800, data=msg.message_id, chat_id=update.effective_chat.id)
    else: await update.message.reply_text("❌ Access Denied: Wrong Key.")
    return ConversationHandler.END

# --- ERROR HANDLER ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Exception while handling an update: {context.error}")

# --- SERVER & MAIN ---
server = Flask(__name__)
@server.route('/')
def h(): return "Bot Active"

def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init_db())

    app = Application.builder().token(TOKEN).build()
    
    

    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_router, pattern="^a_"),
            CallbackQueryHandler(user_router, pattern="^u_"),
            CallbackQueryHandler(user_router, pattern="^v_")
        ],
        states={
            W_TXT: [MessageHandler(filters.TEXT, save_w_txt)],
            W_PHO: [MessageHandler(filters.PHOTO | filters.COMMAND, save_w_pho)],
            ANI_NA: [MessageHandler(filters.TEXT, save_g_name)],
            ANI_ME: [MessageHandler(filters.PHOTO | filters.VIDEO, save_g_media)],
            ANI_DE: [MessageHandler(filters.TEXT, save_g_desc)],
            ANI_LI: [MessageHandler(filters.TEXT, save_g_final)],
            MOV_NA: [MessageHandler(filters.TEXT, save_g_name)],
            MOV_ME: [MessageHandler(filters.PHOTO | filters.VIDEO, save_g_media)],
            MOV_DE: [MessageHandler(filters.TEXT, save_g_desc)],
            MOV_LI: [MessageHandler(filters.TEXT, save_g_final)],
            A_V_FOLD: [MessageHandler(filters.TEXT, v_sub)],
            A_V_SUB: [MessageHandler(filters.TEXT, v_post)],
            A_V_POST: [MessageHandler(filters.PHOTO | filters.VIDEO, v_desc)],
            A_V_DESC: [MessageHandler(filters.TEXT, v_files_start)],
            A_V_FILES: [MessageHandler(filters.ALL & ~filters.COMMAND, v_collect), CommandHandler("done", v_done)],
            AD_PHO: [MessageHandler(filters.PHOTO | filters.COMMAND, save_ad_pho)],
            AD_TXT: [MessageHandler(filters.TEXT, save_ad_txt)],
            AD_LNK: [MessageHandler(filters.TEXT, save_ad_lnk)],
            V_KEY_INPUT: [MessageHandler(filters.TEXT, vault_key_check)],
            U_GUIDE_SELECT: [MessageHandler(filters.Regex(r'^\d+$'), show_guide_content)]
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(start, pattern="main"))
    app.add_error_handler(error_handler)

    Thread(target=lambda: server.run(host='0.0.0.0', port=PORT, threaded=True)).start()
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
