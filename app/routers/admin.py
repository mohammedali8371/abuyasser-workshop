from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import RedirectResponse
from sqlalchemy import select, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession
import os
import uuid
import json

from app.database import get_db
from app.models import (
    User, Product, ProductImage, Category, Order, OrderItem,
    Review, Chat, Message, Notification, SiteSetting, UserRole, OrderStatus,
    Payment, PaymentMethod, BannerImage,
)
from app.auth import get_current_user, get_admin, get_owner, verify_password, create_token, hash_password
from app.templates_mod import render

router = APIRouter()


import base64

async def _save_upload(file: UploadFile, folder: str) -> tuple:
    import base64 as b64
    from app.config import settings
    ext = os.path.splitext(file.filename or "")[1] or ".jpg"
    content = await file.read()
    if not content:
        return "", ""
    b64_data = b64.b64encode(content).decode("utf-8")
    mime = "image/jpeg"
    if ext.lower() == ".png":
        mime = "image/png"
    elif ext.lower() == ".gif":
        mime = "image/gif"
    elif ext.lower() == ".webp":
        mime = "image/webp"
    data_uri = f"data:{mime};base64,{b64_data}"
    try:
        filename = f"{uuid.uuid4().hex}{ext}"
        path = os.path.join(settings.UPLOAD_DIR, folder)
        os.makedirs(path, exist_ok=True)
        filepath = os.path.join(path, filename)
        with open(filepath, "wb") as f:
            f.write(content)
        return "/" + filepath.replace("\\", "/"), data_uri
    except Exception:
        return "", data_uri


def _status_arabic(status: str) -> str:
    return {
        "new": "جديد", "reviewing": "قيد المراجعة", "accepted": "تم القبول",
        "in_progress": "قيد التنفيذ", "ready": "جاهز", "completed": "مكتمل",
        "cancelled": "ملغي",
    }.get(status, status)


@router.get("/login")
async def admin_login_page(request: Request):
    return render(request, "admin/login.html")


@router.post("/login")
async def admin_login(request: Request, email: str = Form(...), password: str = Form(...), db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if not user:
            return render(request, "admin/login.html", {"error": f"لا يوجد مستخدم بهذا البريد: {email}"})
        if user.role not in (UserRole.OWNER.value, UserRole.ADMIN.value):
            return render(request, "admin/login.html", {"error": f"الصلاحية: {user.role} — غير مصرح"})
        if not verify_password(password, user.password_hash):
            return render(request, "admin/login.html", {"error": "كلمة المرور خاطئة"})
        token = create_token(user.id, user.role)
        resp = RedirectResponse("/mo/", status_code=302)
        resp.set_cookie("access_token", token, httponly=True, max_age=604800)
        return resp
    except Exception as e:
        return render(request, "admin/login.html", {"error": f"خطأ: {str(e)}"})


@router.get("/logout")
async def admin_logout():
    resp = RedirectResponse("/mo/login", status_code=302)
    resp.delete_cookie("access_token")
    return resp


@router.get("/")
async def admin_dashboard(request: Request, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Order).order_by(Order.created_at.desc()))
    orders = result.scalars().all()
    result = await db.execute(select(User).where(User.role == UserRole.CUSTOMER.value))
    customers = result.scalars().all()
    result = await db.execute(select(Product))
    products = result.scalars().all()
    result = await db.execute(select(Review))
    reviews = result.scalars().all()

    stats = {
        "total_customers": len(customers),
        "total_products": len(products),
        "total_orders": len(orders),
        "new_orders": sum(1 for o in orders if o.status == "new"),
        "in_progress_orders": sum(1 for o in orders if o.status == "in_progress"),
        "completed_orders": sum(1 for o in orders if o.status == "completed"),
        "avg_rating": round(sum(r.rating for r in reviews) / len(reviews), 1) if reviews else 0,
        "total_reviews": len(reviews),
    }
    return render(request, "admin/dashboard.html", {"user": user, "stats": stats, "recent_orders": orders[:10]})


