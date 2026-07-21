# SelectionWay Telegram Bot

Koyeb-optimized Telegram bot for extracting SelectionWay batch content.

## Quick Deploy to Koyeb

1. Fork this repo
2. Go to [Koyeb](https://app.koyeb.com)
3. Create Service → Git Repository
4. Set `BOT_TOKEN` environment variable
5. Deploy!

## Environment Variables

- `BOT_TOKEN` (Required) - Telegram bot token
- `ALLOWED_USERS` (Optional) - Comma-separated user IDs
- `ADMIN_IDS` (Optional) - Admin user IDs

## Local Development

```bash
pip install -r requirements.txt
export BOT_TOKEN="your_token"
python bot.py
