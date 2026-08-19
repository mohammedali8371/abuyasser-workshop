import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./workshop.db")
    ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "admin@abuyasser.com")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "Admin@123456")
    ADMIN_NAME: str = os.getenv("ADMIN_NAME", "المالك الرئيسي")
    ADMIN_PHONE: str = os.getenv("ADMIN_PHONE", "0500000000")
    WORKSHOP_NAME: str = os.getenv("WORKSHOP_NAME", "ورشة أبو ياسر الصرماح")
    WORKSHOP_PHONE: str = os.getenv("WORKSHOP_PHONE", "0500000000")
    WORKSHOP_LOCATION: str = os.getenv("WORKSHOP_LOCATION", "المملكة العربية السعودية")
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "static/uploads")
    MAX_UPLOAD_SIZE: int = int(os.getenv("MAX_UPLOAD_SIZE", "10485760"))
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days


settings = Settings()