@router.get("/products")
async def admin_products(request: Request, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    from sqlalchemy.orm import selectinload
    result = await db.execute(select(Product).order_by(Product.created_at.desc()).options(
        selectinload(Product.images), selectinload(Product.category)
    ))
    products = result.scalars().all()
    result = await db.execute(select(Category))
    categories = result.scalars().all()
    return render(request, "admin/products.html", {"user": user, "products": products, "categories": categories})


@router.post("/products/add")
async def admin_add_product(
    request: Request,
    user: User = Depends(get_admin), db: AsyncSession = Depends(get_db),
):
    form = await request.form()
    name = form.get("name", "")
    description = form.get("description", "")
    price = float(form.get("price", 0) or 0)
    category_id = form.get("category_id")
    is_available = form.get("is_available") in ("on", "1", "true", True)
    stock = int(form.get("stock", 0) or 0)
    image = form.get("image")
    image_path = ""
    image_data = ""
    if image and hasattr(image, "filename") and image.filename:
        image_path, image_data = await _save_upload(image, "products")
    db.add(Product(
        name=name, description=description, price=price,
        category_id=int(category_id) if category_id else None,
        is_available=is_available, stock=stock, image=image_data or image_path,
    ))
    await db.flush()

    extra_images = form.getlist("extra_images")
    for idx, img in enumerate(extra_images):
        if img and hasattr(img, "filename") and img.filename:
            path, data = await _save_upload(img, "products")
            db.add(ProductImage(product_id=product.id, image_data=data or path, sort_order=idx))

    await db.commit()
    return RedirectResponse("/mo/products", status_code=302)


@router.post("/products/{product_id}/update")
async def admin_update_product(
    product_id: int, request: Request,
    user: User = Depends(get_admin), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if product:
        form = await request.form()
        product.name = form.get("name", "")
        product.description = form.get("description", "")
        product.price = float(form.get("price", 0) or 0)
        cat_id = form.get("category_id")
        product.category_id = int(cat_id) if cat_id else None
        product.is_available = form.get("is_available") in ("on", "1", "true", True)
        product.stock = int(form.get("stock", 0) or 0)

        image = form.get("image")
        if image and hasattr(image, "filename") and image.filename:
            path, data = await _save_upload(image, "products")
            product.image = data or path

        if form.get("replace_images") == "1":
            for old_img in product.images:
                await db.delete(old_img)
            extra_images = form.getlist("extra_images")
            for idx, img in enumerate(extra_images):
                if img and hasattr(img, "filename") and img.filename:
                    path, data = await _save_upload(img, "products")
                    db.add(ProductImage(product_id=product.id, image_data=data or path, sort_order=idx))
        else:
            extra_images = form.getlist("extra_images")
            if extra_images:
                max_order = max((i.sort_order for i in product.images), default=-1)
                for idx, img in enumerate(extra_images):
                    if img and hasattr(img, "filename") and img.filename:
                        path, data = await _save_upload(img, "products")
                        db.add(ProductImage(product_id=product.id, image_data=data or path, sort_order=max_order + idx + 1))

        await db.commit()
    return RedirectResponse("/mo/products", status_code=302)


@router.post("/products/{product_id}/delete")
async def admin_delete_product(product_id: int, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if product:
        await db.delete(product)
    return RedirectResponse("/mo/products", status_code=302)


@router.get("/categories")
async def admin_categories(request: Request, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Category).order_by(Category.created_at.desc()))
    categories = result.scalars().all()
    return render(request, "admin/categories.html", {"user": user, "categories": categories})


@router.post("/categories/add")
async def admin_add_category(
    request: Request, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db),
):
    form = await request.form()
    name = form.get("name", "")
    description = form.get("description", "")
    is_active = form.get("is_active") in ("on", "1", "true", True)
    image = form.get("image")
    image_path = ""
    image_data = ""
    if image and hasattr(image, "filename") and image.filename:
        image_path, image_data = await _save_upload(image, "categories")
    db.add(Category(name=name, description=description, is_active=is_active, image=image_data or image_path))
    await db.commit()
    return RedirectResponse("/mo/categories", status_code=302)


@router.post("/categories/{category_id}/update")
async def admin_update_category(
    category_id: int, request: Request,
    user: User = Depends(get_admin), db: AsyncSession = Depends(get_db),
):
    form = await request.form()
    result = await db.execute(select(Category).where(Category.id == category_id))
    cat = result.scalar_one_or_none()
    if cat:
        cat.name = form.get("name", "")
        cat.description = form.get("description", "")
        cat.is_active = form.get("is_active") in ("on", "1", "true", True)
        image = form.get("image")
        if image and hasattr(image, "filename") and image.filename:
            path, data = await _save_upload(image, "categories")
            cat.image = data or path
        await db.commit()
    return RedirectResponse("/mo/categories", status_code=302)


@router.post("/categories/{category_id}/delete")
async def admin_delete_category(category_id: int, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Category).where(Category.id == category_id))
    cat = result.scalar_one_or_none()
    if cat:
        await db.delete(cat)
    return RedirectResponse("/mo/categories", status_code=302)


