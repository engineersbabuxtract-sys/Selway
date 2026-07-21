"""
SelectionWay Batch Content Extractor
=====================================
Async-compatible extractor for SelectionWay batches.
"""

import sys
import os
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Callable

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    os.environ['PYTHONIOENCODING'] = 'utf-8'

import aiohttp
import json
import re
import time

class SelectionWayExtractor:
    """Extractor for SelectionWay batch content."""
    
    def __init__(self, api_base: str, user_id: str = ""):
        self.api_base = api_base
        self.user_id = user_id
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://www.selectionway.com/",
            "Origin": "https://www.selectionway.com",
        }
        self.session = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=60)
            self.session = aiohttp.ClientSession(
                headers=self.headers,
                timeout=timeout
            )
        return self.session
    
    async def _api_get(self, endpoint: str, params: dict = None) -> dict:
        """Make async API GET request."""
        session = await self._get_session()
        url = f"{self.api_base}{endpoint}"
        
        if params is None:
            params = {}
        params['userId'] = self.user_id
        
        try:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                return await response.json()
        except Exception as e:
            print(f"API Error: {e}")
            return {"state": 0, "message": str(e)}
    
    async def fetch_all_batches_async(self) -> List[dict]:
        """Fetch all active batches asynchronously."""
        data = await self._api_get("/courses/active")
        
        if data.get("state") != 200:
            print(f"API error: {data.get('message', 'Unknown error')}")
            return []
        
        return data.get("data", [])
    
    async def get_batch_info_async(self, course_id: str) -> dict:
        """Get detailed batch information."""
        batches = await self.fetch_all_batches_async()
        for batch in batches:
            if str(batch.get("id")) == str(course_id):
                return batch
        return None
    
    async def fetch_topics_async(self, course_id: str) -> List[dict]:
        """Fetch topics for a course asynchronously."""
        data = await self._api_get("/topic-and-section", {"courseId": course_id})
        
        if data.get("state") != 200:
            return []
        
        return data.get("data", {}).get("topics", [])
    
    async def fetch_classes_async(self, topic_id: str, course_id: str) -> List[dict]:
        """Fetch classes for a topic asynchronously."""
        endpoint = f"/topics/{topic_id}/classes"
        data = await self._api_get(endpoint, {"courseId": course_id})
        
        if data.get("state") != 200:
            return []
        
        return data.get("data", {}).get("classes", [])
    
    async def extract_batch_content_async(
        self,
        course_id: str,
        progress_callback: Optional[Callable] = None
    ) -> dict:
        """Extract all content from a batch asynchronously."""
        
        # Get batch info
        batch_info = await self.get_batch_info_async(course_id)
        if not batch_info:
            raise ValueError(f"Batch not found: {course_id}")
        
        batch_title = batch_info.get("title", "Unknown")
        
        # Get topics
        topics = await self.fetch_topics_async(course_id)
        if not topics:
            raise ValueError("No topics found")
        
        # Initialize counters
        total_videos = 0
        total_pdfs = 0
        total_hls = 0
        total_topics = len(topics)
        topics_completed = 0
        
        # Create output file
        filename = self._sanitize_filename(batch_title) + ".txt"
        filepath = os.path.join(os.getcwd(), "downloads", filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        with open(filepath, "w", encoding="utf-8") as f:
            # Write header
            f.write(f"{'='*80}\n")
            f.write(f"  BATCH: {batch_title}\n")
            f.write(f"  Course ID: {course_id}\n")
            f.write(f"  Faculty: {batch_info.get('facultyDetails', {}).get('name', 'N/A')}\n")
            f.write(f"  Extracted on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"{'='*80}\n\n")
            
            # Process each topic
            for t_idx, topic in enumerate(topics, 1):
                topic_name = topic.get("topicName", f"Topic {t_idx}")
                topic_id = topic.get("topicId")
                
                f.write(f"\n{'─'*80}\n")
                f.write(f"  TOPIC: {topic_name}\n")
                f.write(f"  Topic ID: {topic_id}\n")
                f.write(f"{'─'*80}\n\n")
                
                # Get classes for this topic
                classes = await self.fetch_classes_async(topic_id, course_id)
                
                if not classes:
                    f.write("    (No classes found)\n\n")
                    topics_completed += 1
                    if progress_callback:
                        progress_callback({
                            "topics_completed": topics_completed,
                            "total_topics": total_topics,
                            "videos_found": total_videos,
                            "pdfs_found": total_pdfs,
                            "hls_found": total_hls
                        })
                    continue
                
                # Group by subtopic
                subtopic_groups = {}
                for cls in classes:
                    sub = cls.get("subTopic", {})
                    sub_name = sub.get("subTopicName", "General") if sub else "General"
                    if sub_name not in subtopic_groups:
                        subtopic_groups[sub_name] = []
                    subtopic_groups[sub_name].append(cls)
                
                # Write classes
                for sub_name, sub_classes in subtopic_groups.items():
                    f.write(f"    Subtopic: {sub_name}\n")
                    f.write(f"    Classes: {len(sub_classes)}\n\n")
                    
                    for cls in sub_classes:
                        title = cls.get("title", "Untitled")
                        class_id = cls.get("classId", "N/A")
                        
                        f.write(f"    ├── {title}\n")
                        f.write(f"    │   Class ID: {class_id}\n")
                        
                        # HLS Link
                        hls_link = cls.get("class_link", "")
                        if hls_link:
                            f.write(f"    │   [HLS] {hls_link}\n")
                            total_hls += 1
                        
                        # MP4 Links
                        mp4s = cls.get("mp4Recordings", [])
                        if mp4s:
                            for mp4 in mp4s:
                                quality = mp4.get("quality", "?")
                                url = mp4.get("url", "")
                                size = mp4.get("size", 0)
                                if url:
                                    f.write(f"    │   [MP4-{quality}] ({size:.1f}MB) {url}\n")
                                    total_videos += 1
                        
                        # PDF Links
                        pdfs = cls.get("classPdf", [])
                        if pdfs:
                            for pdf in pdfs:
                                pdf_name = pdf.get("name", "PDF")
                                pdf_url = pdf.get("url", "")
                                if pdf_url:
                                    f.write(f"    │   [PDF] {pdf_name}: {pdf_url}\n")
                                    total_pdfs += 1
                        
                        f.write(f"    │\n")
                    
                    f.write(f"    └──\n\n")
                
                topics_completed += 1
                
                # Update progress
                if progress_callback:
                    progress_callback({
                        "topics_completed": topics_completed,
                        "total_topics": total_topics,
                        "videos_found": total_videos,
                        "pdfs_found": total_pdfs,
                        "hls_found": total_hls
                    })
                
                # Small delay to avoid rate limiting
                await asyncio.sleep(0.3)
            
            # Write summary
            f.write(f"\n{'='*80}\n")
            f.write(f"  SUMMARY\n")
            f.write(f"{'='*80}\n")
            f.write(f"  Total HLS Streams : {total_hls}\n")
            f.write(f"  Total MP4 Videos  : {total_videos}\n")
            f.write(f"  Total PDFs        : {total_pdfs}\n")
            f.write(f"  Total Links       : {total_hls + total_videos + total_pdfs}\n")
            f.write(f"{'='*80}\n")
        
        return {
            "batch_title": batch_title,
            "course_id": course_id,
            "file_path": filepath,
            "total_hls": total_hls,
            "total_videos": total_videos,
            "total_pdfs": total_pdfs,
            "total_topics": total_topics,
            "total_classes": topics_completed
        }
    
    def _sanitize_filename(self, name: str) -> str:
        """Sanitize filename."""
        name = re.sub(r'[<>:"/\\|?*]', '_', name)
        name = re.sub(r'\s+', '_', name)
        name = re.sub(r'_+', '_', name)
        name = name.strip('_. ')
        return name if name else "Unknown_Batch"
    
    async def close(self):
        """Close the session."""
        if self.session and not self.session.closed:
            await self.session.close()
