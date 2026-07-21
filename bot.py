"""
SelectionWay Telegram Bot
=========================
Telegram bot interface for SelectionWay batch content extraction.
"""

import os
import sys
import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, List

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    os.environ['PYTHONIOENCODING'] = 'utf-8'

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    BotCommand, constants
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from telegram.error import TelegramError

from config import (
    BOT_TOKEN, API_BASE, USER_ID, ALLOWED_USERS,
    ADMIN_IDS, LOG_LEVEL, MAX_BATCHES_PER_PAGE
)
from selectionway_extractor import SelectionWayExtractor
from utils.helpers import (
    format_batch_info, create_batches_keyboard,
    format_extraction_summary, sanitize_filename,
    split_long_message
)
from utils.progress import ProgressTracker

# ─── Setup Logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, LOG_LEVEL)
)
logger = logging.getLogger(__name__)

# ─── Conversation States ────────────────────────────────────────────────────
SELECTING_BATCH, CONFIRM_EXTRACTION, EXTRACTING = range(3)

# ─── Initialize Extractor ───────────────────────────────────────────────────
extractor = SelectionWayExtractor(API_BASE, USER_ID)
progress_tracker = ProgressTracker()

# ─── Decorators ─────────────────────────────────────────────────────────────
def restricted(func):
    """Restrict command to allowed users only."""
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if ALLOWED_USERS and user_id not in ALLOWED_USERS:
            await update.message.reply_text(
                "🚫 *Access Denied*\n\n"
                "You are not authorized to use this bot.\n"
                "Contact the administrator for access.",
                parse_mode='Markdown'
            )
            logger.warning(f"Unauthorized access attempt by user {user_id}")
            return ConversationHandler.END
        return await func(update, context, *args, **kwargs)
    return wrapped

def admin_only(func):
    """Restrict command to admin users only."""
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text(
                "⛔ *Admin Only*\n\n"
                "This command is restricted to administrators.",
                parse_mode='Markdown'
            )
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# ─── Command Handlers ───────────────────────────────────────────────────────
@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message and show main menu."""
    user = update.effective_user
    
    welcome_msg = (
        f"🎓 *SelectionWay Batch Extractor Bot*\n\n"
        f"Welcome, {user.first_name}!\n\n"
        f"*Available Commands:*\n"
        f"📚 /batches - List all available batches\n"
        f"🔍 /search - Search for a specific batch\n"
        f"📊 /stats - View extraction statistics\n"
        f"ℹ️ /help - Show detailed help\n\n"
        f"*Quick Actions:*"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("📚 View Batches", callback_data="view_batches"),
            InlineKeyboardButton("🔍 Search", callback_data="search")
        ],
        [
            InlineKeyboardButton("📊 Statistics", callback_data="stats"),
            InlineKeyboardButton("ℹ️ Help", callback_data="help")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        welcome_msg,
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

@restricted
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help message."""
    help_text = (
        "*📖 SelectionWay Bot Help*\n\n"
        "*Core Commands:*\n"
        "• /start - Show main menu\n"
        "• /batches - List all available batches\n"
        "• /search <keyword> - Search batches\n"
        "• /extract <batch_id> - Extract batch content\n"
        "• /stats - View statistics\n"
        "• /cancel - Cancel current operation\n\n"
        
        "*Admin Commands:*\n"
        "• /admin - Admin panel\n"
        "• /broadcast - Send message to all users\n"
        "• /userstats - View user statistics\n\n"
        
        "*How to use:*\n"
        "1. Use /batches to see available courses\n"
        "2. Select a batch to extract content from\n"
        "3. The bot will fetch all videos and PDFs\n"
        "4. Download the text file with all links\n\n"
        
        "*Note:* Extraction may take a few minutes for large batches."
    )
    
    await update.message.reply_text(
        help_text,
        parse_mode='Markdown'
    )