@router.get("/orders")
async def admin_orders(request: Request, status: str = None, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    query = select(Order).order_by(Order.created_at.desc())
    if status:
        query = query.where(Order.status == status)
    result = await db.execute(query)
    orders = result.scalars().all()
    return render(request, "admin/orders.html", {"user": user, "orders": orders, "selected_status": status})


@router.get("/orders/{order_id}")
async def admin_order_detail(request: Request, order_id: int, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        return RedirectResponse("/mo/orders", status_code=302)
    from sqlalchemy.orm import selectinload
    result = await db.execute(select(OrderItem).where(OrderItem.order_id == order_id).options(selectinload(OrderItem.product)))
    items = result.scalars().all()
    result = await db.execute(select(User).where(User.id == order.user_id))
    customer = result.scalar_one_or_none()
    result = await db.execute(
        select(Payment).where(Payment.order_id == order_id).order_by(Payment.created_at.desc())
    )
    payments = result.scalars().all()
    total_paid = sum(p.amount for p in payments)
    remaining = max(0, order.total - total_paid)
    return render(request, "admin/order_detail.html", {
        "user": user, "order": order, "items": items, "customer": customer,
        "payments": payments, "total_paid": total_paid, "remaining": remaining,
    })


@router.post("/orders/{order_id}/status")
async def admin_update_order_status(
    order_id: int, status: str = Form(...), user: User = Depends(get_admin), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if order:
        order.status = status
        db.add(Notification(
            user_id=order.user_id, title="تحديث حالة الطلب",
            body=f"تم تحديث حالة طلبك رقم {order.order_number} إلى {_status_arabic(status)}",
            link="/customer/profile",
        ))
    return RedirectResponse(f"/mo/orders/{order_id}", status_code=302)


@router.get("/customers")
async def admin_customers(request: Request, q: str = None, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    query = select(User).where(User.role == UserRole.CUSTOMER.value)
    if q:
        query = query.where(User.name.contains(q) | User.phone.contains(q) | User.email.contains(q))
    result = await db.execute(query.order_by(User.created_at.desc()))
    customers = result.scalars().all()
    balances = {}
    for c in customers:
        r = await db.execute(select(Order).where(Order.user_id == c.id))
        orders = r.scalars().all()
        total = sum(o.total for o in orders)
        paid = sum(o.paid_amount for o in orders)
        balances[c.id] = max(0, total - paid)
    return render(request, "admin/customers.html", {"user": user, "customers": customers, "q": q or "", "balances": balances})


@router.post("/customers/{customer_id}/ban")
async def admin_ban_customer(customer_id: int, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == customer_id))
    customer = result.scalar_one_or_none()
    if customer:
        customer.is_banned = not customer.is_banned
    return RedirectResponse("/mo/customers", status_code=302)


@router.get("/chats")
async def admin_chats(request: Request, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Chat).order_by(Chat.created_at.desc()))
    chats = result.scalars().all()
    chat_users = {}
    for chat in chats:
        r = await db.execute(select(User).where(User.id == chat.user_id))
        chat_users[chat.id] = r.scalar_one_or_none()
    return render(request, "admin/chats.html", {"user": user, "chats": chats, "chat_users": chat_users})


@router.get("/chats/{chat_id}")
async def admin_chat_detail(request: Request, chat_id: int, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Chat).where(Chat.id == chat_id))
    chat = result.scalar_one_or_none()
    if not chat:
        return RedirectResponse("/mo/chats", status_code=302)
    chat.admin_id = user.id
    result = await db.execute(select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at))
    messages = result.scalars().all()
    for msg in messages:
        if msg.sender_id != user.id:
            msg.is_read = True
    result = await db.execute(select(User).where(User.id == chat.user_id))
    customer = result.scalar_one_or_none()
    return render(request, "admin/chat_detail.html", {"user": user, "chat": chat, "messages": messages, "customer": customer})


