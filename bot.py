#!/usr/bin/env python3
"""
SelectionWay Telegram Bot - Koyeb Edition
==========================================
Single-file Telegram bot for extracting SelectionWay batch content.
Designed for direct Koyeb deployment without Docker.
"""

import os
import sys
import asyncio
import logging
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from collections import defaultdict

# Third-party imports
import aiohttp
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, ApplicationBuilder,
    CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
    Defaults
)
from telegram.request import HTTPXRequest
from telegram.error import TelegramError

# ─── Configuration ───────────────────────────────────────────────────────────

# REQUIRED: Set this in Koyeb environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# API Configuration
API_BASE = os.getenv("API_BASE", "https://gdgoenkaratia.com/api")
USER_ID = os.getenv("USER_ID", "")

# Koyeb Settings
PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
APP_NAME = os.getenv("KOYEB_APP_NAME", "")

# User Permissions (comma-separated IDs)
ALLOWED_USERS = []
if os.getenv("ALLOWED_USERS"):
    ALLOWED_USERS = [int(uid.strip()) for uid in os.getenv("ALLOWED_USERS").split(",") if uid.strip()]

ADMIN_IDS = []
if os.getenv("ADMIN_IDS"):
    ADMIN_IDS = [int(aid.strip()) for aid in os.getenv("ADMIN_IDS").split(",") if aid.strip()]

