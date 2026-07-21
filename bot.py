#!/usr/bin/env python3
"""
SelectionWay Telegram Bot - Koyeb Optimized
============================================
Complete Telegram bot for extracting SelectionWay batch content.
Optimized for Koyeb serverless deployment.

Author: SelectionWay Bot
Version: 2.0.0
"""

import os
import sys
import asyncio
import logging
import json
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field
from collections import defaultdict
from pathlib import Path
import tempfile
import shutil
from urllib.parse import urljoin

# Fix Windows console encoding (for local testing)
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    os.environ['PYTHONIOENCODING'] = 'utf-8'

# Third-party imports
import aiohttp
from aiohttp import ClientTimeout, ClientSession, TCPConnector
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    BotCommand, BotCommandScopeDefault, constants, helpers
)
from telegram.ext import (
    Application, ApplicationBuilder,
    CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
    ConversationHandler, Defaults
)
from telegram.error import TelegramError, NetworkError, TimedOut
from telegram.request import HTTPXRequest

# Try to load dotenv (optional for local development)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─── Configuration ───────────────────────────────────────────────────────────

# Bot Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    print("❌ ERROR: BOT_TOKEN environment variable is required!")
    sys.exit(1)

# API Configuration
API_BASE = os.getenv("API_BASE", "https://gdgoenkaratia.com/api")
USER_ID = os.getenv("USER_ID", "")

# Koyeb specific settings
PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
IS_PRODUCTION = os.getenv("KOYEB_APP_NAME", "") != ""
ENVIRONMENT = os.getenv("ENVIRONMENT", "production" if IS_PRODUCTION else "development")

# User Permissions
ALLOWED_USERS = []
if os.getenv("ALLOWED_USERS"):
    try:
        ALLOWED_USERS = [int(uid.strip()) for uid in os.getenv("ALLOWED_USERS").split(",") if uid.strip()]
    except ValueError:
        pass

ADMIN_IDS = []
if os.getenv("ADMIN_IDS"):
    try:
        ADMIN_IDS = [int(aid.strip()) for aid in os.getenv("ADMIN_IDS").split(",") if aid.strip()]
    except ValueError:
        pass

# Bot Settings
MAX_BATCHES_PER_PAGE = int(os.getenv("MAX_BATCHES_PER_PAGE", "8"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
RATE_LIMIT_DELAY = float(os.getenv("RATE_LIMIT_DELAY", "0.5"))

# Storage paths (use /tmp for Koyeb ephemeral storage)
TEMP_DIR = Path(os.getenv("TEMP_DIR", "/tmp/selectionway" if IS_PRODUCTION else "./temp"))
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/tmp/selectionway/downloads" if IS_PRODUCTION else "./downloads"))
TEMP_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ─── Logging Setup ───────────────────────────────────────────────────────────

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(TEMP_DIR / "bot.log") if not IS_PRODUCTION else logging.NullHandler()
    ]
)
logger = logging.getLogger(__name__)

# ─── Data Classes ────────────────────────────────────────────────────────────

@dataclass
class BatchInfo:
    """Batch/Course information."""
    id: str
    title: str
    is_live: bool = False
    is_free: bool = False
    faculty_name: str = "Unknown"
    description: str = ""
    language: str = ""
    thumbnail: str = ""

@dataclass
class ExtractionResult:
    """Extraction result data."""
    batch_title: str
    course_id: str
    file_path: str
    file_content: str = ""
    total_topics: int = 0
    total_classes: int = 0
    total_hls: int = 0
    total_videos: int = 0
    total_pdfs: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

@dataclass
class UserSession:
    """User session data."""
    user_id: int
    username: str = ""
    current_page: int = 0
    search_query: str = ""
    selected_batch_id: str = ""
    extraction_count: int = 0
    last_activity: datetime = field(default_factory=datetime.now)

# ─── Progress Tracker ────────────────────────────────────────────────────────

class ProgressTracker:
    """Track bot usage statistics."""
    
    def __init__(self):
        self.sessions: Dict[int, UserSession] = {}
        self.stats = {
            'total_users': set(),
            'total_extractions': 0,
            'successful_extractions': 0,
            'failed_extractions': 0,
            'total_videos_found': 0,
            'total_pdfs_found': 0,
            'daily_extractions': defaultdict(int),
            'start_time': datetime.now()
        }
    
    def get_session(self, user_id: int, username: str = "") -> UserSession:
        """Get or create user session."""
        if user_id not in self.sessions:
            self.sessions[user_id] = UserSession(user_id=user_id, username=username)
            self.stats['total_users'].add(user_id)
        else:
            self.sessions[user_id].last_activity = datetime.now()
        return self.sessions[user_id]
    
    def record_extraction(self, user_id: int, success: bool, result: ExtractionResult = None):
        """Record extraction attempt."""
        self.stats['total_extractions'] += 1
        today = datetime.now().strftime('%Y-%m-%d')
        self.stats['daily_extractions'][today] += 1
        
        if success and result:
            self.stats['successful_extractions'] += 1
            self.stats['total_videos_found'] += result.total_videos
            self.stats['total_pdfs_found'] += result.total_pdfs
            
            if user_id in self.sessions:
                self.sessions[user_id].extraction_count += 1
        else:
            self.stats['failed_extractions'] += 1
    
    def get_stats_summary(self) -> Dict:
        """Get statistics summary."""
        total = self.stats['total_extractions']
        success_rate = (self.stats['successful_extractions'] / total * 100) if total > 0 else 0
        uptime = datetime.now() - self.stats['start_time']
        
        return {
            'total_users': len(self.stats['total_users']),
            'total_extractions': total,
            'successful': self.stats['successful_extractions'],
            'failed': self.stats['failed_extractions'],
            'success_rate': round(success_rate, 1),
            'total_videos': self.stats['total_videos_found'],
            'total_pdfs': self.stats['total_pdfs_found'],
            'today_extractions': self.stats['daily_extractions'].get(datetime.now().strftime('%Y-%m-%d'), 0),
            'uptime': str(uptime).split('.')[0],
            'environment': ENVIRONMENT
        }
    
    def cleanup_old_sessions(self, max_age_hours: int = 24):
        """Remove inactive sessions."""
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        inactive = [
            uid for uid, session in self.sessions.items()
            if session.last_activity < cutoff
        ]
        for uid in inactive:
            del self.sessions[uid]