@router.post("/chats/{chat_id}/send")
async def admin_send_message(chat_id: int, text: str = Form(""), user: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    db.add(Message(chat_id=chat_id, sender_id=user.id, text=text))
    return RedirectResponse(f"/mo/chats/{chat_id}", status_code=302)


@router.post("/chats/{chat_id}/close")
async def admin_close_chat(chat_id: int, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Chat).where(Chat.id == chat_id))
    chat = result.scalar_one_or_none()
    if chat:
        chat.is_active = False
    return RedirectResponse("/mo/chats", status_code=302)


@router.get("/reviews")
async def admin_reviews(request: Request, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Review).order_by(Review.created_at.desc()))
    reviews = result.scalars().all()
    review_data = []
    for r in reviews:
        u = (await db.execute(select(User).where(User.id == r.user_id))).scalar_one_or_none()
        p = (await db.execute(select(Product).where(Product.id == r.product_id))).scalar_one_or_none()
        review_data.append({"review": r, "user": u, "product": p})
    return render(request, "admin/reviews.html", {"user": user, "review_data": review_data})


@router.post("/reviews/{review_id}/delete")
async def admin_delete_review(review_id: int, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Review).where(Review.id == review_id))
    review = result.scalar_one_or_none()
    if review:
        await db.delete(review)
    return RedirectResponse("/mo/reviews", status_code=302)


@router.get("/settings")
async def admin_settings_page(request: Request, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SiteSetting))
    settings_list = result.scalars().all()
    settings_dict = {}
    for s in settings_list:
        settings_dict[s.key] = s.value
    return render(request, "admin/settings.html", {"user": user, "settings": settings_dict})


@router.post("/settings")
async def admin_update_settings(request: Request, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    form = await request.form()
    for key, value in form.items():
        if key == "currency":
            value = str(value)
        result = await db.execute(select(SiteSetting).where(SiteSetting.key == key))
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = json.dumps(str(value), ensure_ascii=False)
        else:
            db.add(SiteSetting(key=key, value=json.dumps(str(value), ensure_ascii=False)))
    await db.commit()
    # Invalidate customer-side cache
    try:
        from app.routers import customer
        customer._site_cache["data"] = None
        customer._site_cache["ts"] = 0
    except Exception:
        pass
    return RedirectResponse("/mo/settings", status_code=302)


@router.get("/notifications")
async def admin_notifications(request: Request, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Notification).order_by(Notification.created_at.desc()))
    notifications = result.scalars().all()
    notif_users = {}
    for n in notifications:
        if n.user_id not in notif_users:
            r = await db.execute(select(User).where(User.id == n.user_id))
            notif_users[n.user_id] = r.scalar_one_or_none()
    return render(request, "admin/notifications.html", {"user": user, "notifications": notifications, "notif_users": notif_users})


@router.post("/notifications/send")
async def admin_send_notification(
    user_id: int = Form(...), title: str = Form(...), body: str = Form(""), link: str = Form(""),
    user: User = Depends(get_admin), db: AsyncSession = Depends(get_db),
):
    db.add(Notification(user_id=user_id, title=title, body=body, link=link))
    return RedirectResponse("/mo/notifications", status_code=302)


@router.get("/managers")
async def admin_managers(request: Request, user: User = Depends(get_owner), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.role == UserRole.ADMIN.value).order_by(User.created_at.desc()))
    managers = result.scalars().all()
    return render(request, "admin/managers.html", {"user": user, "managers": managers})


@router.post("/managers/add")
async def admin_add_manager(
    name: str = Form(...), phone: str = Form(...), email: str = Form(...), password: str = Form(...),
    user: User = Depends(get_owner), db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        return RedirectResponse("/mo/managers", status_code=302)
    db.add(User(
        name=name, phone=phone, email=email,
        password_hash=hash_password(password), role=UserRole.ADMIN.value,
    ))
    return RedirectResponse("/mo/managers", status_code=302)


@router.post("/managers/{manager_id}/delete")
async def admin_delete_manager(manager_id: int, user: User = Depends(get_owner), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == manager_id, User.role == UserRole.ADMIN.value))
    manager = result.scalar_one_or_none()
    if manager:
        await db.delete(manager)
    return RedirectResponse("/mo/managers", status_code=302)


