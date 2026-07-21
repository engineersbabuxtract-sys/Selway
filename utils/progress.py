"""
Progress Tracking
================
"""

from datetime import datetime
from collections import defaultdict
from typing import Dict

class ProgressTracker:
    """Track extraction progress and statistics."""
    
    def __init__(self):
        self.stats = {
            'total_extractions': 0,
            'successful': 0,
            'failed': 0,
            'total_videos': 0,
            'total_pdfs': 0,
            'total_hls': 0,
            'active_users': set(),
            'last_extraction': None,
            'today_extractions': 0,
            'extractions_today': defaultdict(int)
        }
        self.active_extractions = {}
    
    def start_extraction(self, user_id: int, batch_id: str):
        """Record start of extraction."""
        self.active_extractions[user_id] = {
            'batch_id': batch_id,
            'start_time': datetime.now()
        }
    
    def complete_extraction(self, user_id: int, success: bool, result: dict = None):
        """Record completion of extraction."""
        self.stats['total_extractions'] += 1
        self.stats['active_users'].add(user_id)
        self.stats['last_extraction'] = datetime.now().isoformat()
        
        today = datetime.now().strftime('%Y-%m-%d')
        self.stats['extractions_today'][today] += 1
        self.stats['today_extractions'] = self.stats['extractions_today'][today]
        
        if success:
            self.stats['successful'] += 1
            if result:
                self.stats['total_videos'] += result.get('total_videos', 0)
                self.stats['total_pdfs'] += result.get('total_pdfs', 0)
                self.stats['total_hls'] += result.get('total_hls', 0)
        else:
            self.stats['failed'] += 1
        
        if user_id in self.active_extractions:
            del self.active_extractions[user_id]
    
    def get_stats(self) -> dict:
        """Get current statistics."""
        total = self.stats['total_extractions']
        success_rate = (self.stats['successful'] / total * 100) if total > 0 else 0
        
        return {
            'total_extractions': total,
            'successful': self.stats['successful'],
            'failed': self.stats['failed'],
            'total_videos': self.stats['total_videos'],
            'total_pdfs': self.stats['total_pdfs'],
            'total_hls': self.stats['total_hls'],
            'active_users': len(self.stats['active_users']),
            'last_extraction': self.stats['last_extraction'] or 'Never',
            'today_extractions': self.stats['today_extractions'],
            'success_rate': round(success_rate, 1)
        }