# Bot Settings
MAX_BATCHES_PER_PAGE = int(os.getenv("MAX_BATCHES_PER_PAGE", "6"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Temp storage (Koyeb provides /tmp)
TEMP_DIR = Path("/tmp/selectionway")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Validate token
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN environment variable is required!")
    sys.exit(1)

# ─── SelectionWay Extractor ──────────────────────────────────────────────────

class SelectionWayExtractor:
    """Async extractor for SelectionWay batches."""
    
    def __init__(self):
        self.api_base = API_BASE
        self.user_id = USER_ID
        self.session = None
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://www.selectionway.com/",
            "Origin": "https://www.selectionway.com",
        }
    
    async def get_session(self):
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(headers=self.headers, timeout=timeout)
        return self.session
    
    async def api_get(self, endpoint: str, params: dict = None) -> dict:
        """Make API request with retry."""
        if params is None:
            params = {}
        params['userId'] = self.user_id
        
        url = f"{self.api_base}/{endpoint.lstrip('/')}"
        
        for attempt in range(3):
            try:
                session = await self.get_session()
                async with session.get(url, params=params, ssl=False) as resp:
                    if resp.status == 429:
                        await asyncio.sleep(2)
                        continue
                    resp.raise_for_status()
                    data = await resp.json()
                    return data
            except Exception as e:
                if attempt == 2:
                    logger.error(f"API request failed: {e}")
                    return {"state": 0}
                await asyncio.sleep(1)
        
        return {"state": 0}
    
    async def fetch_batches(self) -> List[Dict]:
        """Fetch all active batches."""
        data = await self.api_get("courses/active")
        if data.get("state") == 200:
            return data.get("data", [])
        return []
    
    async def get_batch(self, course_id: str) -> Optional[Dict]:
        """Get specific batch info."""
        batches = await self.fetch_batches()
        for batch in batches:
            if str(batch.get("id", "")) == str(course_id):
                return batch
        return None
    
    async def fetch_topics(self, course_id: str) -> List[Dict]:
        """Fetch topics for a course."""
        data = await self.api_get("topic-and-section", {"courseId": course_id})
        if data.get("state") == 200:
            return data.get("data", {}).get("topics", [])
        return []
    
    async def fetch_classes(self, topic_id: str, course_id: str) -> List[Dict]:
        """Fetch classes for a topic."""
        data = await self.api_get(f"topics/{topic_id}/classes", {"courseId": course_id})
        if data.get("state") == 200:
            return data.get("data", {}).get("classes", [])
        return []
    
    async def extract_batch(self, course_id: str, progress_callback=None) -> Dict:
        """Extract all content from a batch."""
        batch_info = await self.get_batch(course_id)
        if not batch_info:
            raise ValueError("Batch not found")
        
        batch_title = batch_info.get("title", "Unknown")
        topics = await self.fetch_topics(course_id)
        
        if not topics:
            raise ValueError("No topics found")
        
        # Prepare output
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', batch_title)[:80]
        filename = f"{safe_title}.txt"
        filepath = TEMP_DIR / filename
        
        total_videos = 0
        total_pdfs = 0
        total_hls = 0
        
        with open(filepath, "w", encoding="utf-8") as f:
            # Header
            f.write(f"{'='*80}\n")
            f.write(f"  BATCH: {batch_title}\n")
            f.write(f"  Course ID: {course_id}\n")
            f.write(f"  Extracted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"{'='*80}\n\n")
            
            # Process topics
            for t_idx, topic in enumerate(topics, 1):
                topic_name = topic.get("topicName", f"Topic {t_idx}")
                topic_id = topic.get("topicId", "")
                
                f.write(f"\n{'─'*80}\n")
                f.write(f"  TOPIC {t_idx}/{len(topics)}: {topic_name}\n")
                f.write(f"  Topic ID: {topic_id}\n")
                f.write(f"{'─'*80}\n\n")
                
                # Get classes
                classes = await self.fetch_classes(topic_id, course_id)
                await asyncio.sleep(0.5)  # Rate limiting
                
                if not classes:
                    f.write("  (No classes)\n\n")
                    if progress_callback:
                        await progress_callback(t_idx, len(topics), total_videos, total_pdfs)
                    continue
                
                # Group by subtopic
                groups = defaultdict(list)
                for cls in classes:
                    sub = cls.get("subTopic", {}) or {}
                    sub_name = sub.get("subTopicName", "General")
                    groups[sub_name].append(cls)
                
                for sub_name, sub_classes in groups.items():
                    f.write(f"    Subtopic: {sub_name}\n")
                    f.write(f"    Classes: {len(sub_classes)}\n\n")
                    
                    for cls in sub_classes:
                        title = cls.get("title", "Untitled")
                        class_id = cls.get("classId", "N/A")
                        
                        f.write(f"    ├── {title}\n")
                        f.write(f"    │   ID: {class_id}\n")
                        
                        # HLS
                        hls = cls.get("class_link", "")
                        if hls:
                            f.write(f"    │   [HLS] {hls}\n")
                            total_hls += 1
                        
                        # MP4
                        for mp4 in cls.get("mp4Recordings", []):
                            url = mp4.get("url", "")
                            if url:
                                quality = mp4.get("quality", "?")
                                size = mp4.get("size", 0) / (1024*1024)
                                f.write(f"    │   [MP4 {quality}] ({size:.1f}MB) {url}\n")
                                total_videos += 1
                        
                        # PDF
                        for pdf in cls.get("classPdf", []):
                            pdf_url = pdf.get("url", "")
                            if pdf_url:
                                f.write(f"    │   [PDF] {pdf.get('name', 'PDF')}: {pdf_url}\n")
                                total_pdfs += 1
                        
                        f.write("    │\n")
                    
                    f.write("    └──\n\n")
                
                if progress_callback:
                    await progress_callback(t_idx, len(topics), total_videos, total_pdfs)
            
            # Summary
            total_links = total_hls + total_videos + total_pdfs
            f.write(f"\n{'='*80}\n")
            f.write(f"  SUMMARY\n")
            f.write(f"{'='*80}\n")
            f.write(f"  HLS Streams: {total_hls}\n")
            f.write(f"  MP4 Videos : {total_videos}\n")
            f.write(f"  PDFs       : {total_pdfs}\n")
            f.write(f"  Total Links: {total_links}\n")
            f.write(f"{'='*80}\n")
        
        return {
            "batch_title": batch_title,
            "course_id": course_id,
            "file_path": str(filepath),
            "total_hls": total_hls,
            "total_videos": total_videos,
            "total_pdfs": total_pdfs,
            "total_topics": len(topics),
            "total_links": total_links
        }
    
    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

# ─── Initialize ──────────────────────────────────────────────────────────────

extractor = SelectionWayExtractor()

# ─── Keyboard Builders ───────────────────────────────────────────────────────

def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📚 Browse Batches", callback_data="batches")],
        [InlineKeyboardButton("🔍 Search Batch", callback_data="search_prompt")],
        [InlineKeyboardButton("📊 Statistics", callback_data="stats")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)

def batches_keyboard(batches, page=0):
    start = page * MAX_BATCHES_PER_PAGE
    end = start + MAX_BATCHES_PER_PAGE
    page_batches = batches[start:end]
    total_pages = (len(batches) + MAX_BATCHES_PER_PAGE - 1) // MAX_BATCHES_PER_PAGE
    
    keyboard = []
    for batch in page_batches:
        title = batch.get('title', 'Unknown')[:40]
        batch_id = batch.get('id', '')
        icon = "🔴" if batch.get('isLive') else "📺"
        keyboard.append([InlineKeyboardButton(
            f"{icon} {title}", 
            callback_data=f"select_{batch_id}"
        )])
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"page_{page-1}"))
    nav.append(InlineKeyboardButton(f"📄 {page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{page+1}"))
    keyboard.append(nav)
    
    keyboard.append([InlineKeyboardButton("🏠 Home", callback_data="home")])
    return InlineKeyboardMarkup(keyboard)