@router.post("/managers/{manager_id}/toggle")
async def admin_toggle_manager(manager_id: int, user: User = Depends(get_owner), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == manager_id, User.role == UserRole.ADMIN.value))
    manager = result.scalar_one_or_none()
    if manager:
        manager.is_banned = not manager.is_banned
    return RedirectResponse("/mo/managers", status_code=302)


# ── Payment / Installment Routes ──────────────────────────────────────


@router.post("/orders/{order_id}/pay")
async def admin_record_payment(
    order_id: int, amount: float = Form(...), note: str = Form(""),
    user: User = Depends(get_admin), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        return RedirectResponse("/mo/orders", status_code=302)
    if amount > 0:
        db.add(Payment(order_id=order_id, amount=amount, note=note))
        order.paid_amount = order.paid_amount + amount
    return RedirectResponse(f"/mo/orders/{order_id}", status_code=302)


@router.get("/orders/{order_id}/payments")
async def admin_order_payments(
    request: Request, order_id: int,
    user: User = Depends(get_admin), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        return RedirectResponse("/mo/orders", status_code=302)
    result = await db.execute(
        select(Payment).where(Payment.order_id == order_id).order_by(Payment.created_at.desc())
    )
    payments = result.scalars().all()
    total_paid = sum(p.amount for p in payments)
    remaining = max(0, order.total - total_paid)
    return render(request, "admin/order_payments.html", {
        "user": user, "order": order, "payments": payments,
        "total_paid": total_paid, "remaining": remaining,
    })


# ── Admin Profile Routes ──────────────────────────────────────────────


@router.get("/profile")
async def admin_profile_page(request: Request, user: User = Depends(get_admin)):
    return render(request, "admin/profile.html", {"user": user})


@router.post("/profile")
async def admin_update_profile(
    request: Request,
    name: str = Form(...), phone: str = Form(""), email: str = Form(""),
    password: str = Form(""), user: User = Depends(get_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user.id))
    db_user = result.scalar_one_or_none()
    if db_user:
        db_user.name = name
        db_user.phone = phone
        db_user.email = email
        if password:
            db_user.password_hash = hash_password(password)
    return RedirectResponse("/mo/profile", status_code=302)


# ── Payment Methods Routes ──────────────────────────────────────────────


@router.get("/payment-methods")
async def admin_payment_methods(request: Request, user: User = Depends(get_owner), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PaymentMethod).order_by(PaymentMethod.sort_order))
    methods = result.scalars().all()
    return render(request, "admin/payment_methods.html", {"user": user, "methods": methods})


@router.post("/payment-methods/add")
async def admin_add_payment_method(
    name: str = Form(...), icon: str = Form("bi-credit-card"),
    details: str = Form(""), sort_order: int = Form(0),
    user: User = Depends(get_owner), db: AsyncSession = Depends(get_db),
):
    db.add(PaymentMethod(name=name, icon=icon, details=details, sort_order=sort_order))
    return RedirectResponse("/mo/payment-methods", status_code=302)


@router.post("/payment-methods/{method_id}/update")
async def admin_update_payment_method(
    method_id: int, name: str = Form(...), icon: str = Form("bi-credit-card"),
    details: str = Form(""), is_active: bool = Form(True), sort_order: int = Form(0),
    user: User = Depends(get_owner), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(PaymentMethod).where(PaymentMethod.id == method_id))
    method = result.scalar_one_or_none()
    if method:
        method.name = name
        method.icon = icon
        method.details = details
        method.is_active = is_active
        method.sort_order = sort_order
    return RedirectResponse("/mo/payment-methods", status_code=302)


@router.post("/payment-methods/{method_id}/delete")
async def admin_delete_payment_method(method_id: int, user: User = Depends(get_owner), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PaymentMethod).where(PaymentMethod.id == method_id))
    method = result.scalar_one_or_none()
    if method:
        await db.delete(method)
    return RedirectResponse("/mo/payment-methods", status_code=302)


@router.post("/payment-methods/{method_id}/toggle")
async def admin_toggle_payment_method(method_id: int, user: User = Depends(get_owner), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PaymentMethod).where(PaymentMethod.id == method_id))
    method = result.scalar_one_or_none()
    if method:
        method.is_active = not method.is_active
    return RedirectResponse("/mo/payment-methods", status_code=302)


