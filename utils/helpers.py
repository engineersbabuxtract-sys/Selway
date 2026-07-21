"""
Helper Functions
===============
"""

from datetime import datetime
from typing import Dict, List

def format_batch_info(batch: dict) -> str:
    """Format batch information for display."""
    faculty = batch.get('facultyDetails', {})
    faculty_name = faculty.get('name', 'Unknown') if faculty else 'Unknown'
    
    info = (
        f"📚 *Batch Details*\n\n"
        f"*Title:* {batch.get('title', 'Unknown')}\n"
        f"*ID:* `{batch.get('id', 'N/A')}`\n"
        f"*Type:* {'🔴 LIVE' if batch.get('isLive') else '📺 Recorded'}\n"
        f"*Access:* {'🆓 Free' if batch.get('isFree') else '💎 Paid'}\n"
        f"*Faculty:* {faculty_name}\n"
        f"*Language:* {batch.get('language', 'N/A')}\n"
    )
    
    if batch.get('description'):
        desc = batch['description'][:200]
        info += f"\n*Description:*\n{desc}..."
    
    return info

def create_batches_keyboard(batches: List[dict], prefix: str = "batch") -> list:
    """Create inline keyboard from batches list."""
    from telegram import InlineKeyboardButton
    
    keyboard = []
    for batch in batches[:10]:  # Limit to 10
        title = batch.get('title', 'Unknown')[:40]
        batch_id = batch.get('id', '0')
        keyboard.append([InlineKeyboardButton(
            f"📥 {title}",
            callback_data=f"{prefix}_{batch_id}"
        )])
    
    return keyboard

def format_extraction_summary(result: dict) -> str:
    """Format extraction result summary."""
    return (
        f"✅ *Extraction Complete!*\n\n"
        f"*Batch:* {result.get('batch_title', 'Unknown')}\n"
        f"*Course ID:* `{result.get('course_id', 'N/A')}`\n\n"
        f"*Results:*\n"
        f"📚 Topics Processed: {result.get('total_topics', 0)}\n"
        f"📹 MP4 Videos: {result.get('total_videos', 0)}\n"
        f"📡 HLS Streams: {result.get('total_hls', 0)}\n"
        f"📄 PDFs: {result.get('total_pdfs', 0)}\n\n"
        f"📁 File saved: `{result.get('file_path', 'N/A')}`\n\n"
        f"_Download the file to access all links_"
    )

def sanitize_filename(name: str) -> str:
    """Sanitize string for use as filename."""
    import re
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'\s+', '_', name)
    name = re.sub(r'_+', '_', name)
    name = name.strip('_. ')
    return name if name else "Unknown"

def split_long_message(text: str, max_length: int = 4000) -> List[str]:
    """Split long message into chunks."""
    if len(text) <= max_length:
        return [text]
    
    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        
        # Find last newline before max_length
        split_point = text.rfind('\n', 0, max_length)
        if split_point == -1:
            split_point = text.rfind(' ', 0, max_length)
        if split_point == -1:
            split_point = max_length
        
        chunks.append(text[:split_point])
        text = text[split_point:].lstrip()
    
    return chunks

def get_file_size_mb(file_path: str) -> float:
    """Get file size in megabytes."""
    import os
    return os.path.getsize(file_path) / (1024 * 1024)
