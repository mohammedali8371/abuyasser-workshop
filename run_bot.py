import os
import sys
import asyncio
import logging

os.environ["DATABASE_URL"] = os.getenv(
    "DATABASE_URL",
    "postgresql://appuser:TXOyQuQ01rE7JzZDlqbMQbT1TYuU6XKA@dpg-da3fadflk1mc73fpncl0-a.oregon-postgres.render.com:5432/abuyasser"
)
os.environ["TELEGRAM_BOT_TOKEN"] = os.getenv("TELEGRAM_BOT_TOKEN", "8503124202:AAGI9rPf3P-5pr5VGzwLhofgda1PXCJtqX4")
os.environ["TELEGRAM_OWNER_ID"] = os.getenv("TELEGRAM_OWNER_ID", "7958260008")
os.environ["SECRET_KEY"] = os.getenv("SECRET_KEY", "vps-bot-secret-key-2026")
os.environ["ADMIN_EMAIL"] = "mol716796302@gmail.com"
os.environ["ADMIN_PASSWORD"] = "77272227722722"
os.environ["ADMIN_NAME"] = "المالك الرئيسي"
os.environ["ADMIN_PHONE"] = "77272227722722"
os.environ["WORKSHOP_NAME"] = "ورشة أبو ياسر الصرماح"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("bot_runner")


async def main():
    from app.telegram_bot import start_bot_polling, stop_bot_polling
    logger.info("Bot starting on VPS...")
    try:
        await start_bot_polling()
    except KeyboardInterrupt:
        pass
    finally:
        await stop_bot_polling()


if __name__ == "__main__":
    asyncio.run(main())
