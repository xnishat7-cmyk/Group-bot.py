import logging
import sqlite3
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    Updater
)

# ==================================================================
# PATCH FOR PYTHON 3.14+ (Fixes AttributeError in Termux/VPS)
if not hasattr(Updater, '_Updater__polling_cleanup_cb'):
    Updater._Updater__polling_cleanup_cb = None
# ==================================================================

# --- CONFIGURATION ---
BOT_TOKEN = "9044135029:AAEW5eutjYw7D-Jknk_VDKSfT3-AsYNfChA"
ADMIN_ID_1 = 8296736080
ADMIN_ID_2 = 8904986478
ADMINS = [ADMIN_ID_1, ADMIN_ID_2]

BOT_USERNAME = "YourBotUsername"  # Replace with actual username (without @)
FORCE_JOIN_CHAT_ID = "-1001234567890"  # Replace with actual Chat ID
FORCE_JOIN_LINK = "https://t.me/yourgrouplink"  # Replace with actual group link

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect('bot_database.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS files
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  command TEXT UNIQUE, secret TEXT, file_id TEXT, 
                  file_name TEXT, stock INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS warnings
                 (user_id INTEGER PRIMARY KEY, count INTEGER)''')
    conn.commit()
    conn.close()

init_db()

def db_execute(query, params=()):
    conn = sqlite3.connect('bot_database.db', check_same_thread=False)
    c = conn.cursor()
    c.execute(query, params)
    conn.commit()
    result = c.fetchall()
    conn.close()
    return result

def is_admin(user_id):
    return user_id in ADMINS

# --- HELPER FUNCTIONS ---
async def auto_delete_message(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    try:
        await context.bot.delete_message(chat_id=job.chat_id, message_id=job.data)
    except Exception as e:
        logger.error(f"Failed to delete message: {e}")

async def check_force_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(chat_id=FORCE_JOIN_CHAT_ID, user_id=user_id)
        if member.status in ['left', 'kicked']:
            return False
        return True
    except Exception as e:
        logger.error(f"Force join check error: {e}")
        return True

async def send_force_join_message(update: Update):
    keyboard = [
        [InlineKeyboardButton("✅ Join Group", url=FORCE_JOIN_LINK)],
        [InlineKeyboardButton("🔄 Verify", callback_data="verify_join")]
    ]
    await update.effective_message.reply_text(
        "⚠️ You must join our group to use this bot.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# --- START COMMAND ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    
    if chat_type in ['group', 'supergroup']:
        await update.message.reply_text("👋 Welcome! Use the bot in private to get files.")
        return
        
    if not await check_force_join(update, context):
        await send_force_join_message(update)
        return
        
    if context.args:
        param = context.args[0]
        file_data = db_execute("SELECT file_id, file_name, stock FROM files WHERE (command || '_' || secret) = ?", (param,))
        
        if file_data:
            file_id, file_name, stock = file_data[0]
            if stock == 0:
                await update.message.reply_text("❌ Sorry, this file is out of stock!")
                return
                
            sent_msg = await update.message.reply_document(document=file_id, caption=f"✅ File: {file_name}")
            
            if stock > 0:
                db_execute("UPDATE files SET stock = stock - 1 WHERE (command || '_' || secret) = ?", (param,))
                
            context.job_queue.run_once(auto_delete_message, 300, chat_id=update.effective_chat.id, data=sent_msg.message_id)
            await sent_msg.reply_text("⏳ This file will be automatically deleted in 5 minutes.")
        else:
            await update.message.reply_text("❌ Invalid or expired link!")
    else:
        await update.message.reply_text("👋 Welcome! Use the special link provided by the admin to get files.")

# --- GROUP COMMANDS ---
async def handle_group_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.split()[0].replace("/", "")
    chat_type = update.effective_chat.type
    
    file_data = db_execute("SELECT id, secret FROM files WHERE command = ?", (text,))
    
    if file_data:
        if chat_type in ['group', 'supergroup']:
            secret = file_data[0][1]
            deep_link = f"https://t.me/{BOT_USERNAME}?start={text}_{secret}"
            keyboard = [[InlineKeyboardButton("📥 Get File in Private", url=deep_link)]]
            await update.message.reply_text("🔒 This file is private. Click below to get it.", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text("❌ Please use the command from the group.")

# --- CALLBACKS ---
async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if await check_force_join(update, context):
        await query.edit_message_text("✅ Verified! You can now use the bot.")
    else:
        await query.answer("❌ You have not joined all required groups yet!", show_alert=True)

# --- ADMIN FILE UPLOAD FLOW ---
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
    
    doc = update.message.document
    context.user_data['new_file'] = {'file_id': doc.file_id, 'file_name': doc.file_name}
    context.user_data['state'] = 'waiting_cmd'
    await update.message.reply_text("📁 File received.\n\nEnter the main command (e.g., get_diamond):")

async def handle_admin_states(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    state = context.user_data.get('state')
    text = update.message.text.strip()
    
    if state == 'waiting_cmd':
        context.user_data['new_file']['command'] = text.replace("/", "")
        context.user_data['state'] = 'waiting_secret'
        await update.message.reply_text("🔑 Enter the secret password:")
        
    elif state == 'waiting_secret':
        context.user_data['new_file']['secret'] = text
        context.user_data['state'] = 'waiting_stock'
        await update.message.reply_text("📦 Enter the stock limit (number):")
        
    elif state == 'waiting_stock':
        try:
            stock = int(text)
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid number.")
            return
            
        f = context.user_data['new_file']
        try:
            db_execute("INSERT INTO files (command, secret, file_id, file_name, stock) VALUES (?, ?, ?, ?, ?)",
                       (f['command'], f['secret'], f['file_id'], f['file_name'], stock))
                       
            magic_link = f"https://t.me/{BOT_USERNAME}?start={f['command']}_{f['secret']}"
            await update.message.reply_text(f"✅ File saved!\n\n🔗 Link: {magic_link}")
        except sqlite3.IntegrityError:
            await update.message.reply_text("❌ This command already exists in the database.")
            
        context.user_data['state'] = None

# --- ANTI-SPAM SYSTEM ---
async def anti_spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ['group', 'supergroup']: 
        return
    if is_admin(update.effective_user.id): 
        return
    
    text = update.message.text.lower() if update.message.text else ""
    bad_words = ["badword1", "badword2", "gali"] 
    
    if any(word in text for word in bad_words):
        await update.message.delete()
        
        res = db_execute("SELECT count FROM warnings WHERE user_id = ?", (update.effective_user.id,))
        count = res[0][0] if res else 0
        
        if count == 0:
            db_execute("INSERT OR REPLACE INTO warnings (user_id, count) VALUES (?, 1)", (update.effective_user.id,))
            await update.message.reply_text(f"⚠️ {update.effective_user.first_name}, Warning 1! Please do not use bad words.")
        elif count == 1:
            db_execute("UPDATE warnings SET count = 2 WHERE user_id = ?", (update.effective_user.id,))
            permissions = ChatPermissions(can_send_messages=False)
            await context.bot.restrict_chat_member(update.effective_chat.id, update.effective_user.id, permissions=permissions)
            await update.message.reply_text(f"🔇 {update.effective_user.first_name}, Warning 2! You have been muted for 5 minutes.")
            
            async def unmute(context: ContextTypes.DEFAULT_TYPE):
                try:
                    permissions = ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True, can_add_web_page_previews=True)
                    await context.bot.restrict_chat_member(update.effective_chat.id, update.effective_user.id, permissions=permissions)
                except Exception:
                    pass
            context.job_queue.run_once(unmute, 300, chat_id=update.effective_chat.id)
            
        elif count >= 2:
            db_execute("UPDATE warnings SET count = 0 WHERE user_id = ?", (update.effective_user.id,))
            await context.bot.ban_chat_member(update.effective_chat.id, update.effective_user.id)
            await update.message.reply_text(f"🔨 {update.effective_user.first_name}, Warning 3! You have been permanently banned.")

# --- MAIN ---
if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(verify_callback, pattern="verify_join"))
    app.add_handler(MessageHandler(filters.Document.ALL & filters.ChatType.PRIVATE, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_admin_states))
    app.add_handler(MessageHandler(filters.COMMAND, handle_group_command))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, anti_spam))

    logger.info("Premium Secure File Bot is running...")
    app.run_polling(close_loop=False)
