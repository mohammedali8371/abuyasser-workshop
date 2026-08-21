import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    def __init__(self):
        self.SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production")
        self.ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "admin@abuyasser.com")
        self.ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "Admin@123456")
        self.ADMIN_NAME: str = os.getenv("ADMIN_NAME", "المالك الرئيسي")
        self.ADMIN_PHONE: str = os.getenv("ADMIN_PHONE", "0500000000")
        self.WORKSHOP_PHONE: str = os.getenv("WORKSHOP_PHONE", "0500000000")
        self.WORKSHOP_LOCATION: str = os.getenv("WORKSHOP_LOCATION", "المملكة العربية السعودية")
        self.UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "static/uploads")
        self.MAX_UPLOAD_SIZE: int = int(os.getenv("MAX_UPLOAD_SIZE", "10485760"))
        self.JWT_ALGORITHM: str = "HS256"
        self.JWT_EXPIRE_MINUTES: int = 60 * 24 * 7
        self.TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "8503124202:AAGI9rPf3P-5pr5VGzwLhofgda1PXCJtqX4")
        self.TELEGRAM_OWNER_ID: int = int(os.getenv("TELEGRAM_OWNER_ID", "7958260008"))
        self.WORKSHOP_NAME: str = os.getenv("WORKSHOP_NAME", "ورشة أبو ياسر الصرماح")
        if not self.WORKSHOP_NAME or '?' in self.WORKSHOP_NAME or '\ufffd' in self.WORKSHOP_NAME:
            self.WORKSHOP_NAME = "ورشة أبو ياسر الصرماح"


settings = Settings()
