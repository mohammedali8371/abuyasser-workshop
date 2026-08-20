import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import select
import io

from app.config import settings
from app.auth import hash_password, verify_password
from app.templates_mod import templates

logger = logging.getLogger(__name__)

_bot_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import engine, Base, async_session
    from app.models import User, UserRole, SiteSetting

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as e:
        logger.error("DB init error: %s", e)

    try:
        async with async_session() as db:
            result = await db.execute(
                select(User).where(User.email == settings.ADMIN_EMAIL)
            )
            owner = result.scalar_one_or_none()
            if not owner:
                owner = User(
                    name=settings.ADMIN_NAME,
                    phone=settings.ADMIN_PHONE,
                    email=settings.ADMIN_EMAIL,
                    password_hash=hash_password(settings.ADMIN_PASSWORD),
                    role=UserRole.OWNER.value,
                )
                db.add(owner)
                await db.flush()
            else:
                owner.password_hash = hash_password(settings.ADMIN_PASSWORD)
                owner.email = settings.ADMIN_EMAIL
                owner.name = settings.ADMIN_NAME
                owner.phone = settings.ADMIN_PHONE
                if owner.role != UserRole.OWNER.value:
                    owner.role = UserRole.OWNER.value
                await db.flush()

            defaults = {
                "workshop_name": settings.WORKSHOP_NAME,
                "workshop_phone": settings.WORKSHOP_PHONE,
                "workshop_location": settings.WORKSHOP_LOCATION,
                "workshop_description": "ورشة متخصصة في تقديم أفضل الخدمات والمنتجات لعملائنا الكرام",
                "workshop_hours": "السبت - الخميس: 8 صباحاً - 10 مساءً",
                "workshop_days": "السبت إلى الخميس",
                "welcome_message": "مرحباً بكم في ورشة أبو ياسر الصرماح",
                "facebook": "", "twitter": "", "instagram": "",
                "tiktok": "", "telegram": "", "youtube": "",
                "whatsapp": "",
                "hero_title": "",
                "hero_description": "",
                "hero_btn_text": "",
                "categories_title": "",
                "categories_subtitle": "",
                "products_title": "",
                "products_subtitle": "",
                "contact_title": "",
                "contact_subtitle": "",
                "footer_text": "",
                "copyright_text": "",
                "about_title": "",
                "about_text": "",
                "about_vision": "",
                "about_mission": "",
                "terms_text": "",
                "privacy_text": "",
                "login_title": "",
                "login_subtitle": "",
                "register_title": "",
                "register_subtitle": "",
            }
            for key, val in defaults.items():
                result = await db.execute(
                    select(SiteSetting).where(SiteSetting.key == key)
                )
                if not result.scalar_one_or_none():
                    db.add(SiteSetting(key=key, value=f'"{val}"'))

            await db.commit()
    except Exception as e:
        logger.error("Seed data error: %s", e)

    # Telegram bot disabled on Render - running on VPS instead
    # import asyncio
    # global _bot_task
    # try:
    #     from app.telegram_bot import start_bot_polling
    #     _bot_task = asyncio.create_task(start_bot_polling())
    #     logger.info("Telegram bot task started.")
    # except Exception as e:
    #     logger.error("Telegram bot start error: %s", e)

    yield

    try:
        from app.telegram_bot import stop_bot_polling
        await stop_bot_polling()
    except Exception:
        pass

    await engine.dispose()


app = FastAPI(title="ورشة أبو ياسر الصرماح", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


def include_routers():
    from app.routers import customer, admin

    app.include_router(customer.router, prefix="/customer", tags=["Customer"])
    app.include_router(admin.router, prefix="/mo", tags=["Admin"])


include_routers()


@app.get("/")
async def index(request: Request):
    return RedirectResponse("/customer/", status_code=302)


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return RedirectResponse("/customer/", status_code=302)


@app.exception_handler(401)
async def unauthorized(request: Request, exc):
    path = request.url.path
    if path.startswith("/mo"):
        return RedirectResponse("/mo/login", status_code=302)
    return RedirectResponse("/customer/login", status_code=302)


@app.exception_handler(403)
async def forbidden(request: Request, exc):
    path = request.url.path
    if path.startswith("/mo"):
        return RedirectResponse("/mo/login", status_code=302)
    return RedirectResponse("/customer/", status_code=302)


@app.get("/debug-admin")
async def debug_admin():
    from app.database import async_session
    from app.models import User
    from app.auth import verify_password, hash_password
    async with async_session() as db:
        result = await db.execute(select(User))
        users = result.scalars().all()
        lines = []
        for u in users:
            lines.append(f"ID:{u.id} | email:{u.email} | role:{u.role} | name:{u.name}")
        admin_email = settings.ADMIN_EMAIL
        admin_pass = settings.ADMIN_PASSWORD
        lines.append(f"--- ENV ADMIN_EMAIL={admin_email} ---")
        lines.append(f"--- ENV ADMIN_PASSWORD={admin_pass} ---")
        result2 = await db.execute(select(User).where(User.email == admin_email))
        found = result2.scalar_one_or_none()
        if found:
            ok = verify_password(admin_pass, found.password_hash)
            lines.append(f"Found admin: role={found.role} | password_match={ok}")
            found.password_hash = hash_password(admin_pass)
            await db.commit()
            ok2 = verify_password(admin_pass, found.password_hash)
            lines.append(f"After reset: password_match={ok2}")
        else:
            lines.append("Admin NOT FOUND - creating...")
            new_admin = User(
                name=settings.ADMIN_NAME,
                phone=settings.ADMIN_PHONE,
                email=admin_email,
                password_hash=hash_password(admin_pass),
                role="OWNER",
            )
            db.add(new_admin)
            await db.commit()
            lines.append("Admin created!")
    return {"debug": lines}


@app.get("/og-image")
async def og_image():
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1200, 630
    img = Image.new("RGB", (W, H), "#1a1a2e")
    draw = ImageDraw.Draw(img)

    for y in range(H):
        r = int(26 + (y / H) * 10)
        g = int(26 + (y / H) * 15)
        b = int(46 + (y / H) * 20)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    draw.rounded_rectangle([40, 40, W-40, H-40], radius=30, fill=None, outline="#e94560", width=3)
    draw.rounded_rectangle([60, 60, W-60, H-60], radius=20, fill=None, outline="#f5a623", width=1)

    try:
        font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
        font_icon = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 80)
    except Exception:
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()
        font_icon = ImageFont.load_default()

    draw.text((W//2, 180), "Abu Yasser Workshop", font=font_big, fill="#ffffff", anchor="mm")
    draw.text((W//2, 260), "ورشة أبو ياسر الصرماح", font=font_small, fill="#f5a623", anchor="mm")
    draw.text((W//2, 350), "Tools & Services", font=font_small, fill="#a0a0b8", anchor="mm")

    draw.ellipse([W//2-40, 420, W//2+40, 500], fill="#e94560")
    draw.text((W//2, 460), ">>", font=font_small, fill="#ffffff", anchor="mm")

    draw.text((W//2, 560), "abuyasser-workshop.onrender.com", font=font_small, fill="#a0a0b8", anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="PNG", quality=95)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