def batch_detail_keyboard(batch_id):
    keyboard = [
        [InlineKeyboardButton("📥 Extract Content", callback_data=f"extract_{batch_id}")],
        [InlineKeyboardButton("📊 View Topics", callback_data=f"topics_{batch_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="batches"),
         InlineKeyboardButton("🏠 Home", callback_data="home")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ─── Command Handlers ────────────────────────────────────────────────────────

async def check_access(user_id: int) -> bool:
    """Check if user is allowed."""
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler."""
    user = update.effective_user
    
    if not await check_access(user.id):
        await update.message.reply_text("🚫 Access denied.")
        return
    
    msg = (
        f"🎓 *SelectionWay Batch Extractor*\n\n"
        f"Welcome, {user.first_name}!\n\n"
        f"*Commands:*\n"
        f"📚 /batches - Browse batches\n"
        f"🔍 /search - Search batches\n"
        f"📊 /stats - View stats\n"
        f"ℹ️ /help - Help guide"
    )
    
    await update.message.reply_text(
        msg,
        parse_mode='Markdown',
        reply_markup=main_menu_keyboard()
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command."""
    help_text = (
        "*📖 Help Guide*\n\n"
        "*How to extract:*\n"
        "1. Use /batches to see courses\n"
        "2. Select a batch\n"
        "3. Click 'Extract Content'\n"
        "4. Download the text file\n\n"
        "*Commands:*\n"
        "/start - Main menu\n"
        "/batches - Browse batches\n"
        "/search <keyword> - Search\n"
        "/stats - Statistics\n"
        "/cancel - Cancel operation"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def batches_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show batches."""
    if not await check_access(update.effective_user.id):
        return
    
    msg = await update.message.reply_text("🔄 Loading batches...")
    
    try:
        batches = await extractor.fetch_batches()
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")
        return
    
    await msg.delete()
    
    if not batches:
        await update.message.reply_text("❌ No batches found.")
        return
    
    await update.message.reply_text(
        f"📚 *Available Batches* ({len(batches)} total)\n_Tap to select:_",
        parse_mode='Markdown',
        reply_markup=batches_keyboard(batches, 0)
    )

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search batches."""
    if not context.args:
        await update.message.reply_text(
            "Usage: `/search <keyword>`\nExample: `/search python`",
            parse_mode='Markdown'
        )
        return
    
    keyword = ' '.join(context.args).lower()
    msg = await update.message.reply_text(f"🔍 Searching: {keyword}...")
    
    try:
        batches = await extractor.fetch_batches()
        matches = [b for b in batches if keyword in b.get('title', '').lower()]
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")
        return
    
    await msg.delete()
    
    if not matches:
        await update.message.reply_text(f"❌ No results for: {keyword}")
        return
    
    keyboard = []
    for batch in matches[:10]:
        title = batch.get('title', '')[:40]
        keyboard.append([InlineKeyboardButton(
            f"📚 {title}",
            callback_data=f"select_{batch.get('id', '')}"
        )])
    
    keyboard.append([InlineKeyboardButton("🏠 Home", callback_data="home")])
    
    await update.message.reply_text(
        f"🔍 *Results for:* {keyword}\nFound: {len(matches)} batches",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show statistics."""
    stats_text = (
        "*📊 Bot Status*\n\n"
        f"✅ Bot is running\n"
        f"🌐 Environment: {'Koyeb' if APP_NAME else 'Local'}\n"
        f"📡 API: Connected\n"
    )
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel operation."""
    await update.message.reply_text("✅ Operation cancelled.", reply_markup=main_menu_keyboard())

# ─── Callback Handler ────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all callback queries."""
    query = update.callback_query
    data = query.data
    await query.answer()
    
    if data == "home":
        await query.edit_message_text(
            "🎓 *Main Menu*",
            parse_mode='Markdown',
            reply_markup=main_menu_keyboard()
        )
    
    elif data == "batches":
        batches = await extractor.fetch_batches()
        if batches:
            await query.edit_message_text(
                f"📚 *Available Batches* ({len(batches)} total)",
                parse_mode='Markdown',
                reply_markup=batches_keyboard(batches, 0)
            )
        else:
            await query.edit_message_text("❌ No batches found.")
    
    elif data == "search_prompt":
        await query.message.reply_text(
            "🔍 Send me a keyword to search.\nExample: `python course`",
            parse_mode='Markdown'
        )
    
    elif data == "stats":
        await query.edit_message_text(
            "*📊 Bot Status*\n\n✅ Running normally",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Home", callback_data="home")
            ]])
        )
    
    elif data == "help":
        await query.edit_message_text(
            "*📖 Help*\n\nUse /batches to browse\nSelect batch → Extract → Download",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Home", callback_data="home")
            ]])
        )
    
    elif data == "noop":
        pass
    
    elif data.startswith("page_"):
        page = int(data.split("_")[1])
        batches = await extractor.fetch_batches()
        await query.edit_message_text(
            f"📚 *Batches* (Page {page+1})",
            parse_mode='Markdown',
            reply_markup=batches_keyboard(batches, page)
        )
    
    elif data.startswith("select_"):
        batch_id = data.split("_", 1)[1]
        batch = await extractor.get_batch(batch_id)
        if batch:
            faculty = (batch.get('facultyDetails') or {}).get('name', 'N/A')
            info = (
                f"📚 *{batch.get('title')}*\n\n"
                f"🆔 ID: `{batch_id}`\n"
                f"📡 Type: {'🔴 LIVE' if batch.get('isLive') else '📺 Recorded'}\n"
                f"🔐 Access: {'🆓 Free' if batch.get('isFree') else '💎 Paid'}\n"
                f"👨‍🏫 Faculty: {faculty}"
            )
            await query.edit_message_text(
                info,
                parse_mode='Markdown',
                reply_markup=batch_detail_keyboard(batch_id)
            )
    
    elif data.startswith("topics_"):
        batch_id = data.split("_", 1)[1]
        topics = await extractor.fetch_topics(batch_id)
        
        if topics:
            text = f"📑 *Topics* ({len(topics)})\n\n"
            for i, t in enumerate(topics[:15], 1):
                text += f"{i}. {t.get('topicName', 'Unknown')}\n"
            if len(topics) > 15:
                text += f"\n...and {len(topics)-15} more"
        else:
            text = "No topics found."
        
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=batch_detail_keyboard(batch_id)
        )
    
    elif data.startswith("extract_"):
        batch_id = data.split("_", 1)[1]
        await start_extraction(update, context, batch_id)

