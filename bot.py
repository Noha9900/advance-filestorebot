import os, asyncio, secrets
from datetime import datetime
from flask import Flask
from threading import Thread
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from motor.motor_asyncio import AsyncIOMotorClient

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

# --- STATES ---
(W_TXT, W_PHO, AD_PHO, AD_TXT, AD_LNK, 
 ANI_NA, ANI_ME, ANI_DE, ANI_LI, 
 MOV_NA, MOV_ME, MOV_DE, MOV_LI,
 V_NA, V_ME, V_DE, V_KEY_IN) = range(17)

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
        [InlineKeyboardButton("Secret Vault 🔒", callback_data="u_vault_list")]
    ]
    markup = InlineKeyboardMarkup(kb)
    
    # Direct Welcome Feature
    if update.message:
        if w.get("photo"):
            msg = await update.message.reply_photo(w["photo"], caption=w["text"], reply_markup=markup)
        else:
            msg = await update.message.reply_text(w["text"], reply_markup=markup)
        context.job_queue.run_once(del_msg, 30, data=msg.message_id, chat_id=update.effective_chat.id)
    else:
        # For 'Back' buttons
        try:
            await update.callback_query.edit_message_text(w["text"], reply_markup=markup)
        except:
            await update.callback_query.message.reply_text(w["text"], reply_markup=markup)

async def user_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "u_ad":
        _, ad = await get_settings()
        kb = [[InlineKeyboardButton(c["name"], url=c["link"])] for c in ad["channels"]]
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="main")])
        if ad.get("photo"):
            await query.message.reply_photo(ad["photo"], caption=ad["text"], reply_markup=InlineKeyboardMarkup(kb))
        else:
            await query.edit_message_text(ad["text"], reply_markup=InlineKeyboardMarkup(kb))

    elif "u_list_" in query.data:
        g_type = query.data.split("_")[-1]
        items = await col_guides.find({"type": g_type}).to_list(length=100)
        txt = f"📖 **{g_type.upper()} LIST**\nSelect a number:\n\n"
        for i, item in enumerate(items, 1):
            txt += f"{i}. {item['name']}\n"
        if not items: txt += "No items added yet."
        await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main")]]))

    elif query.data == "u_vault_list":
        await query.edit_message_text("🔐 **VAULT LOCKED**\nEnter your 12-digit secret key:")
        return V_KEY_IN

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
    if query.data == "a_w":
        await query.edit_message_text("Send Welcome Text:")
        return W_TXT
    if query.data == "a_ad":
        await query.edit_message_text("Adult Setup: Send Photo (or /skip):")
        return AD_PHO
    if query.data == "a_ani":
        context.user_data["p"] = "anime"
        await query.edit_message_text("Anime Name:")
        return ANI_NA
    if query.data == "a_mov":
        context.user_data["p"] = "movies"
        await query.edit_message_text("Movie Name:")
        return MOV_NA
    if query.data == "a_v":
        await query.edit_message_text("Vault Item Name:")
        return V_NA

# --- SAVE LOGICS (FIXED) ---
async def save_w_txt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["w_tmp_txt"] = update.message.text
    await update.message.reply_text("Send Photo (or /skip):")
    return W_PHO

async def save_w_pho(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1].file_id if update.message.photo else None
    await col_settings.update_one({"type": "welcome"}, {"$set": {"text": context.user_data["w_tmp_txt"], "photo": photo}}, upsert=True)
    await update.message.reply_text("✅ Welcome Message Updated!")
    return ConversationHandler.END

async def save_guide_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["g_tmp"] = {"name": update.message.text}
    await update.message.reply_text("Send Media (Photo/Video):")
    return ANI_ME if context.user_data["p"] == "anime" else MOV_ME

async def save_guide_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fid = update.message.photo[-1].file_id if update.message.photo else update.message.video.file_id
    context.user_data["g_tmp"]["file"] = fid
    await update.message.reply_text("Send Description:")
    return ANI_DE if context.user_data["p"] == "anime" else MOV_DE

async def save_guide_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["g_tmp"]["desc"] = update.message.text
    await update.message.reply_text("Send Final Link:")
    return ANI_LI if context.user_data["p"] == "anime" else MOV_LI

async def save_guide_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data["g_tmp"]
    data["link"] = update.message.text
    data["type"] = context.user_data["p"]
    await col_guides.insert_one(data)
    await update.message.reply_text(f"✅ Success! Added to {data['type']}.")
    return ConversationHandler.END

async def save_vault_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = "".join([str(secrets.randbelow(10)) for _ in range(12)])
    data = context.user_data["v_tmp"]
    data["desc"] = update.message.text
    data["key"] = key
    await col_vaults.insert_one(data)
    await update.message.reply_text(f"✅ Vault Created!\n🔑 **Key:** `{key}`")
    return ConversationHandler.END

# --- FLASK ---
server = Flask(__name__)
@server.route('/')
def h(): return "Bot Online"

def main():
    app = Application.builder().token(TOKEN).build()
    
    # 
    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_router, pattern="^a_"),
            CallbackQueryHandler(user_router, pattern="u_vault_list")
        ],
        states={
            W_TXT: [MessageHandler(filters.TEXT, save_w_txt)],
            W_PHO: [MessageHandler(filters.PHOTO | filters.COMMAND, save_w_pho)],
            ANI_NA: [MessageHandler(filters.TEXT, save_guide_name)],
            ANI_ME: [MessageHandler(filters.PHOTO | filters.VIDEO, save_guide_media)],
            ANI_DE: [MessageHandler(filters.TEXT, save_guide_desc)],
            ANI_LI: [MessageHandler(filters.TEXT, save_guide_final)],
            MOV_NA: [MessageHandler(filters.TEXT, save_guide_name)],
            MOV_ME: [MessageHandler(filters.PHOTO | filters.VIDEO, save_guide_media)],
            MOV_DE: [MessageHandler(filters.TEXT, save_guide_desc)],
            MOV_LI: [MessageHandler(filters.TEXT, save_guide_final)],
            V_NA: [MessageHandler(filters.TEXT, lambda u,c: V_ME)],
            V_DE: [MessageHandler(filters.TEXT, save_vault_finish)],
            AD_PHO: [MessageHandler(filters.PHOTO | filters.COMMAND, adult_photo)],
            AD_TXT: [MessageHandler(filters.TEXT, adult_text)],
            AD_LNK: [MessageHandler(filters.TEXT, adult_final)],
            V_KEY_IN: [MessageHandler(filters.TEXT, vault_key_check)]
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(user_router, pattern="^u_"))
    app.add_handler(CallbackQueryHandler(start, pattern="main"))

    Thread(target=lambda: server.run(host='0.0.0.0', port=PORT)).start()
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
