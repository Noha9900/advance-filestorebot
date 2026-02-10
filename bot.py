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

# --- DB (Local) ---
db = {
    "welcome": {"photo": None, "text": "Welcome to the Vault! 🔥"},
    "adult": {"photo": None, "text": "Adult Stream Zone", "channels": []}, # List of {"name": x, "link": y}
    "anime": [], "movies": [], "vault": {}, "keys": []
}

# --- STATES ---
(A_W_TEXT, A_W_PHOTO, A_NAME, A_MEDIA, A_DESC, A_LINK, 
 A_AD_PHOTO, A_AD_TEXT, A_AD_CHAN_NAME, A_AD_CHAN_LINK) = range(10)

# --- UTILS ---
async def del_msg(context: ContextTypes.DEFAULT_TYPE):
    try: await context.bot.delete_message(chat_id=context.job.chat_id, message_id=context.job.data)
    except: pass

# --- USER INTERFACE ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    w = db["welcome"]
    kb = [
        [InlineKeyboardButton("Adult Stream 🔥", callback_data="u_adult_view")],
        [InlineKeyboardButton("Anime Guide 🎌", callback_data="u_list_anime"), 
         InlineKeyboardButton("Movie Guide 🎬", callback_data="u_list_movies")],
        [InlineKeyboardButton("Secret Vault 🔒", callback_data="u_v_lock")]
    ]
    markup = InlineKeyboardMarkup(kb)
    
    if update.message:
        if w["photo"]:
            msg = await update.message.reply_photo(w["photo"], caption=w["text"], reply_markup=markup)
        else:
            msg = await update.message.reply_text(w["text"], reply_markup=markup)
        context.job_queue.run_once(del_msg, 30, data=msg.message_id, chat_id=update.effective_chat.id)
    else:
        # If edit fails (e.g. switching from photo to text), send a new message
        try:
            await update.callback_query.edit_message_text(w["text"], reply_markup=markup)
        except:
            await update.callback_query.message.reply_text(w["text"], reply_markup=markup)

async def view_adult_stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ad = db["adult"]
    
    # Build Channel Buttons
    kb = [[InlineKeyboardButton(c["name"], url=c["link"])] for c in ad["channels"]]
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
    
    if ad["photo"]:
        msg = await query.message.reply_photo(ad["photo"], caption=ad["text"], reply_markup=InlineKeyboardMarkup(kb))
    else:
        msg = await query.message.reply_text(ad["text"], reply_markup=InlineKeyboardMarkup(kb))
    
    context.job_queue.run_once(del_msg, 30, data=msg.message_id, chat_id=query.message.chat_id)

# --- ADMIN PANEL ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    kb = [
        [InlineKeyboardButton("Set Welcome", callback_data="adm_w"), InlineKeyboardButton("Set Adult Stream", callback_data="adm_ad")],
        [InlineKeyboardButton("Add Anime", callback_data="adm_ani"), InlineKeyboardButton("Add Movie", callback_data="adm_mov")],
        [InlineKeyboardButton("Gen Key 🗝", callback_data="adm_gen")]
    ]
    await update.message.reply_text("🛠 **ADMIN PANEL**", reply_markup=InlineKeyboardMarkup(kb))

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "adm_w":
        await query.edit_message_text("1️⃣ Send the new Welcome Text:")
        return A_W_TEXT
    elif query.data == "adm_ad":
        await query.edit_message_text("🔞 Adult Setup: Send the Welcome Photo (or /skip):")
        return A_AD_PHOTO
    elif query.data in ["adm_ani", "adm_mov"]:
        context.user_data["type"] = "anime" if "ani" in query.data else "movies"
        await query.edit_message_text(f"Enter Name for {context.user_data['type']}:")
        return A_NAME
    elif query.data == "adm_gen":
        key = "".join([str(secrets.randbelow(10)) for _ in range(12)])
        db["keys"].append(key)
        await query.edit_message_text(f"🗝 **New Key Generated:** `{key}`")
        return ConversationHandler.END

# --- ADULT STREAM SETUP ---
async def ad_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db["adult"]["photo"] = update.message.photo[-1].file_id if update.message.photo else None
    await update.message.reply_text("Send the Adult Stream Welcome Text:")
    return A_AD_TEXT

async def ad_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db["adult"]["text"] = update.message.text
    db["adult"]["channels"] = [] # Reset for new setup
    await update.message.reply_text("Now send the 1st Channel Name:")
    return A_AD_CHAN_NAME

async def ad_chan_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["last_chan"] = update.message.text
    await update.message.reply_text(f"Send the Link for {update.message.text}:")
    return A_AD_CHAN_LINK

async def ad_chan_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db["adult"]["channels"].append({"name": context.user_data["last_chan"], "link": update.message.text})
    kb = [[InlineKeyboardButton("Add Another", callback_data="add_more"), InlineKeyboardButton("Finish", callback_data="finish")]]
    await update.message.reply_text("Channel added! Add more or finish?", reply_markup=InlineKeyboardMarkup(kb))
    return A_AD_CHAN_NAME # Loop or end via callback

# --- FLASK KEEP-ALIVE ---
server = Flask(__name__)
@server.route('/')
def h(): return "Bot is 24/7 Active"

def main():
    app = Application.builder().token(TOKEN).build()

    admin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^adm_")],
        states={
            A_W_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: A_W_PHOTO)], # Connect to your existing save logic
            A_AD_PHOTO: [MessageHandler(filters.PHOTO | filters.COMMAND, ad_photo)],
            A_AD_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ad_text)],
            A_AD_CHAN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ad_chan_name), CallbackQueryHandler(lambda u, c: ConversationHandler.END, pattern="finish")],
            A_AD_CHAN_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, ad_chan_link)],
            # Include your previous A_NAME, A_MEDIA, A_DESC states here...
        },
        fallbacks=[CommandHandler("start", start), CallbackQueryHandler(start, pattern="main_menu")],
        allow_reentry=True # Critical for 24/7 button usage
    )

    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(admin_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(view_adult_stream, pattern="u_adult_view"))
    app.add_handler(CallbackQueryHandler(start, pattern="main_menu"))

    Thread(target=lambda: server.run(host='0.0.0.0', port=PORT)).start()
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
