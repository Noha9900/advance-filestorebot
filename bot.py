import os
import asyncio
import secrets
from datetime import datetime, timedelta
from flask import Flask
from threading import Thread
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from openai import OpenAI

# --- CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
OPENAI_API_KEY = os.getenv("OPEN_AI_KEY")
PORT = int(os.getenv("PORT", "8080"))

client = OpenAI(api_key=OPENAI_API_KEY)

# --- DATABASE STRUCTURE ---
db = {
    "welcome": {"photo": None, "text": "Welcome to the Vault!"},
    "adult": {"welcome_photo": None, "welcome_text": "Adult Zone", "channels": []},
    "anime": [],
    "movies": [],
    "vault": {}, # structure: {folder_name: {subfolders: {videos: [], albums: []}, key: ""}}
    "active_keys": {} 
}

# --- CONVERSATION STATES ---
(A_WELCOME_TEXT, A_WELCOME_PHOTO, A_ANIME_NAME, A_ANIME_MEDIA, A_ANIME_DESC, A_ANIME_LINK,
 A_VAULT_NAME, A_VAULT_FILE, U_VAULT_KEY, U_GUIDE_SELECT) = range(10)

# --- AI FACILITY ---
async def ai_guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message.text
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": "You are an AI assistant for a Vault Bot. Guide users on how to enter keys or find content."},
                      {"role": "user", "content": user_msg}]
        )
        await update.message.reply_text(f"🤖 AI Guide: {response.choices[0].message.content}")
    except:
        await update.message.reply_text("🤖 I'm here to help! Use the menu buttons to navigate.")

# --- UTILS ---
async def delete_msg(context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.delete_message(chat_id=context.job.chat_id, message_id=context.job.data)
    except: pass

# --- USER INTERFACE ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("Welcome 🏠", callback_data="u_welcome"), InlineKeyboardButton("Adult Stream 🔥", callback_data="u_adult")],
        [InlineKeyboardButton("Anime Guide 🎌", callback_data="u_list_anime"), InlineKeyboardButton("Movie Guide 🎬", callback_data="u_list_movies")],
        [InlineKeyboardButton("Secret Vault 🔒", callback_data="u_vault_folders")]
    ]
    text = "Main Menu"
    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def handle_user_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "u_welcome":
        data = db["welcome"]
        if data["photo"]:
            msg = await query.message.reply_photo(photo=data["photo"], caption=data["text"])
        else:
            msg = await query.message.reply_text(data["text"])
        context.job_queue.run_once(delete_msg, 30, data=msg.message_id, chat_id=query.message.chat_id)

    elif "u_list_" in query.data:
        guide_type = query.data.split("_")[-1]
        items = db[guide_type]
        text = f"📖 **{guide_type.upper()} LIST**\nReply with the number to view:\n"
        for i, item in enumerate(items, 1):
            text += f"{i}. {item['name']}\n"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main")]]))
        context.user_data["viewing"] = guide_type
        return U_GUIDE_SELECT

async def show_guide_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idx = int(update.message.text) - 1
    guide_type = context.user_data.get("viewing")
    if 0 <= idx < len(db[guide_type]):
        item = db[guide_type][idx]
        caption = f"📌 {item['name']}\n\n{item['desc']}\n\n🔗 Link: {item['link']}"
        await update.message.reply_photo(photo=item['media'], caption=caption)
    return ConversationHandler.END

# --- ADMIN PANEL ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    kb = [
        [InlineKeyboardButton("Set Welcome", callback_data="adm_w"), InlineKeyboardButton("Add Anime", callback_data="adm_anime")],
        [InlineKeyboardButton("Add Movie", callback_data="adm_movie"), InlineKeyboardButton("Create Vault Folder", callback_data="adm_vault")]
    ]
    await update.message.reply_text("🛠 Admin Control Panel", reply_markup=InlineKeyboardMarkup(kb))

async def start_anime_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["mode"] = "anime" if "anime" in update.callback_query.data else "movies"
    await update.callback_query.edit_message_text("Send the Name:")
    return A_ANIME_NAME

async def save_anime_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["temp"] = {"name": update.message.text}
    await update.message.reply_text("Now send Video or Photo:")
    return A_ANIME_MEDIA

async def save_anime_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["temp"]["media"] = (update.message.photo[-1].file_id if update.message.photo else update.message.video.file_id)
    await update.message.reply_text("Send Description:")
    return A_ANIME_DESC

async def save_anime_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["temp"]["desc"] = update.message.text
    await update.message.reply_text("Send Link:")
    return A_ANIME_LINK

async def save_anime_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["temp"]["link"] = update.message.text
    db[context.user_data["mode"]].append(context.user_data["temp"])
    await update.message.reply_text("✅ Entry Added!")
    return ConversationHandler.END

# --- SERVER FOR RENDER ---
server = Flask(__name__)
@server.route('/')
def h(): return "OK"

def main():
    app = Application.builder().token(TOKEN).build()
    
    # Combined Conversation Handler
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("admin", admin_panel),
            CallbackQueryHandler(start_anime_add, pattern="adm_(anime|movie)"),
            CallbackQueryHandler(handle_user_buttons, pattern="u_.*")
        ],
        states={
            A_ANIME_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_anime_name)],
            A_ANIME_MEDIA: [MessageHandler((filters.PHOTO | filters.VIDEO) & ~filters.COMMAND, save_anime_media)],
            A_ANIME_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_anime_desc)],
            A_ANIME_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_anime_final)],
            U_GUIDE_SELECT: [MessageHandler(filters.Regex(r'^\d+$'), show_guide_item)]
        },
        fallbacks=[CommandHandler("start", start)]
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_guide))
    app.add_handler(CallbackQueryHandler(start, pattern="main"))

    Thread(target=lambda: server.run(host='0.0.0.0', port=PORT)).start()
    app.run_polling()

if __name__ == "__main__":
    main()
