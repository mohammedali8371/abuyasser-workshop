from fastapi import APIRouter, Request, Depends, Form, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession
import json

from app.database import get_db
from app.models import (
    User, Product, Category, Order, OrderItem, Payment, Review,
    Chat, Message, Notification, SiteSetting, UserRole, OrderStatus,
    PaymentMethod,
)
from app.auth import create_token, get_current_user, hash_password, verify_password
from app.templates_mod import render

router = APIRouter()


def fromjson(val: str):
    try:
        return json.loads(val)
    except Exception:
        return val


async def _get_site_settings(db: AsyncSession) -> dict:
    result = await db.execute(select(SiteSetting))
    return {s.key: fromjson(s.value) for s in result.scalars().all()}


async def _get_user(request: Request, db: AsyncSession):
    token = request.cookies.get("access_token")
    if token:
        from app.auth import decode_token
        payload = decode_token(token)
        if payload:
            r = await db.execute(select(User).where(User.id == int(payload.get("sub", 0))))
            return r.scalar_one_or_none()
    return None


@router.get("/")
async def customer_home(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Product).where(Product.is_available == True).order_by(Product.created_at.desc()).limit(12))
    products = result.scalars().all()
    result = await db.execute(select(Category).where(Category.is_active == True))
    categories = result.scalars().all()
    site = await _get_site_settings(db)
    user = await _get_user(request, db)
    result = await db.execute(select(PaymentMethod).where(PaymentMethod.is_active == True).order_by(PaymentMethod.sort_order))
    payment_methods = result.scalars().all()
    return render(request, "customer/index.html", {"products": products, "categories": categories, "site": site, "user": user, "payment_methods": payment_methods})



@router.get("/products")
async def customer_products(request: Request, q: str = None, category_id: int = None, db: AsyncSession = Depends(get_db)):
    query = select(Product).where(Product.is_available == True)
    if category_id:
        query = query.where(Product.category_id == category_id)
    if q:
        query = query.where(Product.name.contains(q))
    result = await db.execute(query.order_by(Product.created_at.desc()))
    products = result.scalars().all()
    result = await db.execute(select(Category).where(Category.is_active == True).order_by(Category.sort_order))
    categories = result.scalars().all()
    cat_counts = {}
    for cat in categories:
        cr = await db.execute(select(sqlfunc.count()).select_from(Product).where(Product.category_id == cat.id, Product.is_available == True))
        cat_counts[cat.id] = cr.scalar() or 0
    total_cr = await db.execute(select(sqlfunc.count()).select_from(Product).where(Product.is_available == True))
    total_count = total_cr.scalar() or 0
    user = await _get_user(request, db)
    site = await _get_site_settings(db)
    return render(request, "customer/products.html", {
        "products": products, "categories": categories,
        "selected_category": category_id, "q": q or "", "user": user, "site": site,
        "cat_counts": cat_counts, "total_count": total_count,
    })