@restricted
async def batches_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show paginated list of all batches."""
    await show_batches_page(update, context, page=0)

async def show_batches_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    """Display a page of batches with navigation."""
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.message
    
    # Fetch batches
    status_msg = await message.reply_text("🔄 *Fetching batches...*", parse_mode='Markdown')
    
    try:
        batches = await extractor.fetch_all_batches_async()
    except Exception as e:
        logger.error(f"Failed to fetch batches: {e}")
        await status_msg.edit_text(
            "❌ *Error fetching batches*\n\n"
            f"```{str(e)}```\n\n"
            "Please try again later.",
            parse_mode='Markdown'
        )
        return
    
    await status_msg.delete()
    
    if not batches:
        await message.reply_text("❌ No batches found. Please check your configuration.")
        return
    
    # Calculate pagination
    total_pages = (len(batches) + MAX_BATCHES_PER_PAGE - 1) // MAX_BATCHES_PER_PAGE
    start_idx = page * MAX_BATCHES_PER_PAGE
    end_idx = min(start_idx + MAX_BATCHES_PER_PAGE, len(batches))
    page_batches = batches[start_idx:end_idx]
    
    # Create message
    header = f"📚 *Available Batches* (Page {page + 1}/{total_pages})\n\n"
    batch_list = "\n".join([
        f"`{i+1}.` *{b['title'][:50]}*\n"
        f"   🆔 `{b['id']}` | "
        f"{'🔴 LIVE' if b.get('isLive') else '📺 VOD'} | "
        f"{'🆓 Free' if b.get('isFree') else '💎 Paid'}"
        for i, b in enumerate(page_batches)
    ])
    
    # Create keyboard with batch buttons and navigation
    keyboard = []
    
    # Batch selection buttons (2 per row)
    for i in range(0, len(page_batches), 2):
        row = []
        for j in range(2):
            if i + j < len(page_batches):
                batch = page_batches[i + j]
                row.append(InlineKeyboardButton(
                    f"📥 {batch['title'][:30]}",
                    callback_data=f"select_batch_{batch['id']}"
                ))
        keyboard.append(row)
    
    # Navigation buttons
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"page_{page-1}"))
    nav_row.append(InlineKeyboardButton("🔍 Search", callback_data="search"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{page+1}"))
    keyboard.append(nav_row)
    
    # Action buttons
    keyboard.append([
        InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"),
        InlineKeyboardButton("📊 Statistics", callback_data="stats")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    full_message = header + batch_list
    
    # Split if too long
    if len(full_message) > 4000:
        messages = split_long_message(full_message, 4000)
        for i, msg in enumerate(messages):
            if i == len(messages) - 1:
                await message.reply_text(msg, parse_mode='Markdown', reply_markup=reply_markup)
            else:
                await message.reply_text(msg, parse_mode='Markdown')
    else:
        await message.reply_text(full_message, parse_mode='Markdown', reply_markup=reply_markup)

@restricted
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search for batches by keyword."""
    if not context.args:
        await update.message.reply_text(
            "🔍 *Search Batches*\n\n"
            "Usage: `/search <keyword>`\n\n"
            "Example: `/search python`",
            parse_mode='Markdown'
        )
        return
    
    keyword = ' '.join(context.args).lower()
    status_msg = await update.message.reply_text(f"🔍 Searching for: *{keyword}*...", parse_mode='Markdown')
    
    try:
        batches = await extractor.fetch_all_batches_async()
        matching = [b for b in batches if keyword in b.get('title', '').lower()]
    except Exception as e:
        logger.error(f"Search failed: {e}")
        await status_msg.edit_text(f"❌ Search failed: {str(e)}")
        return
    
    await status_msg.delete()
    
    if not matching:
        await update.message.reply_text(
            f"❌ No batches found matching '*{keyword}*'",
            parse_mode='Markdown'
        )
        return
    
    # Display results
    result_text = f"🔍 *Search Results for '{keyword}'*\n\n"
    result_text += "\n".join([
        f"• *{b['title']}*\n  🆔 `{b['id']}`"
        for b in matching[:10]  # Limit to 10 results
    ])
    
    keyboard = []
    for batch in matching[:6]:
        keyboard.append([InlineKeyboardButton(
            f"📥 {batch['title'][:40]}",
            callback_data=f"select_batch_{batch['id']}"
        )])
    
    if len(matching) > 10:
        result_text += f"\n\n... and {len(matching) - 10} more results"
    
    keyboard.append([InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(result_text, parse_mode='Markdown', reply_markup=reply_markup)

@restricted
async def extract_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct extraction by batch ID."""
    if not context.args:
        await update.message.reply_text(
            "📥 *Extract Batch Content*\n\n"
            "Usage: `/extract <batch_id>`\n\n"
            "Get batch IDs from /batches command",
            parse_mode='Markdown'
        )
        return
    
    batch_id = context.args[0]
    context.user_data['extracting_batch_id'] = batch_id
    await perform_extraction(update, context, batch_id)

@restricted
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot statistics."""
    stats = progress_tracker.get_stats()
    
    stats_text = (
        "*📊 Bot Statistics*\n\n"
        f"• Total Extractions: {stats['total_extractions']}\n"
        f"• Successful: {stats['successful']}\n"
        f"• Failed: {stats['failed']}\n"
        f"• Total Videos Found: {stats['total_videos']}\n"
        f"• Total PDFs Found: {stats['total_pdfs']}\n"
        f"• Active Users: {stats['active_users']}\n"
        f"• Last Extraction: {stats['last_extraction']}\n\n"
        f"*Today:*\n"
        f"• Extractions: {stats['today_extractions']}\n"
        f"• Success Rate: {stats['success_rate']}%"
    )
    
    keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="stats")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(stats_text, parse_mode='Markdown', reply_markup=reply_markup)

@restricted
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel current operation."""
    context.user_data.clear()
    await update.message.reply_text(
        "✅ Operation cancelled.\n"
        "Use /start to begin again."
    )
    return ConversationHandler.END

# ─── Callback Query Handlers ─────────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all callback queries."""
    query = update.callback_query
    data = query.data
    
    await query.answer()
    
    if data == "main_menu":
        await start(update, context)
        await query.message.delete()
        
    elif data == "view_batches":
        await show_batches_page(update, context, page=0)
        await query.message.delete()
        
    elif data == "search":
        await query.message.reply_text(
            "🔍 *Search Batches*\n\n"
            "Send me a keyword to search for.\n"
            "Example: `python course`",
            parse_mode='Markdown'
        )
        
    elif data == "stats":
        await stats_command(update, context)
        
    elif data == "help":
        await help_command(update, context)
        
    elif data.startswith("page_"):
        page = int(data.split("_")[1])
        await show_batches_page(update, context, page)
        await query.message.delete()
        
    elif data.startswith("select_batch_"):
        batch_id = data.split("_")[-1]
        await show_batch_details(update, context, batch_id)
        
    elif data.startswith("confirm_extract_"):
        batch_id = data.split("_")[-1]
        await perform_extraction(update, context, batch_id)
        
    elif data.startswith("download_file_"):
        file_path = data.replace("download_file_", "")
        await send_extraction_file(update, context, file_path)

async def show_batch_details(update: Update, context: ContextTypes.DEFAULT_TYPE, batch_id: str):
    """Show detailed information about a batch."""
    query = update.callback_query
    
    status_msg = await query.message.reply_text("🔄 Loading batch details...")
    
    try:
        batch_info = await extractor.get_batch_info_async(batch_id)
    except Exception as e:
        logger.error(f"Failed to get batch info: {e}")
        await status_msg.edit_text(f"❌ Error: {str(e)}")
        return
    
    await status_msg.delete()
    
    if not batch_info:
        await query.message.reply_text("❌ Batch not found")
        return
    
    details = format_batch_info(batch_info)
    
    keyboard = [
        [
            InlineKeyboardButton("📥 Extract Content", callback_data=f"confirm_extract_{batch_id}"),
        ],
        [
            InlineKeyboardButton("📊 Topic Details", callback_data=f"topics_{batch_id}"),
        ],
        [
            InlineKeyboardButton("⬅️ Back to Batches", callback_data="view_batches"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.reply_text(details, parse_mode='Markdown', reply_markup=reply_markup)

async def perform_extraction(update: Update, context: ContextTypes.DEFAULT_TYPE, batch_id: str):
    """Execute the extraction process."""
    user_id = update.effective_user.id
    
    # Send initial status
    status_msg = await update.effective_message.reply_text(
        "🔄 *Starting Extraction...*\n\n"
        "⏳ Fetching batch information...",
        parse_mode='Markdown'
    )
    
    # Start progress tracking
    progress_tracker.start_extraction(user_id, batch_id)
    
    try:
        # Perform extraction
        result = await extractor.extract_batch_content_async(
            batch_id,
            progress_callback=lambda p: update_progress(status_msg, p)
        )
        
        # Update progress
        progress_tracker.complete_extraction(user_id, True, result)
        
        # Prepare result message
        summary = format_extraction_summary(result)
        
        # Send file
        if result['file_path'] and os.path.exists(result['file_path']):
            with open(result['file_path'], 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"{sanitize_filename(result['batch_title'])}.txt",
                    caption="📄 Extraction Results"
                )
        
        # Update status with summary
        await status_msg.edit_text(summary, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Extraction failed: {e}", exc_info=True)
        progress_tracker.complete_extraction(user_id, False)
        
        await status_msg.edit_text(
            f"❌ *Extraction Failed*\n\n"
            f"Error: `{str(e)}`\n\n"
            f"Please try again or contact support.",
            parse_mode='Markdown'
        )

async def update_progress(message, progress_data):
    """Update progress message."""
    try:
        text = (
            f"🔄 *Extracting Content...*\n\n"
            f"📚 Topics: {progress_data.get('topics_completed', 0)}/{progress_data.get('total_topics', 0)}\n"
            f"📹 Videos: {progress_data.get('videos_found', 0)}\n"
            f"📄 PDFs: {progress_data.get('pdfs_found', 0)}\n"
            f"📡 HLS Streams: {progress_data.get('hls_found', 0)}\n\n"
            f"⏳ Please wait..."
        )
        await message.edit_text(text, parse_mode='Markdown')
    except Exception:
        pass  # Ignore update errors

async def send_extraction_file(update: Update, context: ContextTypes.DEFAULT_TYPE, file_path: str):
    """Send extraction result file to user."""
    query = update.callback_query
    
    if not os.path.exists(file_path):
        await query.message.reply_text("❌ File not found. It may have been deleted.")
        return
    
    try:
        with open(file_path, 'rb') as f:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=f,
                filename=os.path.basename(file_path)
            )
    except Exception as e:
        await query.message.reply_text(f"❌ Error sending file: {str(e)}")

# ─── Admin Commands ──────────────────────────────────────────────────────────
@admin_only
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panel."""
    keyboard = [
        [InlineKeyboardButton("👥 User Stats", callback_data="admin_users")],
        [InlineKeyboardButton("📊 Bot Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🔧 Maintenance", callback_data="admin_maintenance")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "*🔧 Admin Panel*\n\n"
        "Select an option:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

@admin_only
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast message to all users."""
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    
    message = ' '.join(context.args)
    await update.message.reply_text(f"Broadcasting: {message}")

# ─── Error Handler ──────────────────────────────────────────────────────────
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors."""
    logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)
    
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ An error occurred. Please try again later.\n"
                "Use /start to restart the bot."
            )
    except Exception:
        pass

# ─── Main Application ───────────────────────────────────────────────────────
def setup_application() -> Application:
    """Setup and configure the bot application."""
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("batches", batches_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("extract", extract_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    
    # Admin commands
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    
    # Callback query handler
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Error handler
    application.add_error_handler(error_handler)
    
    return application

async def setup_commands(application: Application):
    """Setup bot commands menu."""
    commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("batches", "List all batches"),
        BotCommand("search", "Search for a batch"),
        BotCommand("extract", "Extract batch content"),
        BotCommand("stats", "View statistics"),
        BotCommand("help", "Show help"),
        BotCommand("cancel", "Cancel operation"),
    ]
    await application.bot.set_my_commands(commands)

def main():
    """Main entry point."""
    print("""
╔══════════════════════════════════════════════════════════╗
║     SelectionWay Telegram Bot                            ║
║     Version 1.0.0                                        ║
╚══════════════════════════════════════════════════════════╝
    """)
    
    # Validate configuration
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ ERROR: Bot token not configured!")
        print("Please set your BOT_TOKEN in config.py or .env file")
        sys.exit(1)
    
    # Setup application
    application = setup_application()
    
    # Setup commands
    application.job_queue.run_once(
        lambda ctx: asyncio.create_task(setup_commands(application)),
        when=1
    )
    
    # Start bot
    print("🚀 Bot is starting...")
    print("Press Ctrl+C to stop")
    
    try:
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
    except KeyboardInterrupt:
        print("\n👋 Bot stopped")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
