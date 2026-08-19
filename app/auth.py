from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_token(user_id: int, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except JWTError:
        return None


def get_token_from_cookie(request: Request) -> Optional[str]:
    return request.cookies.get("access_token")


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    from app.models import User

    token = None
    if credentials:
        token = credentials.credentials
    if not token and request:
        token = get_token_from_cookie(request)

    if not token:
        raise HTTPException(status_code=401, detail="غير مصرح بالدخول")

    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="رمز غير صالح")

    user_id = int(payload.get("sub", 0))
    if not user_id:
        raise HTTPException(status_code=401, detail="رمز غير صالح")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="المستخدم غير موجود")
    if user.is_banned:
        raise HTTPException(status_code=403, detail="تم حظر حسابك")

    return user


def require_role(*allowed_roles):
    async def role_checker(
        current_user=None,
        user: "User" = Depends(get_current_user),
    ):
        if user.role not in allowed_roles:
            raise HTTPException(status_code=403, detail="ليس لديك صلاحية")
        return user

    return role_checker


async def get_owner(
    user=Depends(get_current_user),
):
    from app.models import UserRole

    if user.role != UserRole.OWNER.value:
        raise HTTPException(status_code=403, detail="stricted to owner only")
    return user


async def get_admin(
    user=Depends(get_current_user),
):
    from app.models import UserRole

    if user.role not in (UserRole.OWNER.value, UserRole.ADMIN.value):
        raise HTTPException(status_code=403, detail="stricted to admin only")
    return user
