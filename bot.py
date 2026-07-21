#!/usr/bin/env python3
"""
SelectionWay Telegram Bot - Koyeb Edition
==========================================
Single-file Telegram bot for extracting SelectionWay batch content.
Extracts ONLY HLS/m3u8 links in clean plain text format.
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
import zipfile

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
    """Async extractor for SelectionWay batches. Extracts ONLY HLS/m3u8 links."""
    
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
        """
        Extract HLS/m3u8 links in clean plain text format.
        Generates TXT and JSON files.
        """
        batch_info = await self.get_batch(course_id)
        if not batch_info:
            raise ValueError("Batch not found")
        
        batch_title = batch_info.get("title", "Unknown")
        faculty_name = (batch_info.get('facultyDetails') or {}).get('name', 'Unknown')
        topics = await self.fetch_topics(course_id)
        
        if not topics:
            raise ValueError("No topics found")
        
        # Prepare filenames
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', batch_title)[:60]
        txt_filename = f"{safe_title}.txt"
        json_filename = f"{safe_title}.json"
        zip_filename = f"{safe_title}.zip"
        
        txt_filepath = TEMP_DIR / txt_filename
        json_filepath = TEMP_DIR / json_filename
        zip_filepath = TEMP_DIR / zip_filename
        
        # Counters
        total_hls = 0
        
        # JSON Structure
        json_data = {
            "course": {
                "id": course_id,
                "title": batch_title,
                "faculty": faculty_name,
                "type": "LIVE" if batch_info.get('isLive') else "Recorded",
                "access": "FREE" if batch_info.get('isFree') else "PAID",
                "extracted_at": datetime.now().isoformat(),
                "total_topics": len(topics)
            },
            "topics": []
        }
        
        # ─── Generate TXT File (Clean Plain Text) ────────────────────────
        
        with open(txt_filepath, "w", encoding="utf-8") as f:
            # Header
            f.write(f"{'='*70}\n")
            f.write(f"  {batch_title}\n")
            f.write(f"  Faculty: {faculty_name}\n")
            f.write(f"  Extracted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"{'='*70}\n\n")
            
            # ─── Process each topic ──────────────────────────────────
            
            for t_idx, topic in enumerate(topics, 1):
                topic_name = topic.get("topicName", f"Topic {t_idx}")
                topic_id = topic.get("topicId", "")
                
                # Topic header
                f.write(f"\n{'─'*70}\n")
                f.write(f"  TOPIC {t_idx}: {topic_name}\n")
                f.write(f"{'─'*70}\n\n")
                
                # JSON topic
                json_topic = {
                    "topic_name": topic_name,
                    "topic_id": topic_id,
                    "topic_number": t_idx,
                    "subtopics": []
                }
                
                # Get classes
                classes = await self.fetch_classes(topic_id, course_id)
                await asyncio.sleep(0.5)
                
                if not classes:
                    f.write("  (No classes)\n\n")
                    json_data["topics"].append(json_topic)
                    if progress_callback:
                        await progress_callback(t_idx, len(topics), total_hls)
                    continue
                
                # Group by subtopic
                groups = defaultdict(list)
                for cls in classes:
                    sub = cls.get("subTopic", {}) or {}
                    sub_name = sub.get("subTopicName", "General")
                    groups[sub_name].append(cls)
                
                for sub_name, sub_classes in groups.items():
                    # Subtopic header
                    f.write(f"  [{sub_name}]\n")
                    
                    # JSON subtopic
                    json_subtopic = {
                        "subtopic_name": sub_name,
                        "classes": []
                    }
                    
                    has_hls = False
                    
                    for cls in sub_classes:
                        title = cls.get("title", "Untitled")
                        class_id = cls.get("classId", "N/A")
                        hls_link = cls.get("class_link", "")
                        
                        if hls_link:
                            has_hls = True
                            # Clean format: Title : Link
                            f.write(f"  {title} : {hls_link}\n")
                            
                            json_subtopic["classes"].append({
                                "title": title,
                                "class_id": class_id,
                                "hls_link": hls_link
                            })
                            total_hls += 1
                    
                    if has_hls:
                        json_topic["subtopics"].append(json_subtopic)
                        f.write("\n")
                
                json_data["topics"].append(json_topic)
                
                if progress_callback:
                    await progress_callback(t_idx, len(topics), total_hls)
            
            # Footer with credit
            f.write(f"\n{'='*70}\n")
            f.write(f"  Total Links: {total_hls}\n")
            f.write(f"{'='*70}\n\n")
            f.write(f"Extractor Bot Made By: http://t.me/anonymousrajput\n")
        
        # ─── Generate JSON File ──────────────────────────────────────────
        
        json_data["summary"] = {
            "total_topics": len(topics),
            "total_hls_links": total_hls,
            "extractor_bot_by": "http://t.me/anonymousrajput"
        }
        
        with open(json_filepath, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        
        # ─── Create ZIP file ─────────────────────────────────────────────
        
        with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(txt_filepath, txt_filename)
            zipf.write(json_filepath, json_filename)
        
        return {
            "batch_title": batch_title,
            "course_id": course_id,
            "txt_file": str(txt_filepath),
            "json_file": str(json_filepath),
            "zip_file": str(zip_filepath),
            "total_hls": total_hls,
            "total_topics": len(topics)
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
        [InlineKeyboardButton("📥 Extract HLS Links", callback_data=f"extract_{batch_id}")],
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
        f"🎓 *SelectionWay HLS Extractor*\n\n"
        f"Welcome, {user.first_name}!\n\n"
        f"*Extracts HLS/m3u8 Links Only*\n\n"
        f"*Output Format:*\n"
        f"```\nTitle : Link\n```\n\n"
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
        "1. /batches - View courses\n"
        "2. Select a batch\n"
        "3. Click 'Extract HLS Links'\n"
        "4. Download ZIP file\n\n"
        "*Output:*\n"
        "📄 Clean TXT: `Title : Link`\n"
        "📋 JSON: Structured data\n\n"
        "*Commands:*\n"
        "/start - Main menu\n"
        "/batches - Browse batches\n"
        "/search <keyword> - Search\n"
        "/stats - Statistics\n"
        "/cancel - Cancel"
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
        f"📄 Format: Title : Link\n"
        f"👤 By: @anonymousrajput"
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
            "🎓 *HLS Link Extractor*\n\n📄 Format: Title : Link",
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
            "🔍 Send me a keyword to search.\nExample: `python`",
            parse_mode='Markdown'
        )
    
    elif data == "stats":
        await query.edit_message_text(
            "*📊 Bot Status*\n\n✅ Running",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Home", callback_data="home")
            ]])
        )
    
    elif data == "help":
        await query.edit_message_text(
            "*📖 Help*\n\nExtracts HLS links.\nFormat: Title : Link",
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
                f"👨‍🏫 Faculty: {faculty}\n\n"
                f"📥 Output: Clean TXT + JSON"
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
    """Perform extraction and send ZIP file."""
    query = update.callback_query
    
    status_msg = await query.message.reply_text(
        "🔄 *Extracting HLS Links...*\n\n"
        "📄 Generating files...\n"
        "📦 Creating ZIP...\n\n"
        "⏳ Please wait...",
        parse_mode='Markdown'
    )
    
    try:
        async def progress(topics_done, total_topics, hls_count):
            pct = (topics_done / total_topics * 100) if total_topics > 0 else 0
            bar = "█" * int(pct/10) + "░" * (10 - int(pct/10))
            try:
                await status_msg.edit_text(
                    f"🔄 *Extracting...*\n\n"
                    f"📊 [{bar}] {pct:.0f}%\n"
                    f"📑 Topics: {topics_done}/{total_topics}\n"
                    f"🔗 Links: {hls_count}",
                    parse_mode='Markdown'
                )
            except:
                pass
        
        result = await extractor.extract_batch(batch_id, progress)
        
        await status_msg.edit_text(
            f"✅ *Done!*\n\n"
            f"📚 {result['batch_title']}\n"
            f"🔗 {result['total_hls']} HLS links\n"
            f"📤 Sending ZIP...",
            parse_mode='Markdown'
        )
        
        # Send ZIP
        zip_path = result['zip_file']
        if Path(zip_path).exists():
            file_size = Path(zip_path).stat().st_size
            
            if file_size < 50 * 1024 * 1024:
                await query.message.reply_document(
                    document=open(zip_path, 'rb'),
                    filename=Path(zip_path).name,
                    caption=(
                        f"📦 *Extraction Complete*\n\n"
                        f"📚 {result['batch_title']}\n"
                        f"🔗 {result['total_hls']} HLS links\n"
                        f"📄 TXT + 📋 JSON"
                    ),
                    parse_mode='Markdown'
                )
            else:
                await query.message.reply_text("⚠️ File too large. Sending separately...")
                
                if Path(result['txt_file']).exists():
                    await query.message.reply_document(
                        document=open(result['txt_file'], 'rb'),
                        filename=Path(result['txt_file']).name,
                        caption="📄 TXT File"
                    )
                
                if Path(result['json_file']).exists():
                    await query.message.reply_document(
                        document=open(result['json_file'], 'rb'),
                        filename=Path(result['json_file']).name,
                        caption="📋 JSON File"
                    )
        
        # Cleanup
        for f in TEMP_DIR.glob("*"):
            if f.stat().st_mtime < (datetime.now().timestamp() - 3600):
                try:
                    f.unlink()
                except:
                    pass
        
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        await status_msg.edit_text(
            f"❌ *Failed*\n`{str(e)[:200]}`",
            parse_mode='Markdown'
        )

# ─── Error Handler ───────────────────────────────────────────────────────────

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}", exc_info=context.error)
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text("❌ An error occurred. Try again.")
    except:
        pass

# ─── Web Server ──────────────────────────────────────────────────────────────

async def health_check(request):
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
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    return app

# ─── Application Setup ───────────────────────────────────────────────────────

def create_bot_app():
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
    
    if WEBHOOK_URL:
        builder.updater(None)
    
    app = builder.build()
    
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
    print("""