async def start_extraction(update: Update, context: ContextTypes.DEFAULT_TYPE, batch_id: str):
    """Perform extraction."""
    query = update.callback_query
    
    status_msg = await query.message.reply_text(
        "🔄 *Starting extraction...*\n⏳ Please wait...",
        parse_mode='Markdown'
    )
    
    try:
        async def progress(topics_done, total_topics, videos, pdfs):
            pct = (topics_done / total_topics * 100) if total_topics > 0 else 0
            bar = "█" * int(pct/10) + "░" * (10 - int(pct/10))
            try:
                await status_msg.edit_text(
                    f"🔄 *Extracting...*\n\n"
                    f"[{bar}] {pct:.0f}%\n"
                    f"📑 Topics: {topics_done}/{total_topics}\n"
                    f"📹 Videos: {videos}\n"
                    f"📄 PDFs: {pdfs}",
                    parse_mode='Markdown'
                )
            except:
                pass
        
        result = await extractor.extract_batch(batch_id, progress)
        
        # Send success message
        total = result['total_links']
        await status_msg.edit_text(
            f"✅ *Extraction Complete!*\n\n"
            f"📚 {result['batch_title']}\n"
            f"📹 Videos: {result['total_videos']}\n"
            f"📡 HLS: {result['total_hls']}\n"
            f"📄 PDFs: {result['total_pdfs']}\n"
            f"📎 Total: {total} links",
            parse_mode='Markdown'
        )
        
        # Send file
        filepath = result['file_path']
        if Path(filepath).exists():
            file_size = Path(filepath).stat().st_size
            if file_size < 50 * 1024 * 1024:  # 50MB limit
                await query.message.reply_document(
                    document=open(filepath, 'rb'),
                    filename=Path(filepath).name,
                    caption="📄 Extraction Results"
                )
            else:
                await query.message.reply_text(
                    f"⚠️ File too large ({file_size/1024/1024:.1f}MB). Max 50MB."
                )
        
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        await status_msg.edit_text(
            f"❌ *Extraction failed*\n`{str(e)[:200]}`",
            parse_mode='Markdown'
        )

# ─── Error Handler ───────────────────────────────────────────────────────────

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}", exc_info=context.error)
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ An error occurred. Please try again."
            )
    except:
        pass

