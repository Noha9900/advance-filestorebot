import os, asyncio, secrets, logging
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

# --- DB (Local Memory) ---
# NOTE: To make this 100% permanent, you'd replace these lists with MongoDB calls.
db = {
    "welcome": {"photo": None, "text": "Welcome to the Vault! 🔥"},
    "adult": {"photo": None, "text": "Adult Stream Zone", "channels": []},
    "anime": [], 
    "movies": [], 
    "vault": {}, # Structure: {"Desi": {"files": [{"photo": id, "desc": str, "video": id}]}}
    "keys": []
}

# --- STATES ---
(A_W_TEXT, A_W_PHOTO, A_NAME, A_MEDIA, A_DESC, A_LINK, 
 A_AD_PHOTO, A_AD_TEXT, A_AD_CHAN_NAME, A_AD_CHAN_LINK,
 A_V_FOLDER, A_V_POSTER, A_V_DESC, A_V_FILE, V_KEY_INPUT) = range(15)

# --- UTILS ---
async def del_msg(context: ContextTypes.DEFAULT_TYPE):
    try: await context.bot.delete_message(chat_id=context.job.chat_id, message_id=context.job.data)
    except: pass

# --- USER UI ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    w = db["welcome"]
    kb = [
        [InlineKeyboardButton("Adult Stream 🔥", callback_data="u_adult_view")],
        [InlineKeyboardButton("Anime Guide 🎌", callback_data="u_list_anime"), 
         InlineKeyboardButton("Movie Guide 🎬", callback_data="u_list_movies")],
        [InlineKeyboardButton("Secret Vault 🔒", callback_data="u_vault_view")]
    ]
    markup = InlineKeyboardMarkup(kb)
    
    if update.message:
        if w["photo"]:
            msg = await update.message.reply_photo(w["photo"], caption=w["text"], reply_markup=markup)
        else:
            msg = await update.message.reply_text(w["text"], reply_markup=markup)
        context.job_queue.run_once(del_msg, 30, data=msg.message_id, chat_id=update.effective_chat.id)
    else:
        try: await update.callback_query.edit_message_text(w["text"], reply_markup=markup)
        except: await update.callback_query.message.reply_text(w["text"], reply_markup=markup)

async def view_guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    g_type = "anime" if "anime" in query.data else "movies"
    items = db[g_type]
    txt = f"📖 **{g_type.upper()} LIST**\nSelect a number:\n\n"
    for i, item in enumerate(items, 1): txt += f"{i}. {item['name']}\n"
    kb = [[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]
    await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))

# --- SECRET VAULT USER LOGIC ---
async def vault_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not db["vault"]:
        await query.edit_message_text("Vault is currently empty.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]))
        return ConversationHandler.END
    
    txt = "🔐 **VAULT ACCESS**\nEnter the 12-digit secret key to unlock folders:"
    await query.edit_message_text(txt)
    return V_KEY_INPUT

async def check_vault_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text in db["keys"]:
        kb = [[InlineKeyboardButton(folder, callback_data=f"vfold_{folder}")] for folder in db["vault"].keys()]
        await update.message.reply_text("🔓 **UNLOCKED**\nChoose a folder (Access expires in 30m):", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text("❌ Invalid Key.")
    return ConversationHandler.END

# --- ADMIN LOGIC ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    kb = [
        [InlineKeyboardButton("Set Welcome", callback_data="adm_w"), InlineKeyboardButton("Set Adult", callback_data="adm_ad")],
        [InlineKeyboardButton("Add Anime/Movie", callback_data="adm_ani")],
        [InlineKeyboardButton("Create Vault Folder", callback_data="adm_vcreate")],
        [InlineKeyboardButton("Gen Key 🗝", callback_data="adm_gen")]
    ]
    await update.message.reply_text("🛠 **ADMIN PANEL**", reply_markup=InlineKeyboardMarkup(kb))

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "adm_w":
        await query.edit_message_text("Send Welcome Text:")
        return A_W_TEXT
    elif query.data == "adm_ani":
        await query.edit_message_text("Enter Name:")
        return A_NAME
    elif query.data == "adm_vcreate":
        await query.edit_message_text("Enter Folder Name (e.g. Desi):")
        return A_V_FOLDER
    elif query.data == "adm_gen":
        key = "".join([str(secrets.randbelow(10)) for _ in range(12)])
        db["keys"].append(key)
        await query.edit_message_text(f"🗝 **Generated Key:** `{key}`")
        return ConversationHandler.END

# --- ADMIN SAVE FUNCTIONS ---
async def save_w_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["tmp_txt"] = update.message.text
    await update.message.reply_text("Send Photo (or /skip):")
    return A_W_PHOTO

async def save_w_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db["welcome"]["text"] = context.user_data["tmp_txt"]
    db["welcome"]["photo"] = update.message.photo[-1].file_id if update.message.photo else None
    await update.message.reply_text("✅ Welcome Set!")
    return ConversationHandler.END

# --- WEB SERVER ---
server = Flask(__name__)
@server.route('/')
def h(): return "Alive"

def main():
    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_callback, pattern="^adm_"),
            CallbackQueryHandler(vault_user_start, pattern="u_vault_view")
        ],
        states={
            A_W_TEXT: [MessageHandler(filters.TEXT, save_w_text)],
            A_W_PHOTO: [MessageHandler(filters.PHOTO | filters.COMMAND, save_w_photo)],
            V_KEY_INPUT: [MessageHandler(filters.TEXT, check_vault_key)],
            # Add remaining states for Anime/Adult Stream similarly
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )

    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(view_guide, pattern="u_list_"))
    app.add_handler(CallbackQueryHandler(start, pattern="main_menu"))

    Thread(target=lambda: server.run(host='0.0.0.0', port=PORT)).start()
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