╔══════════════════════════════════════╗
║  SelectionWay HLS Extractor          ║
║  Format: Title : Link                ║
║  By: @anonymousrajput                ║
╚══════════════════════════════════════╝
    """)
    
    print(f"🔧 Environment: {'Koyeb' if APP_NAME else 'Local'}")
    print(f"🤖 Bot Token: {'✓ Set' if BOT_TOKEN else '✗ Missing'}")
    print(f"📡 API: {API_BASE}")
    print(f"🌐 Webhook: {WEBHOOK_URL or 'Polling'}")
    
    bot_app = create_bot_app()
    
    if WEBHOOK_URL:
        print(f"🚀 Webhook mode on port {PORT}")
        
        webhook_path = f"/webhook/{BOT_TOKEN}"
        webhook_url = f"{WEBHOOK_URL.rstrip('/')}{webhook_path}"
        
        await bot_app.bot.set_webhook(
            url=webhook_url,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        print(f"✅ Webhook: {webhook_url}")
        
        commands = [
            BotCommand("start", "Start"),
            BotCommand("batches", "Browse"),
            BotCommand("search", "Search"),
            BotCommand("stats", "Stats"),
            BotCommand("help", "Help"),
            BotCommand("cancel", "Cancel"),
        ]
        await bot_app.bot.set_my_commands(commands)
        
        web_app = await create_web_app()
        
        async def webhook_handler(request):
            if request.method == "POST":
                data = await request.json()
                await bot_app.update_queue.put(Update.de_json(data, bot_app.bot))
                return web.Response(status=200)
            return web.Response(status=405)
        
        web_app.router.add_post(webhook_path, webhook_handler)
        
        await bot_app.initialize()
        await bot_app.start()
        
        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        
        print(f"🌐 Health: http://0.0.0.0:{PORT}/health")
        print("✅ Ready!")
        
        try:
            while True:
                await asyncio.sleep(3600)
                for f in TEMP_DIR.glob("*"):
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
        print("🚀 Polling mode")
        
        await bot_app.initialize()
        await bot_app.start()
        
        commands = [
            BotCommand("start", "Start"),
            BotCommand("batches", "Browse"),
            BotCommand("search", "Search"),
            BotCommand("stats", "Stats"),
            BotCommand("help", "Help"),
            BotCommand("cancel", "Cancel"),
        ]
        await bot_app.bot.set_my_commands(commands)
        
        await bot_app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        
        web_app = await create_web_app()
        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        
        print(f"🌐 Health: http://localhost:{PORT}/health")
        print("✅ Ready!")
        
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
        print("\n👋 Stopped")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)