# ─── Web Server for Koyeb ───────────────────────────────────────────────────

async def health_check(request):
    """Health check endpoint for Koyeb."""
    return web.Response(
        text=json.dumps({
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "app": APP_NAME or "local"
        }),
        content_type="application/json",
        status=200
    )

async def create_web_app():
    """Create aiohttp web app for health checks."""
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    return app

# ─── Application Setup ───────────────────────────────────────────────────────

def create_bot_app():
    """Create and configure the bot application."""
    request = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=10.0,
        read_timeout=30.0,
        write_timeout=10.0
    )
    
    builder = ApplicationBuilder() \
        .token(BOT_TOKEN) \
        .request(request) \
        .defaults(Defaults(parse_mode='Markdown'))
    
    # Use updater for polling, None for webhook
    if not WEBHOOK_URL:
        # Local development - use polling
        pass
    else:
        # Production - use webhook
        builder.updater(None)
    
    app = builder.build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("batches", batches_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_error_handler(error_handler)
    
    return app

# ─── Main ────────────────────────────────────────────────────────────────────

async def main():
    """Main entry point."""
    print("""
╔══════════════════════════════════════╗
║  SelectionWay Telegram Bot           ║
║  Koyeb Edition v2.0                  ║
╚══════════════════════════════════════╝
    """)
    
    print(f"🔧 Environment: {'Koyeb' if APP_NAME else 'Local'}")
    print(f"🤖 Bot Token: {'✓ Set' if BOT_TOKEN else '✗ Missing'}")
    print(f"📡 API: {API_BASE}")
    print(f"🌐 Webhook: {WEBHOOK_URL or 'Using polling'}")
    
    # Create bot application
    bot_app = create_bot_app()
    
    if WEBHOOK_URL:
        # Production mode with webhook
        print(f"🚀 Starting in WEBHOOK mode on port {PORT}")
        
        # Setup webhook
        webhook_path = f"/webhook/{BOT_TOKEN}"
        webhook_url = f"{WEBHOOK_URL.rstrip('/')}{webhook_path}"
        
        await bot_app.bot.set_webhook(
            url=webhook_url,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        print(f"✅ Webhook set: {webhook_url}")
        
        # Setup commands
        commands = [
            BotCommand("start", "Start the bot"),
            BotCommand("batches", "Browse batches"),
            BotCommand("search", "Search batches"),
            BotCommand("stats", "View stats"),
            BotCommand("help", "Help guide"),
            BotCommand("cancel", "Cancel operation"),
        ]
        await bot_app.bot.set_my_commands(commands)
        
        # Create web server
        web_app = await create_web_app()
        
        # Add webhook route
        async def webhook_handler(request):
            """Handle incoming webhook updates."""
            if request.method == "POST":
                data = await request.json()
                await bot_app.update_queue.put(
                    Update.de_json(data, bot_app.bot)
                )
                return web.Response(status=200)
            return web.Response(status=405)
        
        web_app.router.add_post(webhook_path, webhook_handler)
        
        # Initialize and start bot
        await bot_app.initialize()
        await bot_app.start()
        
        # Start web server
        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        
        print(f"🌐 Health check: http://0.0.0.0:{PORT}/health")
        print("✅ Bot is ready!")
        
        # Keep running
        try:
            while True:
                await asyncio.sleep(3600)
                # Periodic cleanup
                for f in TEMP_DIR.glob("*.txt"):
                    if f.stat().st_mtime < (datetime.now().timestamp() - 86400):
                        f.unlink()
        except asyncio.CancelledError:
            pass
        finally:
            await bot_app.stop()
            await bot_app.shutdown()
            await runner.cleanup()
            await extractor.close()
    
    else:
        # Development mode with polling
        print("🚀 Starting in POLLING mode")
        
        await bot_app.initialize()
        await bot_app.start()
        
        # Setup commands
        commands = [
            BotCommand("start", "Start the bot"),
            BotCommand("batches", "Browse batches"),
            BotCommand("search", "Search batches"),
            BotCommand("stats", "View stats"),
            BotCommand("help", "Help guide"),
            BotCommand("cancel", "Cancel operation"),
        ]
        await bot_app.bot.set_my_commands(commands)
        
        # Start polling
        await bot_app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        
        # Also start health check server
        web_app = await create_web_app()
        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        
        print(f"🌐 Health check: http://localhost:{PORT}/health")
        print("✅ Bot is ready!")
        
        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await bot_app.updater.stop()
            await bot_app.stop()
            await bot_app.shutdown()
            await runner.cleanup()
            await extractor.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Bot stopped")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        sys.exit(1)
