import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("DATABASE_URL", "postgresql://appuser:TXOyQuQ01rE7JzZDlqbMQbT1TYuU6XKA@dpg-da3fadflk1mc73fpncl0-a.oregon-postgres.render.com:5432/abuyasser")
os.environ.setdefault("SECRET_KEY", "workshop-secret-2026-super-secure")
os.environ.setdefault("ADMIN_EMAIL", "mol716796302@gmail.com")
os.environ.setdefault("ADMIN_PASSWORD", "77272227722722")
os.environ.setdefault("ADMIN_NAME", "المالك الرئيسي")
os.environ.setdefault("ADMIN_PHONE", "77272227722722")
os.environ.setdefault("WORKSHOP_NAME", "ورشة أبو ياسر الصرماح")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "8503124202:AAGI9rPf3P-5pr5VGzwLhofgda1PXCJtqX4")
os.environ.setdefault("TELEGRAM_OWNER_ID", "7958260008")

from app.database import async_session
from app.models import User
from app.auth import hash_password, verify_password
from sqlalchemy import select


async def check():
    async with async_session() as db:
        result = await db.execute(select(User))
        users = result.scalars().all()
        print("=== USERS IN DATABASE ===")
        for u in users:
            print(f"ID:{u.id} | Email:{u.email} | Name:{u.name} | Role:{u.role}")
            print(f"  Hash: {u.password_hash[:40]}...")

        admin_email = os.environ["ADMIN_EMAIL"]
        admin_pass = os.environ["ADMIN_PASSWORD"]
        result2 = await db.execute(select(User).where(User.email == admin_email))
        admin = result2.scalar_one_or_none()
        if admin:
            ok = verify_password(admin_pass, admin.password_hash)
            print(f"\nAdmin login check: email={admin_email} -> {'OK' if ok else 'WRONG PASSWORD'}")
        else:
            print(f"\nAdmin with email {admin_email} NOT FOUND - creating...")
            new_admin = User(
                name=os.environ["ADMIN_NAME"],
                phone=os.environ["ADMIN_PHONE"],
                email=admin_email,
                password_hash=hash_password(admin_pass),
                role="OWNER",
            )
            db.add(new_admin)
            print(f"Created admin: {admin_email} / {admin_pass}")

        await db.commit()

        result3 = await db.execute(select(User))
        users2 = result3.scalars().all()
        print("\n=== USERS AFTER FIX ===")
        for u in users2:
            print(f"ID:{u.id} | Email:{u.email} | Role:{u.role}")

asyncio.run(check())