# ── Customer Balance & Reminder Routes ──────────────────────────────────


@router.get("/customers/{customer_id}/balance")
async def admin_customer_balance(
    request: Request, customer_id: int,
    user: User = Depends(get_admin), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        return RedirectResponse("/mo/customers", status_code=302)
    result = await db.execute(select(Order).where(Order.user_id == customer_id).order_by(Order.created_at.desc()))
    orders = result.scalars().all()
    payments_dict = {}
    for order in orders:
        r = await db.execute(select(Payment).where(Payment.order_id == order.id).order_by(Payment.created_at.desc()))
        payments_dict[order.id] = r.scalars().all()
    total_all = sum(o.total for o in orders)
    total_paid = sum(o.paid_amount for o in orders)
    total_remaining = max(0, total_all - total_paid)
    return render(request, "admin/customer_balance.html", {
        "user": user, "customer": customer, "orders": orders,
        "payments_dict": payments_dict, "total_all": total_all,
        "total_paid": total_paid, "total_remaining": total_remaining,
    })


@router.post("/orders/{order_id}/adjust")
async def admin_adjust_payment(
    order_id: int, paid_amount: float = Form(...),
    user: User = Depends(get_admin), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if order:
        order.paid_amount = paid_amount
    return RedirectResponse(f"/mo/orders/{order_id}", status_code=302)


@router.post("/customers/{customer_id}/remind")
async def admin_send_reminder(
    customer_id: int,
    user: User = Depends(get_admin), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        return RedirectResponse("/mo/customers", status_code=302)
    result = await db.execute(select(Order).where(Order.user_id == customer_id))
    orders = result.scalars().all()
    total_remaining = sum(max(0, o.total - o.paid_amount) for o in orders)
    if total_remaining > 0:
        db.add(Notification(
            user_id=customer_id, title="تذكير بالدفع",
            body=f"مرحباً {customer.name}، يُبقى لديك مبلغ {total_remaining} ر.ي كمتبقي. يرجى السداد في أقرب وقت.",
            link="/customer/profile",
        ))
        db.add(Notification(
            user_id=user.id, title="تم إرسال تنبيه",
            body=f"تم إرسال تنبيه دفع للعميل {customer.name} — المتبقي: {total_remaining} ر.ي",
        ))
    return RedirectResponse(f"/mo/customers/{customer_id}/balance", status_code=302)


@router.get("/banners")
async def admin_banners(request: Request, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BannerImage).order_by(BannerImage.sort_order, BannerImage.id.desc()))
    banners = result.scalars().all()
    return render(request, "admin/banners.html", {"user": user, "banners": banners})


@router.post("/banners/create")
async def admin_banner_create(
    request: Request, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db),
):
    form = await request.form()
    title = form.get("title", "")
    subtitle = form.get("subtitle", "")
    link = form.get("link", "")
    sort_order = int(form.get("sort_order", 0))
    image_file = form.get("image")
    image_data = ""
    if image_file and hasattr(image_file, "read"):
        image_data = await _save_upload_data(image_file)
    banner = BannerImage(title=title, subtitle=subtitle, link=link, image_data=image_data, sort_order=sort_order)
    db.add(banner)
    return RedirectResponse("/mo/banners", status_code=302)


@router.post("/banners/{banner_id}/delete")
async def admin_banner_delete(
    banner_id: int, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(BannerImage).where(BannerImage.id == banner_id))
    banner = result.scalar_one_or_none()
    if banner:
        await db.delete(banner)
    return RedirectResponse("/mo/banners", status_code=302)


@router.post("/banners/{banner_id}/toggle")
async def admin_banner_toggle(
    banner_id: int, user: User = Depends(get_admin), db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(BannerImage).where(BannerImage.id == banner_id))
    banner = result.scalar_one_or_none()
    if banner:
        banner.is_active = not banner.is_active
    return RedirectResponse("/mo/banners", status_code=302)


async def _save_upload_data(file) -> str:
    import base64 as b64
    content = await file.read()
    ext = os.path.splitext(getattr(file, "filename", "") or "")[1].lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"
    b64_str = b64.b64encode(content).decode("utf-8")
    return f"data:{mime};base64,{b64_str}"
