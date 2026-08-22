import logging
import json
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse, PlainTextResponse
from sqlalchemy import select
import io

from app.config import settings
from app.auth import hash_password, verify_password
from app.templates_mod import templates

logger = logging.getLogger(__name__)

_bot_task = None
_keep_alive_task = None

SITE_URL = "https://abuyasser-workshop.onrender.com"


async def _keep_alive():
    """Self-ping every 14 minutes to prevent Render free tier from sleeping."""
    import httpx
    while True:
        await asyncio.sleep(14 * 60)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(f"{SITE_URL}/health")
                logger.info("Keep-alive ping: %s", r.status_code)
        except Exception as e:
            logger.warning("Keep-alive ping failed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import engine, Base, async_session
    from app.models import User, UserRole, SiteSetting

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Add new columns if they don't exist (safe migration)
            async def _add_col(table, col, col_type):
                try:
                    await conn.execute(
                        __import__('sqlalchemy').text(
                            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}"
                        )
                    )
                except Exception:
                    pass
            await _add_col("product_images", "image_data", "TEXT DEFAULT ''")
            await _add_col("product_images", "sort_order", "INTEGER DEFAULT 0")
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
                "currency": "ر.ي",
            }
            for key, val in defaults.items():
                result = await db.execute(
                    select(SiteSetting).where(SiteSetting.key == key)
                )
                existing = result.scalar_one_or_none()
                if existing:
                    cur = existing.value or ""
                    if "?" in cur or "\ufffd" in cur or cur == '""' or cur == "":
                        existing.value = json.dumps(str(val), ensure_ascii=False)
                else:
                    db.add(SiteSetting(key=key, value=json.dumps(str(val), ensure_ascii=False)))

            await db.commit()

        # Cache currency symbol
        try:
            from app.templates_mod import _cached_currency
            cur_result = await db.execute(
                select(SiteSetting).where(SiteSetting.key == "currency")
            )
            cur_setting = cur_result.scalar_one_or_none()
            if cur_setting:
                import app.templates_mod as tm
                tm._cached_currency = json.loads(cur_setting.value)
        except Exception:
            pass
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

    # Start keep-alive background task
    global _keep_alive_task
    try:
        _keep_alive_task = asyncio.create_task(_keep_alive())
        logger.info("Keep-alive task started.")
    except Exception as e:
        logger.error("Keep-alive start error: %s", e)

    yield

    # Cancel keep-alive task
    if _keep_alive_task:
        _keep_alive_task.cancel()
        try:
            await _keep_alive_task
        except asyncio.CancelledError:
            pass

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
app.add_middleware(GZipMiddleware, minimum_size=500)

app.mount("/static", StaticFiles(directory="static"), name="static")


def include_routers():
    from app.routers import customer, admin

    app.include_router(customer.router, prefix="/customer", tags=["Customer"])
    app.include_router(admin.router, prefix="/mo", tags=["Admin"])


include_routers()


@app.get("/")
async def index(request: Request):
    return RedirectResponse("/customer/", status_code=302)


@app.get("/health")
async def health_check():
    return PlainTextResponse("OK")


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
