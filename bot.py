import os, asyncio, secrets, logging
from datetime import datetime, timedelta
from flask import Flask
from threading import Thread
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from openai import OpenAI

# --- CONFIG ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
OPENAI_API_KEY = os.getenv("OPEN_AI_KEY")
PORT = int(os.getenv("PORT", "8080"))

client = OpenAI(api_key=OPENAI_API_KEY)

# --- DB ---
db = {
    "welcome": {"photo": None, "text": "Welcome to the Vault!"},
    "adult": {"photo": None, "text": "Adult Stream", "channels": []},
    "anime": [], "movies": [], "vault": {}, "keys": []
}

# --- STATES ---
(A_W_TEXT, A_W_PHOTO, A_NAME, A_MEDIA, A_DESC, A_LINK, V_KEY) = range(7)

# --- AI ---
async def ai_guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        res = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": "Explain the bot buttons and the 12-digit key system."},
                      {"role": "user", "content": update.message.text}]
        )
        await update.message.reply_text(f"🤖 {res.choices[0].message.content}")
    except:
        await update.message.reply_text("🤖 I'm here to help! Use the buttons below.")

# --- UTILS ---
async def del_msg(context: ContextTypes.DEFAULT_TYPE):
    try: await context.bot.delete_message(chat_id=context.job.chat_id, message_id=context.job.data)
    except: pass

# --- MAIN UI ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("Welcome 🏠", callback_data="u_w"), InlineKeyboardButton("Adult Stream 🔥", callback_data="u_a")],
        [InlineKeyboardButton("Anime Guide 🎌", callback_data="u_list_anime"), InlineKeyboardButton("Movie Guide 🎬", callback_data="u_list_movies")],
        [InlineKeyboardButton("Secret Vault 🔒", callback_data="u_v")]
    ]
    text = "💠 **MAIN MENU** 💠\nChoose an option below:"
    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

# --- BUTTON LOGIC ---
async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if data == "u_w":
        w = db["welcome"]
        if w["photo"]: m = await query.message.reply_photo(w["photo"], caption=w["text"])
        else: m = await query.message.reply_text(w["text"])
        context.job_queue.run_once(del_msg, 30, data=m.message_id, chat_id=query.message.chat_id)
    
    elif data == "u_a":
        kb = [[InlineKeyboardButton("🔙 Back", callback_data="main")]]
        await query.edit_message_text(f"🔞 **{db['adult']['text']}**\nJoin our channels below:", reply_markup=InlineKeyboardMarkup(kb))

    elif "u_list_" in data:
        g_type = data.split("_")[-1]
        items = db[g_type]
        txt = f"📖 **{g_type.upper()} GUIDE**\n\n"
        for i, item in enumerate(items, 1): txt += f"{i}. {item['name']}\n"
        if not items: txt += "No entries found."
        kb = [[InlineKeyboardButton("🔙 Back", callback_data="main")]]
        await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))

    elif data == "u_v":
        await query.edit_message_text("🔐 **VAULT LOCKED**\nPlease enter the 12-digit key:")
        return V_KEY

# --- ADMIN PANEL ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    kb = [
        [InlineKeyboardButton("Set Welcome", callback_data="adm_w"), InlineKeyboardButton("Add Anime", callback_data="adm_ani")],
        [InlineKeyboardButton("Add Movie", callback_data="adm_mov"), InlineKeyboardButton("Gen Key", callback_data="adm_gen")]
    ]
    await update.message.reply_text("🛠 **ADMIN PANEL**", reply_markup=InlineKeyboardMarkup(kb))

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "adm_w":
        await query.edit_message_text("Send Welcome Text:")
        return A_W_TEXT
    elif query.data in ["adm_ani", "adm_mov"]:
        context.user_data["type"] = "anime" if "ani" in query.data else "movies"
        await query.edit_message_text("Send Name:")
        return A_NAME
    elif query.data == "adm_gen":
        key = "".join([str(secrets.randbelow(10)) for _ in range(12)])
        db["keys"].append(key)
        await query.edit_message_text(f"🗝 **Generated Key:** `{key}`\nCopy and give to users.")
        return ConversationHandler.END

# --- ADMIN CONVERSATION STEPS ---
async def save_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["temp"] = {"name": update.message.text}
    await update.message.reply_text("Send Photo/Video:")
    return A_MEDIA

async def save_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["temp"]["file"] = update.message.photo[-1].file_id if update.message.photo else update.message.video.file_id
    await update.message.reply_text("Send Description:")
    return A_DESC

async def save_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["temp"]["desc"] = update.message.text
    await update.message.reply_text("Send Link:")
    return A_LINK

async def save_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["temp"]["link"] = update.message.text
    db[context.user_data["type"]].append(context.user_data["temp"])
    await update.message.reply_text("✅ Added Successfully!")
    return ConversationHandler.END

# --- VAULT ACCESS ---
async def check_v_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text in db["keys"]:
        m = await update.message.reply_text("🔓 **Vault Unlocked!**\nAccessing files... (Disappearing in 30m)")
        context.job_queue.run_once(del_msg, 1800, data=m.message_id, chat_id=update.effective_chat.id)
    else:
        await update.message.reply_text("❌ Invalid Key.")
    return ConversationHandler.END

# --- RENDER SERVER ---
app = Flask(__name__)
@app.route('/')
def h(): return "Bot Running"

def main():
    application = Application.builder().token(TOKEN).build()

    # The Logic Engine: Conversation Handler
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("admin", admin_panel),
            CallbackQueryHandler(admin_callback, pattern="^adm_"),
            CallbackQueryHandler(handle_buttons, pattern="^u_")
        ],
        states={
            A_W_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: A_W_PHOTO)],
            A_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_name)],
            A_MEDIA: [MessageHandler((filters.PHOTO | filters.VIDEO), save_media)],
            A_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_desc)],
            A_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_final)],
            V_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_v_key)]
        },
        fallbacks=[CommandHandler("start", start), CallbackQueryHandler(start, pattern="main")]
    )

    application.add_handler(conv)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(start, pattern="main"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_guide))

    Thread(target=lambda: app.run(host='0.0.0.0', port=PORT)).start()
    application.run_polling()

if __name__ == "__main__":
    main()
