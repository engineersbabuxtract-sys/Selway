"""
Configuration Settings
=====================
"""

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ─── Bot Configuration ─────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "8845555323:AAHzKabLkl1h1LuSQh5cYUYyVxslGHmmte8")

# ─── API Configuration ─────────────────────────────────────────────────────
API_BASE = os.getenv("API_BASE", "https://gdgoenkaratia.com/api")
USER_ID = os.getenv("USER_ID", "")

# ─── User Permissions ──────────────────────────────────────────────────────
# List of allowed user IDs (empty list = all users allowed)
ALLOWED_USERS = []
if os.getenv("ALLOWED_USERS"):
    ALLOWED_USERS = [int(uid) for uid in os.getenv("ALLOWED_USERS").split(",")]

# Admin user IDs
ADMIN_IDS = []
if os.getenv("ADMIN_IDS"):
    ADMIN_IDS = [int(aid) for aid in os.getenv("ADMIN_IDS").split(",")]

# ─── Bot Settings ──────────────────────────────────────────────────────────
MAX_BATCHES_PER_PAGE = int(os.getenv("MAX_BATCHES_PER_PAGE", "10"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ─── File Paths ────────────────────────────────────────────────────────────
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "downloads")
TEMP_DIR = os.getenv("TEMP_DIR", "temp")

# Create directories if they don't exist
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)
