from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.database import engine, Base, async_session
from app.config import settings
from app.models import User, UserRole, SiteSetting
from app.auth import hash_password
from app.templates_mod import templates


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

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

        defaults = {
            "workshop_name": settings.WORKSHOP_NAME,
            "workshop_phone": settings.WORKSHOP_PHONE,
            "workshop_location": settings.WORKSHOP_LOCATION,
            "workshop_description": "ورشة متخصصة في تقديم أفضل الخدمات والمنتجات لعملائنا الكرام",
            "workshop_hours": "السبت - الخميس: 8 صباحاً - 10 مساءً",
            "welcome_message": "مرحباً بكم في ورشة أبو ياسر الصرماح",
            "facebook": "",
            "twitter": "",
            "instagram": "",
            "whatsapp": "",
        }
        for key, val in defaults.items():
            result = await db.execute(
                select(SiteSetting).where(SiteSetting.key == key)
            )
            if not result.scalar_one_or_none():
                db.add(SiteSetting(key=key, value=f'"{val}"'))

        await db.commit()

    yield
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
    app.include_router(admin.router, prefix="/admin", tags=["Admin"])


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
    if path.startswith("/admin"):
        return RedirectResponse("/admin/login", status_code=302)
    return RedirectResponse("/customer/login", status_code=302)


@app.exception_handler(403)
async def forbidden(request: Request, exc):
    path = request.url.path
    if path.startswith("/admin"):
        return RedirectResponse("/admin/login", status_code=302)
    return RedirectResponse("/customer/", status_code=302)
