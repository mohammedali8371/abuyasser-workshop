from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.templating import _TemplateResponse
import json

templates = Jinja2Templates(directory="app/templates")

_cached_currency = None

def render(request: Request, name: str, context: dict = None) -> _TemplateResponse:
    ctx = context or {}
    ctx["request"] = request
    return templates.TemplateResponse(request, name, ctx)

def status_arabic(status: str) -> str:
    return {
        "new": "جديد", "reviewing": "قيد المراجعة", "accepted": "تم القبول",
        "in_progress": "قيد التنفيذ", "ready": "جاهز", "completed": "مكتمل",
        "cancelled": "ملغي",
    }.get(status, status)

def role_arabic(role: str) -> str:
    return {"OWNER": "المالك الرئيسي", "ADMIN": "مدير", "CUSTOMER": "عميل"}.get(role, role)

def stars_html(rating: int) -> str:
    return "".join("&#9733;" if i <= rating else "&#9734;" for i in range(1, 6))

def fromjson(val: str):
    try:
        return json.loads(val)
    except Exception:
        return val

def currency_symbol():
    global _cached_currency
    return _cached_currency or "ر.ي"

templates.env.globals["status_arabic"] = status_arabic
templates.env.globals["role_arabic"] = role_arabic
templates.env.globals["stars_html"] = stars_html
templates.env.filters["fromjson"] = fromjson
templates.env.globals["fromjson"] = fromjson
templates.env.globals["currency_symbol"] = currency_symbol
