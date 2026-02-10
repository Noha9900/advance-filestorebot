import os, asyncio, secrets
from datetime import datetime, timedelta
from flask import Flask
from threading import Thread
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# --- CONFIG ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PORT = int(os.getenv("PORT", "8080"))

# --- DB ---
db = {
    "welcome": {"photo": None, "text": "Welcome! 🔥"},
    "adult": {"photo": None, "text": "Adult Zone", "channels": []},
    "anime": [], "movies": [], "vaults": [] 
}

# --- STATES ---
(W_TXT, W_PHO, AD_PHO, AD_TXT, AD_LNK, 
 ANI_NA, ANI_ME, ANI_DE, ANI_LI,
 MOV_NA, MOV_ME, MOV_DE, MOV_LI,
 V_NA, V_ME, V_DE, V_FIN, V_KEY_CHECK) = range(18)

# --- UTILS ---
async def del_msg(context: ContextTypes.DEFAULT_TYPE):
    try: await context.bot.delete_message(chat_id=context.job.chat_id, message_id=context.job.data)
    except: pass

# --- USER UI ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    w = db["welcome"]
    kb = [
        [InlineKeyboardButton("Adult Stream 🔥", callback_data="u_ad")],
        [InlineKeyboardButton("Anime Guide 🎌", callback_data="u_ani"), InlineKeyboardButton("Movie Guide 🎬", callback_data="u_mov")],
        [InlineKeyboardButton("Secret Vault 🔒", callback_data="u_v")]
    ]
    markup = InlineKeyboardMarkup(kb)
    if update.message:
        if w["photo"]: msg = await update.message.reply_photo(w["photo"], caption=w["text"], reply_markup=markup)
        else: msg = await update.message.reply_text(w["text"], reply_markup=markup)
        context.job_queue.run_once(del_msg, 30, data=msg.message_id, chat_id=update.effective_chat.id)
    else:
        await update.callback_query.edit_message_text(w["text"], reply_markup=markup)

# --- ADMIN PANEL ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    kb = [
        [InlineKeyboardButton("Set Welcome", callback_data="a_w"), InlineKeyboardButton("Set Adult", callback_data="a_ad")],
        [InlineKeyboardButton("Add Anime", callback_data="a_ani"), InlineKeyboardButton("Add Movie", callback_data="a_mov")],
        [InlineKeyboardButton("Create Vault Content 🔒", callback_data="a_v")]
    ]
    await update.message.reply_text("🛠 **ADMIN PANEL**", reply_markup=InlineKeyboardMarkup(kb))

# --- ADMIN SAVE LOGIC (ANIME/MOVIES) ---
async def a_save_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["tmp"] = {"name": update.message.text}
    await update.message.reply_text("Send Media (Video/Photo):")
    return ANI_ME if context.user_data["path"] == "ani" else MOV_ME

async def a_save_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["tmp"]["file"] = update.message.photo[-1].file_id if update.message.photo else update.message.video.file_id
    await update.message.reply_text("Send Description:")
    return ANI_DE if context.user_data["path"] == "ani" else MOV_DE

async def a_save_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["tmp"]["link"] = update.message.text
    target = "anime" if context.user_data["path"] == "ani" else "movies"
    db[target].append(context.user_data["tmp"])
    await update.message.reply_text(f"✅ Added to {target}!")
    return ConversationHandler.END

# --- SECRET VAULT ADMIN (UNIQUE KEY PER FILE) ---
async def v_save_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["v_tmp"] = {"name": update.message.text}
    await update.message.reply_text("Send Poster/Media:")
    return V_ME

async def v_save_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["v_tmp"]["file"] = update.message.photo[-1].file_id if update.message.photo else update.message.video.file_id
    await update.message.reply_text("Send Description:")
    return V_DE

async def v_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = "".join([str(secrets.randbelow(10)) for _ in range(12)])
    context.user_data["v_tmp"]["desc"] = update.message.text
    context.user_data["v_tmp"]["key"] = key
    db["vaults"].append(context.user_data["v_tmp"])
    await update.message.reply_text(f"✅ Content Saved!\n🔑 **Unique Access Key:** `{key}`")
    return ConversationHandler.END

# --- ADMIN CALLBACK ROUTER ---
async def admin_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "a_w": await query.edit_message_text("Send Welcome Text:"); return W_TXT
    if query.data == "a_ani": context.user_data["path"]="ani"; await query.edit_message_text("Anime Name:"); return ANI_NA
    if query.data == "a_mov": context.user_data["path"]="mov"; await query.edit_message_text("Movie Name:"); return MOV_NA
    if query.data == "a_v": await query.edit_message_text("Vault Folder/File Name:"); return V_NA

# --- WEB SERVER ---
server = Flask(__name__)
@server.route('/')
def h(): return "Bot Running"

def main():
    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_router, pattern="^a_")],
        states={
            ANI_NA: [MessageHandler(filters.TEXT, a_save_name)],
            ANI_ME: [MessageHandler(filters.PHOTO | filters.VIDEO, a_save_media)],
            ANI_DE: [MessageHandler(filters.TEXT, lambda u,c: ANI_LI)],
            ANI_LI: [MessageHandler(filters.TEXT, a_save_final)],
            MOV_NA: [MessageHandler(filters.TEXT, a_save_name)],
            MOV_ME: [MessageHandler(filters.PHOTO | filters.VIDEO, a_save_media)],
            MOV_DE: [MessageHandler(filters.TEXT, lambda u,c: MOV_LI)],
            MOV_LI: [MessageHandler(filters.TEXT, a_save_final)],
            V_NA: [MessageHandler(filters.TEXT, v_save_name)],
            V_ME: [MessageHandler(filters.PHOTO | filters.VIDEO, v_save_media)],
            V_DE: [MessageHandler(filters.TEXT, v_finish)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )

    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(conv)
    app.add_handler(CommandHandler("start", start))
    
    Thread(target=lambda: server.run(host='0.0.0.0', port=PORT)).start()
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