@router.get("/product/{product_id}")
async def customer_product_detail(request: Request, product_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        return RedirectResponse("/customer/products", status_code=302)
    result = await db.execute(select(Review).where(Review.product_id == product_id).order_by(Review.created_at.desc()))
    reviews = result.scalars().all()
    result = await db.execute(select(Product).where(Product.is_available == True, Product.category_id == product.category_id, Product.id != product_id).limit(4))
    related = result.scalars().all()
    avg_rating = sum(r.rating for r in reviews) / len(reviews) if reviews else 0
    user = await _get_user(request, db)
    site = await _get_site_settings(db)
    return render(request, "customer/product_detail.html", {
        "product": product, "reviews": reviews, "related": related,
        "avg_rating": avg_rating, "user": user, "site": site,
    })


@router.get("/login")
async def customer_login_page(request: Request, db: AsyncSession = Depends(get_db)):
    site = await _get_site_settings(db)
    return render(request, "customer/login.html", {"site": site})


@router.post("/login")
async def customer_login(
    request: Request, response: Response,
    email: str = Form(...), password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    site = await _get_site_settings(db)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        return render(request, "customer/login.html", {"error": "بيانات الدخول غير صحيحة", "site": site})
    if user.is_banned:
        return render(request, "customer/login.html", {"error": "تم حظر حسابك. تواصل مع الإدارة", "site": site})
    token = create_token(user.id, user.role)
    resp = RedirectResponse("/customer/", status_code=302)
    resp.set_cookie("access_token", token, httponly=True, max_age=604800)
    return resp


@router.get("/register")
async def customer_register_page(request: Request, db: AsyncSession = Depends(get_db)):
    site = await _get_site_settings(db)
    return render(request, "customer/register.html", {"site": site})


@router.post("/register")
async def customer_register(
    request: Request, response: Response,
    name: str = Form(...), phone: str = Form(...),
    email: str = Form(""), password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    site = await _get_site_settings(db)
    if not email:
        email = f"{phone}@workshop.local"
    result = await db.execute(select(User).where(User.phone == phone))
    if result.scalar_one_or_none():
        return render(request, "customer/register.html", {"error": "رقم الجوال مستخدم بالفعل", "site": site})
    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none():
        return render(request, "customer/register.html", {"error": "البريد الإلكتروني مستخدم بالفعل", "site": site})
    user = User(name=name, phone=phone, email=email, password_hash=hash_password(password), role=UserRole.CUSTOMER.value)
    db.add(user)
    await db.flush()
    token = create_token(user.id, user.role)
    resp = RedirectResponse("/customer/", status_code=302)
    resp.set_cookie("access_token", token, httponly=True, max_age=604800)
    return resp


@router.get("/logout")
async def customer_logout():
    resp = RedirectResponse("/customer/", status_code=302)
    resp.delete_cookie("access_token")
    return resp


@router.get("/profile")
async def customer_profile(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Order).where(Order.user_id == user.id).order_by(Order.created_at.desc()))
    orders = result.scalars().all()
    result = await db.execute(select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc()))
    notifications = result.scalars().all()
    unread = sum(1 for n in notifications if not n.is_read)
    order_payments = {}
    for order in orders:
        result = await db.execute(select(Payment).where(Payment.order_id == order.id).order_by(Payment.created_at.desc()))
        order_payments[order.id] = result.scalars().all()
    return render(request, "customer/profile.html", {
        "user": user, "orders": orders, "notifications": notifications, "unread": unread,
        "order_payments": order_payments, "site": await _get_site_settings(db),
    })


@router.post("/profile/update")
async def customer_update_profile(
    request: Request, name: str = Form(...), phone: str = Form(...), email: str = Form(""),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    user.name = name
    user.phone = phone
    if email:
        user.email = email
    return RedirectResponse("/customer/profile", status_code=302)


@router.post("/order/create")
async def create_order(
    product_id: int = Form(...), quantity: int = Form(1), notes: str = Form(""),
    is_installment: bool = Form(False), whatsapp: str = Form(""),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        return RedirectResponse("/customer/products", status_code=302)

    count_result = await db.execute(select(sqlfunc.count()).select_from(Order))
    count = count_result.scalar() or 0

    order = Order(
        order_number=f"ORD-{count + 1:05d}", user_id=user.id,
        total=product.price * quantity, notes=notes, status=OrderStatus.NEW.value,
        is_installment=is_installment, whatsapp=whatsapp,
    )
    db.add(order)
    await db.flush()

    db.add(OrderItem(order_id=order.id, product_id=product.id, quantity=quantity, price=product.price))

    admins = await db.execute(select(User).where(User.role.in_([UserRole.OWNER.value, UserRole.ADMIN.value])))
    for admin in admins.scalars().all():
        db.add(Notification(
            user_id=admin.id, title="طلب جديد",
            body=f"طلب جديد رقم {order.order_number} من {user.name} — وتساب: {whatsapp} — المنتج: {product.name} — الكمية: {quantity} — المبلغ: {product.price * quantity} ر.ي",
            link="/mo/orders/" + str(order.id),
        ))

    db.add(Notification(
        user_id=user.id, title="تم إرسال طلبك",
        body=f"تم إرسال طلبك رقم {order.order_number} بنجاح. سيتم التواصل معك عبر الوتساب في أقرب وقت.",
        link="/customer/profile",
    ))

    return RedirectResponse("/customer/order-sent", status_code=302)


@router.get("/order-sent")
async def order_sent(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    site = await _get_site_settings(db)
    return render(request, "customer/order_sent.html", {"user": user, "site": site})


@router.post("/product/{product_id}/review")
async def add_review(
    product_id: int, rating: int = Form(5), comment: str = Form(""),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(Review).where(Review.user_id == user.id, Review.product_id == product_id))
    if existing.scalar_one_or_none():
        return RedirectResponse(f"/customer/product/{product_id}", status_code=302)
    db.add(Review(user_id=user.id, product_id=product_id, rating=rating, comment=comment))
    return RedirectResponse(f"/customer/product/{product_id}", status_code=302)


@router.get("/chat")
async def customer_chat(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Chat).where(Chat.user_id == user.id, Chat.is_active == True))
    chat = result.scalar_one_or_none()
    messages = []
    if chat:
        result = await db.execute(select(Message).where(Message.chat_id == chat.id).order_by(Message.created_at))
        messages = result.scalars().all()
    return render(request, "customer/chat.html", {"user": user, "chat": chat, "messages": messages, "site": await _get_site_settings(db)})


@router.post("/chat/send")
async def send_message(text: str = Form(""), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Chat).where(Chat.user_id == user.id, Chat.is_active == True))
    chat = result.scalar_one_or_none()
    if not chat:
        chat = Chat(user_id=user.id)
        db.add(chat)
        await db.flush()
    db.add(Message(chat_id=chat.id, sender_id=user.id, text=text))
    return RedirectResponse("/customer/chat", status_code=302)


@router.post("/notifications/read")
async def mark_notifications_read(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Notification).where(Notification.user_id == user.id, Notification.is_read == False))
    for n in result.scalars().all():
        n.is_read = True
    return RedirectResponse("/customer/profile", status_code=302)


@router.get("/order/{order_id}")
async def order_detail(
    request: Request, order_id: int,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Order).where(Order.id == order_id, Order.user_id == user.id))
    order = result.scalar_one_or_none()
    if not order:
        return RedirectResponse("/customer/profile", status_code=302)
    from sqlalchemy.orm import selectinload
    result = await db.execute(select(OrderItem).where(OrderItem.order_id == order.id).options(selectinload(OrderItem.product)))
    items = result.scalars().all()
    result = await db.execute(select(Payment).where(Payment.order_id == order.id).order_by(Payment.created_at.desc()))
    payments = result.scalars().all()
    return render(request, "customer/order_detail.html", {"order": order, "items": items, "payments": payments, "site": await _get_site_settings(db)})