# ─── SelectionWay Extractor ──────────────────────────────────────────────────

class SelectionWayExtractor:
    """Async extractor for SelectionWay batch content."""
    
    def __init__(self, api_base: str = API_BASE, user_id: str = USER_ID):
        self.api_base = api_base.rstrip('/')
        self.user_id = user_id
        self.session: Optional[ClientSession] = None
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.selectionway.com/",
            "Origin": "https://www.selectionway.com",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
        }
    
    async def _get_session(self) -> ClientSession:
        """Get or create aiohttp session with connection pooling."""
        if self.session is None or self.session.closed:
            connector = TCPConnector(
                limit=10,
                ttl_dns_cache=300,
                force_close=False,
                enable_cleanup_closed=True
            )
            timeout = ClientTimeout(total=REQUEST_TIMEOUT, connect=10)
            self.session = ClientSession(
                headers=self.headers,
                timeout=timeout,
                connector=connector
            )
        return self.session
    
    async def _api_request(self, endpoint: str, params: Dict = None) -> Dict:
        """Make async API request with retry logic."""
        if params is None:
            params = {}
        params['userId'] = self.user_id
        
        url = f"{self.api_base}/{endpoint.lstrip('/')}"
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                session = await self._get_session()
                async with session.get(url, params=params, ssl=False) as response:
                    if response.status == 429:  # Rate limit
                        wait_time = int(response.headers.get('Retry-After', 5))
                        logger.warning(f"Rate limited. Waiting {wait_time}s")
                        await asyncio.sleep(wait_time)
                        continue
                    
                    response.raise_for_status()
                    data = await response.json()
                    
                    # Check API response state
                    if data.get("state") == 429:
                        await asyncio.sleep(2)
                        continue
                    
                    return data
                    
            except asyncio.TimeoutError:
                logger.warning(f"Request timeout (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise
            except aiohttp.ClientError as e:
                logger.error(f"Request error: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise
        
        return {"state": 0, "message": "Max retries exceeded", "data": {}}
    
    async def fetch_batches(self) -> List[Dict]:
        """Fetch all active batches."""
        try:
            data = await self._api_request("courses/active")
            if data.get("state") == 200:
                return data.get("data", [])
            logger.error(f"API error fetching batches: {data.get('message')}")
            return []
        except Exception as e:
            logger.error(f"Failed to fetch batches: {e}")
            raise
    
    async def get_batch_info(self, course_id: str) -> Optional[Dict]:
        """Get specific batch information."""
        batches = await self.fetch_batches()
        for batch in batches:
            if str(batch.get("id", "")) == str(course_id):
                return batch
        return None
    
    async def fetch_topics(self, course_id: str) -> List[Dict]:
        """Fetch topics for a course."""
        try:
            data = await self._api_request("topic-and-section", {"courseId": course_id})
            if data.get("state") == 200:
                return data.get("data", {}).get("topics", [])
            return []
        except Exception as e:
            logger.error(f"Failed to fetch topics: {e}")
            return []
    
    async def fetch_classes(self, topic_id: str, course_id: str) -> List[Dict]:
        """Fetch classes for a topic."""
        try:
            endpoint = f"topics/{topic_id}/classes"
            data = await self._api_request(endpoint, {"courseId": course_id})
            if data.get("state") == 200:
                return data.get("data", {}).get("classes", [])
            return []
        except Exception as e:
            logger.error(f"Failed to fetch classes: {e}")
            return []
    
    async def extract_batch_content(
        self,
        course_id: str,
        progress_callback: Optional[Callable] = None
    ) -> ExtractionResult:
        """Extract all content from a batch."""
        
        # Get batch info
        batch_info = await self.get_batch_info(course_id)
        if not batch_info:
            raise ValueError(f"Batch not found: {course_id}")
        
        batch_title = batch_info.get("title", "Unknown Batch")
        
        # Get topics
        topics = await self.fetch_topics(course_id)
        if not topics:
            raise ValueError(f"No topics found for batch: {batch_title}")
        
        # Initialize counters
        total_topics = len(topics)
        topics_completed = 0
        total_classes = 0
        total_videos = 0
        total_pdfs = 0
        total_hls = 0
        
        # Generate filename
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', batch_title)
        safe_title = re.sub(r'\s+', '_', safe_title)[:100]
        filename = f"{safe_title}_{course_id}.txt"
        filepath = DOWNLOAD_DIR / filename
        
        # Build content
        content_lines = []
        
        # Header
        faculty_name = batch_info.get('facultyDetails', {}).get('name', 'N/A')
        content_lines.extend([
            f"{'='*80}",
            f"  BATCH: {batch_title}",
            f"  Course ID: {course_id}",
            f"  Faculty: {faculty_name}",
            f"  Type: {'LIVE' if batch_info.get('isLive') else 'Recorded'}",
            f"  Access: {'FREE' if batch_info.get('isFree') else 'PAID'}",
            f"  Extracted on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"  Total Topics: {total_topics}",
            f"{'='*80}",
            ""
        ])
        
        # Process each topic
        for t_idx, topic in enumerate(topics, 1):
            topic_name = topic.get("topicName", f"Topic {t_idx}")
            topic_id = topic.get("topicId", "")
            
            content_lines.extend([
                f"{'─'*80}",
                f"  TOPIC {t_idx}/{total_topics}: {topic_name}",
                f"  Topic ID: {topic_id}",
                f"{'─'*80}",
                ""
            ])
            
            # Fetch classes
            try:
                classes = await self.fetch_classes(topic_id, course_id)
                await asyncio.sleep(RATE_LIMIT_DELAY)
            except Exception as e:
                logger.error(f"Error fetching classes for topic {topic_id}: {e}")
                content_lines.append(f"  [ERROR] Failed to fetch classes: {e}\n")
                topics_completed += 1
                
                if progress_callback:
                    await progress_callback(topics_completed, total_topics, total_videos, total_pdfs)
                continue
            
            if not classes:
                content_lines.append("  (No classes found)\n")
                topics_completed += 1
                
                if progress_callback:
                    await progress_callback(topics_completed, total_topics, total_videos, total_pdfs)
                continue
            
            total_classes += len(classes)
            
            # Group by subtopic
            subtopic_groups = defaultdict(list)
            for cls in classes:
                sub = cls.get("subTopic", {}) or {}
                sub_name = sub.get("subTopicName", "General")
                subtopic_groups[sub_name].append(cls)
            
            # Write classes
            for sub_name, sub_classes in subtopic_groups.items():
                content_lines.extend([
                    f"    ┌── Subtopic: {sub_name}",
                    f"    │   Classes: {len(sub_classes)}",
                    f"    │"
                ])
                
                for cls in sub_classes:
                    title = cls.get("title", "Untitled")
                    class_id = cls.get("classId", "N/A")
                    
                    content_lines.extend([
                        f"    ├── {title}",
                        f"    │   Class ID: {class_id}"
                    ])
                    
                    # HLS Link
                    hls_link = cls.get("class_link", "")
                    if hls_link:
                        content_lines.append(f"    │   [HLS STREAM] {hls_link}")
                        total_hls += 1
                    
                    # MP4 Links
                    mp4s = cls.get("mp4Recordings", [])
                    if mp4s:
                        for mp4 in mp4s:
                            quality = mp4.get("quality", "?")
                            url = mp4.get("url", "")
                            size = mp4.get("size", 0)
                            if url:
                                size_mb = size / (1024 * 1024) if size else 0
                                content_lines.append(f"    │   [MP4 {quality}] ({size_mb:.1f}MB) {url}")
                                total_videos += 1
                    
                    # PDF Links
                    pdfs = cls.get("classPdf", [])
                    if pdfs:
                        for pdf in pdfs:
                            pdf_name = pdf.get("name", "PDF")
                            pdf_url = pdf.get("url", "")
                            if pdf_url:
                                content_lines.append(f"    │   [PDF] {pdf_name}: {pdf_url}")
                                total_pdfs += 1
                    
                    content_lines.append("    │")
                
                content_lines.append("    └──\n")
            
            topics_completed += 1
            
            # Progress update
            if progress_callback:
                await progress_callback(topics_completed, total_topics, total_videos, total_pdfs)
        
        # Footer/Summary
        total_links = total_hls + total_videos + total_pdfs
        content_lines.extend([
            f"{'='*80}",
            f"  EXTRACTION SUMMARY",
            f"{'='*80}",
            f"  Topics Processed : {topics_completed}/{total_topics}",
            f"  Total Classes    : {total_classes}",
            f"  HLS Streams      : {total_hls}",
            f"  MP4 Videos       : {total_videos}",
            f"  PDF Documents    : {total_pdfs}",
            f"  Total Links      : {total_links}",
            f"{'='*80}",
            f"  Generated by SelectionWay Bot",
            f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"{'='*80}"
        ])
        
        # Write to file
        content = "\n".join(content_lines)
        filepath.write_text(content, encoding='utf-8')
        
        return ExtractionResult(
            batch_title=batch_title,
            course_id=course_id,
            file_path=str(filepath),
            file_content=content,
            total_topics=total_topics,
            total_classes=total_classes,
            total_hls=total_hls,
            total_videos=total_videos,
            total_pdfs=total_pdfs
        )
    
    async def close(self):
        """Close HTTP session."""
        if self.session and not self.session.closed:
            await self.session.close()

# ─── Initialize Global Objects ───────────────────────────────────────────────

extractor = SelectionWayExtractor()
progress_tracker = ProgressTracker()

# ─── Helper Functions ────────────────────────────────────────────────────────

def create_main_keyboard() -> InlineKeyboardMarkup:
    """Create main menu keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("📚 Browse Batches", callback_data="batches"),
            InlineKeyboardButton("🔍 Search", callback_data="search_prompt")
        ],
        [
            InlineKeyboardButton("📊 Statistics", callback_data="stats"),
            InlineKeyboardButton("ℹ️ Help", callback_data="help")
        ],
        [
            InlineKeyboardButton("🔄 Refresh Cache", callback_data="refresh")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_batch_navigation_keyboard(
    batches: List[Dict],
    page: int,
    total_pages: int
) -> InlineKeyboardMarkup:
    """Create paginated batch list keyboard."""
    start_idx = page * MAX_BATCHES_PER_PAGE
    end_idx = min(start_idx + MAX_BATCHES_PER_PAGE, len(batches))
    page_batches = batches[start_idx:end_idx]
    
    keyboard = []
    
    # Batch selection buttons
    for batch in page_batches:
        title = batch.get('title', 'Unknown')[:45]
        batch_id = batch.get('id', '')
        is_live = "🔴" if batch.get('isLive') else "📺"
        is_free = "🆓" if batch.get('isFree') else "💎"
        
        keyboard.append([InlineKeyboardButton(
            f"{is_live} {is_free} {title}",
            callback_data=f"select_{batch_id}"
        )])
    
    # Navigation row
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"page_{page-1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{page+1}"))
    keyboard.append(nav_row)
    
    # Action row
    keyboard.append([
        InlineKeyboardButton("🔍 Search", callback_data="search_prompt"),
        InlineKeyboardButton("🏠 Home", callback_data="home")
    ])
    
    return InlineKeyboardMarkup(keyboard)

def create_batch_detail_keyboard(batch_id: str) -> InlineKeyboardMarkup:
    """Create batch detail action keyboard."""
    keyboard = [
        [InlineKeyboardButton("📥 Extract Content", callback_data=f"extract_{batch_id}")],
        [
            InlineKeyboardButton("📊 View Topics", callback_data=f"topics_{batch_id}"),
            InlineKeyboardButton("ℹ️ Details", callback_data=f"details_{batch_id}")
        ],
        [
            InlineKeyboardButton("⬅️ Back to List", callback_data="batches"),
            InlineKeyboardButton("🏠 Home", callback_data="home")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def format_batch_info(batch: Dict) -> str:
    """Format batch information for display."""
    faculty = batch.get('facultyDetails', {}) or {}
    faculty_name = faculty.get('name', 'Not specified')
    
    info = (
        f"📚 *{batch.get('title', 'Unknown Batch')}*\n\n"
        f"🆔 *Course ID:* `{batch.get('id', 'N/A')}`\n"
        f"📡 *Type:* {'🔴 LIVE' if batch.get('isLive') else '📺 Recorded'}\n"
        f"🔐 *Access:* {'🆓 Free' if batch.get('isFree') else '💎 Paid'}\n"
        f"👨‍🏫 *Faculty:* {faculty_name}\n"
    )
    
    if batch.get('language'):
        info += f"🌐 *Language:* {batch['language']}\n"
    
    if batch.get('description'):
        desc = batch['description'][:300]
        info += f"\n📝 *Description:*\n{desc}...\n"
    
    return info

def format_extraction_result(result: ExtractionResult) -> str:
    """Format extraction result message."""
    total_links = result.total_hls + result.total_videos + result.total_pdfs
    return (
        f"✅ *Extraction Complete!*\n\n"
        f"📚 *Batch:* {result.batch_title}\n"
        f"🆔 *Course ID:* `{result.course_id}`\n\n"
        f"📊 *Results:*\n"
        f"├ 📑 Topics: {result.total_topics}\n"
        f"├ 🎬 Classes: {result.total_classes}\n"
        f"├ 📹 MP4 Videos: {result.total_videos}\n"
        f"├ 📡 HLS Streams: {result.total_hls}\n"
        f"├ 📄 PDFs: {result.total_pdfs}\n"
        f"└ 📎 *Total Links:* {total_links}\n\n"
        f"📁 *File:* `{Path(result.file_path).name}`"
    )

def split_long_message(text: str, max_length: int = 4000) -> List[str]:
    """Split long message into Telegram-safe chunks."""
    if len(text) <= max_length:
        return [text]
    
    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        
        # Try to split at newline
        split_point = text.rfind('\n', 0, max_length)
        if split_point == -1 or split_point < max_length // 2:
            split_point = text.rfind(' ', 0, max_length)
        if split_point == -1:
            split_point = max_length
        
        chunks.append(text[:split_point])
        text = text[split_point:].lstrip()
    
    return chunks

# ─── Decorators ──────────────────────────────────────────────────────────────

def restricted(func):
    """Restrict command to allowed users."""
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not update or not update.effective_user:
            return
        
        user_id = update.effective_user.id
        
        # If ALLOWED_USERS is empty, allow all users
        if ALLOWED_USERS and user_id not in ALLOWED_USERS:
            await update.message.reply_text(
                "🚫 *Access Denied*\n\n"
                "You are not authorized to use this bot.\n"
                "Please contact the administrator.",
                parse_mode='Markdown'
            )
            logger.warning(f"Unauthorized access attempt: {user_id}")
            return ConversationHandler.END
        
        # Track user session
        username = update.effective_user.username or update.effective_user.first_name or "Unknown"
        progress_tracker.get_session(user_id, username)
        
        return await func(update, context, *args, **kwargs)
    return wrapped

def admin_only(func):
    """Restrict command to admin users."""
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not update or not update.effective_user:
            return
        
        user_id = update.effective_user.id
        
        if user_id not in ADMIN_IDS:
            await update.message.reply_text(
                "⛔ *Admin Access Required*\n\n"
                "This command is restricted to bot administrators.",
                parse_mode='Markdown'
            )
            return
        
        return await func(update, context, *args, **kwargs)
    return wrapped

async def send_typing_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send typing action while processing."""
    if update.effective_chat:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action=constants.ChatAction.TYPING
        )

# ─── Command Handlers ────────────────────────────────────────────────────────

@restricted
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user = update.effective_user
    
    welcome_msg = (
        f"🎓 *SelectionWay Batch Extractor*\n\n"
        f"Welcome, {user.first_name}! 👋\n\n"
        f"I can help you extract video and PDF links from SelectionWay batches.\n\n"
        f"*Quick Start:*\n"
        f"1️⃣ Browse available batches\n"
        f"2️⃣ Select a batch to extract\n"
        f"3️⃣ Download the file with all links\n\n"
        f"*Available commands:*\n"
        f"📚 /batches - View all batches\n"
        f"🔍 /search - Search batches\n"
        f"📊 /stats - View statistics\n"
        f"ℹ️ /help - Detailed help"
    )
    
    await update.message.reply_text(
        welcome_msg,
        parse_mode='Markdown',
        reply_markup=create_main_keyboard()
    )

@restricted
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    help_text = (
        "*📖 Help & Guide*\n\n"
        "*Core Commands:*\n"
        "• /start - Main menu\n"
        "• /batches - List all available batches\n"
        "• /search <keyword> - Search for batches\n"
        "• /stats - View bot statistics\n"
        "• /cancel - Cancel current operation\n\n"
        
        "*How to Extract Content:*\n"
        "1. Use /batches to see available courses\n"
        "2. Tap on a batch to view details\n"
        "3. Click 'Extract Content' button\n"
        "4. Wait for extraction to complete\n"
        "5. Download the generated text file\n\n"
        
        "*File Contents:*\n"
        "• All MP4 video links with quality info\n"
        "• HLS streaming links\n"
        "• PDF document links\n"
        "• Organized by topics and subtopics\n\n"
        
        "*Tips:*\n"
        "• Large batches may take 1-2 minutes\n"
        "• Files are automatically cleaned after 24h\n"
        "• Use search for quick access to specific batches"
    )
    
    keyboard = [[InlineKeyboardButton("🏠 Back to Home", callback_data="home")]]
    
    await update.message.reply_text(
        help_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

@restricted
async def batches_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /batches command - show paginated batch list."""
    await show_batches_page(update, context, page=0)

async def show_batches_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    """Display paginated batch list."""
    query = update.callback_query
    message = query.message if query else update.message
    
    # Send loading message
    loading_msg = await message.reply_text(
        "🔄 *Loading batches...*",
        parse_mode='Markdown'
    )
    
    try:
        batches = await extractor.fetch_batches()
    except Exception as e:
        logger.error(f"Failed to fetch batches: {e}")
        await loading_msg.edit_text(
            "❌ *Error loading batches*\n\n"
            "Please try again later or contact support.",
            parse_mode='Markdown'
        )
        return
    
    await loading_msg.delete()
    
    if not batches:
        await message.reply_text(
            "❌ *No batches found*\n\n"
            "Please check your API configuration.",
            parse_mode='Markdown'
        )
        return
    
    # Calculate pagination
    total_pages = (len(batches) + MAX_BATCHES_PER_PAGE - 1) // MAX_BATCHES_PER_PAGE
    
    # Create message
    header = f"📚 *Available Batches* ({len(batches)} total)\n"
    header += f"Page {page + 1} of {total_pages}\n\n"
    header += "_Tap a batch to view details and extract content:_\n\n"
    
    # Create keyboard
    keyboard = create_batch_navigation_keyboard(batches, page, total_pages)
    
    if query:
        try:
            await query.edit_message_text(
                header,
                parse_mode='Markdown',
                reply_markup=keyboard
            )
        except Exception:
            await message.reply_text(
                header,
                parse_mode='Markdown',
                reply_markup=keyboard
            )
    else:
        await message.reply_text(
            header,
            parse_mode='Markdown',
            reply_markup=keyboard
        )

@restricted
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /search command."""
    if not context.args:
        await update.message.reply_text(
            "🔍 *Search Batches*\n\n"
            "Usage: `/search <keyword>`\n\n"
            "Example: `/search python`\n"
            "Example: `/search data science`",
            parse_mode='Markdown'
        )
        return
    
    keyword = ' '.join(context.args).lower().strip()
    await perform_search(update, context, keyword)

async def perform_search(update: Update, context: ContextTypes.DEFAULT_TYPE, keyword: str):
    """Execute search and display results."""
    loading_msg = await update.effective_message.reply_text(
        f"🔍 *Searching for:* _{keyword}_",
        parse_mode='Markdown'
    )
    
    try:
        batches = await extractor.fetch_batches()
        matching = [
            b for b in batches
            if keyword in b.get('title', '').lower() or
               keyword in b.get('description', '').lower() or
               keyword in (b.get('facultyDetails', {}) or {}).get('name', '').lower()
        ]
    except Exception as e:
        logger.error(f"Search failed: {e}")
        await loading_msg.edit_text(f"❌ Search failed: {str(e)}")
        return
    
    await loading_msg.delete()
    
    if not matching:
        keyboard = [[InlineKeyboardButton("🔍 New Search", callback_data="search_prompt")]]
        await update.effective_message.reply_text(
            f"❌ *No results found for:* _{keyword}_\n\n"
            "Try different keywords or browse all batches.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # Show results
    result_text = f"🔍 *Search Results:* _{keyword}_\n"
    result_text += f"Found {len(matching)} batch(es)\n\n"
    
    keyboard = []
    for batch in matching[:10]:
        title = batch.get('title', 'Unknown')[:40]
        batch_id = batch.get('id', '')
        keyboard.append([InlineKeyboardButton(
            f"📚 {title}",
            callback_data=f"select_{batch_id}"
        )])
    
    if len(matching) > 10:
        result_text += f"_Showing first 10 of {len(matching)} results_\n"
    
    keyboard.append([InlineKeyboardButton("🏠 Home", callback_data="home")])
    
    await update.effective_message.reply_text(
        result_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

@restricted
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command."""
    stats = progress_tracker.get_stats_summary()
    
    stats_text = (
        "*📊 Bot Statistics*\n\n"
        f"👥 *Users:* {stats['total_users']}\n"
        f"📥 *Total Extractions:* {stats['total_extractions']}\n"
        f"✅ *Successful:* {stats['successful']}\n"
        f"❌ *Failed:* {stats['failed']}\n"
        f"📈 *Success Rate:* {stats['success_rate']}%\n\n"
        f"📹 *Videos Found:* {stats['total_videos']}\n"
        f"📄 *PDFs Found:* {stats['total_pdfs']}\n\n"
        f"📅 *Today:* {stats['today_extractions']} extractions\n"
        f"⏱️ *Uptime:* {stats['uptime']}\n"
        f"🌍 *Environment:* {stats['environment']}"
    )
    
    keyboard = [
        [InlineKeyboardButton("🔄 Refresh", callback_data="stats")],
        [InlineKeyboardButton("🏠 Home", callback_data="home")]
    ]
    
    await update.message.reply_text(
        stats_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

@restricted
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cancel command."""
    # Clean up user data
    user_id = update.effective_user.id
    context.user_data.clear()
    
    await update.message.reply_text(
        "✅ *Operation Cancelled*\n\n"
        "Use /start to begin again.",
        parse_mode='Markdown',
        reply_markup=create_main_keyboard()
    )
    return ConversationHandler.END

# ─── Callback Query Handlers ─────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main callback query handler."""
    query = update.callback_query
    data = query.data
    
    await query.answer()
    
    # Navigation handlers
    if data == "home":
        await show_home(update, context)
    
    elif data == "batches":
        await show_batches_page(update, context, page=0)
    
    elif data == "search_prompt":
        await query.message.reply_text(
            "🔍 *Search Batches*\n\n"
            "Please send me a keyword to search for.\n"
            "Example: `python` or `data science`",
            parse_mode='Markdown'
        )
    
    elif data == "stats":
        await stats_command(update, context)
    
    elif data == "help":
        await help_command(update, context)
    
    elif data == "refresh":
        await query.message.reply_text(
            "🔄 Cache cleared! Use /batches to reload.",
            reply_markup=create_main_keyboard()
        )
    
    elif data == "noop":
        pass  # Do nothing for info buttons
    
    # Page navigation
    elif data.startswith("page_"):
        page = int(data.split("_")[1])
        await show_batches_page(update, context, page)
    
    # Batch selection
    elif data.startswith("select_"):
        batch_id = data.split("_", 1)[1]
        await show_batch_detail(update, context, batch_id)
    
    # Batch details
    elif data.startswith("details_"):
        batch_id = data.split("_", 1)[1]
        await show_batch_detail(update, context, batch_id)
    
    # Topics view
    elif data.startswith("topics_"):
        batch_id = data.split("_", 1)[1]
        await show_batch_topics(update, context, batch_id)
    
    # Extraction
    elif data.startswith("extract_"):
        batch_id = data.split("_", 1)[1]
        await start_extraction(update, context, batch_id)
    
    else:
        logger.warning(f"Unknown callback: {data}")

async def show_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show home/main menu."""
    query = update.callback_query
    
    welcome_msg = (
        "🎓 *SelectionWay Batch Extractor*\n\n"
        "Select an option below:"
    )
    
    try:
        await query.edit_message_text(
            welcome_msg,
            parse_mode='Markdown',
            reply_markup=create_main_keyboard()
        )
    except Exception:
        await query.message.reply_text(
            welcome_msg,
            parse_mode='Markdown',
            reply_markup=create_main_keyboard()
        )

async def show_batch_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, batch_id: str):
    """Show detailed batch information."""
    query = update.callback_query
    
    loading_msg = await query.message.reply_text("🔄 *Loading batch details...*", parse_mode='Markdown')
    
    try:
        batch = await extractor.get_batch_info(batch_id)
    except Exception as e:
        await loading_msg.edit_text(f"❌ Error: {str(e)}")
        return
    
    await loading_msg.delete()
    
    if not batch:
        await query.message.reply_text(
            "❌ *Batch not found*\n\n"
            "It may have been removed or you don't have access.",
            parse_mode='Markdown'
        )
        return
    
    info = format_batch_info(batch)
    keyboard = create_batch_detail_keyboard(batch_id)
    
    await query.message.reply_text(
        info,
        parse_mode='Markdown',
        reply_markup=keyboard
    )

async def show_batch_topics(update: Update, context: ContextTypes.DEFAULT_TYPE, batch_id: str):
    """Show topics for a batch."""
    query = update.callback_query
    
    loading_msg = await query.message.reply_text("🔄 *Loading topics...*", parse_mode='Markdown')
    
    try:
        topics = await extractor.fetch_topics(batch_id)
    except Exception as e:
        await loading_msg.edit_text(f"❌ Error: {str(e)}")
        return
    
    await loading_msg.delete()
    
    if not topics:
        await query.message.reply_text(
            "📭 *No topics found for this batch.*",
            parse_mode='Markdown',
            reply_markup=create_batch_detail_keyboard(batch_id)
        )
        return
    
    # Format topics list
    topics_text = f"📑 *Topics for Batch {batch_id}*\n\n"
    for idx, topic in enumerate(topics[:20], 1):
        topic_name = topic.get('topicName', 'Unknown')
        class_count = topic.get('classCount', 0)
        topics_text += f"{idx}. {topic_name} ({class_count} classes)\n"
    
    if len(topics) > 20:
        topics_text += f"\n_...and {len(topics) - 20} more topics_"
    
    topics_text += f"\n\n*Total:* {len(topics)} topics"
    
    keyboard = create_batch_detail_keyboard(batch_id)
    
    await query.message.reply_text(
        topics_text,
        parse_mode='Markdown',
        reply_markup=keyboard
    )

async def start_extraction(update: Update, context: ContextTypes.DEFAULT_TYPE, batch_id: str):
    """Start the extraction process."""
    query = update.callback_query
    user_id = update.effective_user.id
    
    # Send initial status
    status_msg = await query.message.reply_text(
        "🔄 *Starting Extraction Process...*\n\n"
        "⏳ *Step 1/4:* Fetching batch information...\n"
        "Please wait, this may take a minute for large batches.",
        parse_mode='Markdown'
    )
    
    try:
        # Progress callback
        async def update_progress(topics_done: int, total_topics: int, videos: int, pdfs: int):
            progress_pct = (topics_done / total_topics * 100) if total_topics > 0 else 0
            bar_length = 10
            filled = int(bar_length * topics_done / total_topics) if total_topics > 0 else 0
            bar = "█" * filled + "░" * (bar_length - filled)
            
            try:
                await status_msg.edit_text(
                    f"🔄 *Extracting Content...*\n\n"
                    f"📊 Progress: [{bar}] {progress_pct:.0f}%\n\n"
                    f"📑 Topics: {topics_done}/{total_topics}\n"
                    f"📹 Videos found: {videos}\n"
                    f"📄 PDFs found: {pdfs}\n\n"
                    f"⏳ Please wait...",
                    parse_mode='Markdown'
                )
            except Exception:
                pass  # Ignore update errors
        
        # Perform extraction
        result = await extractor.extract_batch_content(batch_id, update_progress)
        
        # Record success
        progress_tracker.record_extraction(user_id, True, result)
        
        # Send success message
        summary = format_extraction_result(result)
        
        await status_msg.edit_text(
            summary,
            parse_mode='Markdown'
        )
        
        # Send file
        file_path = Path(result.file_path)
        if file_path.exists():
            file_size = file_path.stat().st_size
            
            # Check file size (Telegram limit is 50MB)
            if file_size < MAX_FILE_SIZE_MB * 1024 * 1024:
                await query.message.reply_document(
                    document=open(file_path, 'rb'),
                    filename=file_path.name,
                    caption="📄 *Extraction Results*\n\n"
                            f"Batch: {result.batch_title}\n"
                            f"Links: {result.total_hls + result.total_videos + result.total_pdfs}",
                    parse_mode='Markdown'
                )
            else:
                await query.message.reply_text(
                    f"⚠️ *File too large to send*\n\n"
                    f"File size: {file_size / (1024*1024):.1f}MB\n"
                    f"Max size: {MAX_FILE_SIZE_MB}MB\n\n"
                    f"The file has been saved but cannot be sent via Telegram.\n"
                    f"Contact admin for alternative download method.",
                    parse_mode='Markdown'
                )
        
        # Cleanup old files (keep last 100 files)
        cleanup_old_files(keep_last=100)
        
    except Exception as e:
        logger.error(f"Extraction failed for batch {batch_id}: {e}", exc_info=True)
        progress_tracker.record_extraction(user_id, False)
        
        await status_msg.edit_text(
            f"❌ *Extraction Failed*\n\n"
            f"Error: `{str(e)[:200]}`\n\n"
            f"Please try again or contact support.\n"
            f"Use /batches to try another batch.",
            parse_mode='Markdown'
        )

def cleanup_old_files(keep_last: int = 100):
    """Remove old extraction files."""
    try:
        files = sorted(DOWNLOAD_DIR.glob("*.txt"), key=lambda f: f.stat().st_mtime, reverse=True)
        for old_file in files[keep_last:]:
            old_file.unlink()
            logger.info(f"Cleaned up old file: {old_file.name}")
    except Exception as e:
        logger.error(f"File cleanup error: {e}")

# ─── Admin Commands ──────────────────────────────────────────────────────────

@admin_only
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panel."""
    stats = progress_tracker.get_stats_summary()
    
    admin_text = (
        "*🔧 Admin Panel*\n\n"
        f"👥 Users: {stats['total_users']}\n"
        f"📥 Extractions: {stats['total_extractions']}\n"
        f"📈 Success Rate: {stats['success_rate']}%\n"
        f"⏱️ Uptime: {stats['uptime']}\n"
        f"🌍 Environment: {stats['environment']}\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("📊 Detailed Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("🧹 Cleanup Files", callback_data="admin_cleanup")],
        [InlineKeyboardButton("🔄 Restart Services", callback_data="admin_restart")],
    ]
    
    await update.message.reply_text(
        admin_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ─── Message Handler ─────────────────────────────────────────────────────────

@restricted
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages (for search)."""
    text = update.message.text.strip()
    
    if len(text) < 2:
        await update.message.reply_text(
            "Please enter at least 2 characters to search.",
            reply_markup=create_main_keyboard()
        )
        return
    
    # Treat as search query
    await perform_search(update, context, text)

# ─── Error Handler ───────────────────────────────────────────────────────────

async def error_handler(update: Optional[Update], context: ContextTypes.DEFAULT_TYPE):
    """Handle errors globally."""
    error = context.error
    
    logger.error(f"Update caused error: {error}", exc_info=error)
    
    try:
        if update and update.effective_message:
            error_msg = "❌ *An error occurred*\n\n"
            
            if isinstance(error, NetworkError):
                error_msg += "Network error. Please check your connection and try again."
            elif isinstance(error, TimedOut):
                error_msg += "Request timed out. Please try again."
            elif isinstance(error, TelegramError):
                error_msg += f"Telegram error: {str(error)[:100]}"
            else:
                error_msg += "Something went wrong. Please try again later."
            
            await update.effective_message.reply_text(
                error_msg,
                parse_mode='Markdown'
            )
    except Exception as e:
        logger.error(f"Error in error handler: {e}")

# ─── Health Check Endpoint (for Koyeb) ───────────────────────────────────────

async def health_check():
    """Simple health check endpoint."""
    from aiohttp import web
    
    app = web.Application()
    
    async def health(request):
        return web.Response(
            text=json.dumps({
                "status": "healthy",
                "timestamp": datetime.now().isoformat(),
                "uptime": str(datetime.now() - progress_tracker.stats['start_time']).split('.')[0]
            }),
            content_type='application/json'
        )
    
    app.router.add_get('/health', health)
    app.router.add_get('/', health)
    
    return app

# ─── Application Setup ───────────────────────────────────────────────────────

def create_application() -> Application:
    """Create and configure the bot application."""
    
    # Create request handler with connection pooling
    request = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=10.0,
        read_timeout=30.0,
        write_timeout=10.0
    )
    
    # Build application
    builder = ApplicationBuilder() \
        .token(BOT_TOKEN) \
        .request(request) \
        .defaults(Defaults(parse_mode='Markdown', disable_web_page_preview=True))
    
    if IS_PRODUCTION:
        # Use webhook for production
        builder.updater(None)
    
    application = builder.build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("batches", batches_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("admin", admin_command))
    
    # Add callback query handler
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Add message handler for search
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_message
    ))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    return application

async def setup_commands(application: Application):
    """Setup bot commands in Telegram menu."""
    commands = [
        BotCommand("start", "🚀 Start the bot"),
        BotCommand("batches", "📚 List all batches"),
        BotCommand("search", "🔍 Search for a batch"),
        BotCommand("stats", "📊 View statistics"),
        BotCommand("help", "ℹ️ Show help guide"),
        BotCommand("cancel", "❌ Cancel operation"),
    ]
    
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Bot commands configured successfully")
    except Exception as e:
        logger.error(f"Failed to set commands: {e}")

async def run_polling(application: Application):
    """Run bot with polling (for development)."""
    logger.info("Starting bot in polling mode...")
    
    # Setup commands
    await setup_commands(application)
    
    # Start polling
    await application.initialize()
    await application.start()
    await application.updater.start_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )
    
    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)  # Sleep for 1 hour
            # Periodic cleanup
            progress_tracker.cleanup_old_sessions()
            cleanup_old_files(keep_last=100)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutting down...")
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

async def run_webhook(application: Application):
    """Run bot with webhook (for Koyeb production)."""
    logger.info("Starting bot in webhook mode...")
    
    # Setup commands
    await setup_commands(application)
    
    # Setup webhook
    if not WEBHOOK_URL:
        logger.error("WEBHOOK_URL not set!")
        return
    
    webhook_path = f"/webhook/{BOT_TOKEN}"
    full_webhook_url = f"{WEBHOOK_URL.rstrip('/')}{webhook_path}"
    
    # Set webhook
    await application.bot.set_webhook(
        url=full_webhook_url,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )
    
    logger.info(f"Webhook set to: {full_webhook_url}")
    
    # Create web application
    from aiohttp import web
    
    web_app = web.Application()
    
    # Health check
    async def health(request):
        return web.Response(
            text=json.dumps({
                "status": "healthy",
                "timestamp": datetime.now().isoformat()
            }),
            content_type='application/json'
        )
    
    web_app.router.add_get('/', health)
    web_app.router.add_get('/health', health)
    web_app.router.add_post(webhook_path, application.update_queue)
    
    # Run web server
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    logger.info(f"Health check server running on port {PORT}")
    
    # Initialize and start application
    await application.initialize()
    await application.start()
    
    try:
        while True:
            await asyncio.sleep(3600)
            progress_tracker.cleanup_old_sessions()
            cleanup_old_files(keep_last=100)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutting down...")
    finally:
        await application.stop()
        await application.shutdown()
        await runner.cleanup()

# ─── Main Entry Point ────────────────────────────────────────────────────────

def main():
    """Main entry point."""
    print("""
╔══════════════════════════════════════════════════════════════╗
║          SelectionWay Telegram Bot v2.0                       ║
║          Koyeb Optimized Edition                              ║
╚══════════════════════════════════════════════════════════════╝
    """)
    
    print(f"🔧 Environment: {ENVIRONMENT}")
    print(f"📡 API Base: {API_BASE}")
    print(f"🤖 Bot Token: {'✓ Configured' if BOT_TOKEN else '✗ Missing'}")
    print(f"👥 Allowed Users: {'All' if not ALLOWED_USERS else len(ALLOWED_USERS)}")
    print(f"👑 Admins: {len(ADMIN_IDS)}")
    
    # Create application
    application = create_application()
    
    # Run based on environment
    if IS_PRODUCTION and WEBHOOK_URL:
        print(f"🌐 Running in webhook mode on port {PORT}")
        asyncio.run(run_webhook(application))
    else:
        print("📡 Running in polling mode")
        asyncio.run(run_polling(application))

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        sys.exit(1)
