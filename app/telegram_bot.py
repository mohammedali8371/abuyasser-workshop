import os
import asyncio
import logging
import json
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
)
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import async_session
from app.models import (
    User, Order, OrderItem, Product, Category, Payment,
    Notification, Chat, Message, SiteSetting, PaymentMethod, Review, UserRole, OrderStatus
)

logger = logging.getLogger("telegram_bot")

BOT_TOKEN = settings.TELEGRAM_BOT_TOKEN
OWNER_ID = settings.TELEGRAM_OWNER_ID

STATUS_LABELS = {
    "new": "جديد",
    "reviewing": "قيد المراجعة",
    "accepted": "مقبول",
    "in_progress": "قيد التنفيذ",
    "ready": "جاهز",
    "completed": "مكتمل",
    "cancelled": "ملغي",
}


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


async def main_menu_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("لوحة التحكم", callback_data="dashboard")],
        [
            InlineKeyboardButton("الطلبات", callback_data="orders"),
            InlineKeyboardButton("المنتجات", callback_data="products"),
        ],
        [
            InlineKeyboardButton("العملاء", callback_data="customers"),
            InlineKeyboardButton("المراجعات", callback_data="reviews"),
        ],
        [
            InlineKeyboardButton("المدفوعات", callback_data="payments"),
            InlineKeyboardButton("طرق الدفع", callback_data="pay_methods"),
        ],
        [
            InlineKeyboardButton("الإعدادات", callback_data="settings"),
            InlineKeyboardButton("الإشعارات", callback_data="send_notif"),
        ],
        [
            InlineKeyboardButton("الدردشات", callback_data="chats"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


async def back_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("القائمة الرئيسية", callback_data="main_menu")]])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("هذا البوت خاص بالمالك فقط.")
        return
    await update.message.reply_text(
        "مرحباً بك في لوحة تحكم ورشة أبو ياسر الصرماح",
        reply_markup=await main_menu_keyboard(),
    )


async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if not is_owner(uid):
        await query.edit_message_text("غير مصرح لك.")
        return

    data = query.data

    if data == "main_menu":
        await query.edit_message_text("القائمة الرئيسية:", reply_markup=await main_menu_keyboard())

    elif data == "dashboard":
        await show_dashboard(query)

    elif data == "orders":
        await show_orders(query, 0)

    elif data.startswith("order_"):
        oid = int(data.split("_")[1])
        await show_order_detail(query, oid)

    elif data.startswith("status_"):
        parts = data.split("_")
        oid = int(parts[1])
        status = parts[2]
        await change_order_status(query, oid, status)

    elif data == "products":
        await show_products(query)

    elif data.startswith("prod_"):
        pid = int(data.split("_")[1])
        await show_product_detail(query, pid)

    elif data == "customers":
        await show_customers(query, 0)

    elif data.startswith("cust_"):
        cid = int(data.split("_")[1])
        await show_customer_detail(query, cid)

    elif data == "reviews":
        await show_reviews(query)

    elif data == "payments":
        await show_payments(query)

    elif data == "pay_methods":
        await show_pay_methods(query)

    elif data == "settings":
        await show_settings(query)

    elif data.startswith("edit_setting_"):
        key = data[len("edit_setting_"):]
        context.user_data["awaiting_setting"] = key
        await query.edit_message_text(f"أرسل القيمة الجديدة للإعداد:\n**{key}**", parse_mode="Markdown", reply_markup=await back_main())

    elif data == "send_notif":
        context.user_data["awaiting_notif"] = True
        await query.edit_message_text("أرسل نص الإشعار الذي تريد إرساله لجميع العملاء:", reply_markup=await back_main())

    elif data == "chats":
        await show_chats(query)

    elif data.startswith("chat_"):
        cid = int(data.split("_")[1])
        context.user_data["active_chat"] = cid
        await show_chat_detail(query, cid)

    elif data.startswith("remind_"):
        cid = int(data.split("_")[1])
        async with async_session() as db:
            result = await db.execute(select(User).where(User.id == cid))
            customer = result.scalar_one_or_none()
            if customer:
                notif = Notification(
                    user_id=customer.id,
                    title="تذكير بالدفع",
                    body="يرجى سداد المبلغ المتبقي على طلبك. تواصل معنا لمزيد من التفاصيل.",
                )
                db.add(notif)
                await db.commit()
                await query.edit_message_text(f"تم إرسال تذكير لـ {customer.name}.", reply_markup=await back_main())
            else:
                await query.edit_message_text("العميل غير موجود.", reply_markup=await main_menu_keyboard())

    elif data.startswith("toggle_ban_"):
        cid = int(data.split("_")[1])
        async with async_session() as db:
            result = await db.execute(select(User).where(User.id == cid))
            customer = result.scalar_one_or_none()
            if customer:
                customer.is_banned = not customer.is_banned
                await db.commit()
                status = "تم حظر" if customer.is_banned else "تم إلغاء الحظر"
                await query.edit_message_text(f"{status}: {customer.name}", reply_markup=await back_main())
            else:
                await query.edit_message_text("العميل غير موجود.", reply_markup=await main_menu_keyboard())

    elif data.startswith("close_chat_"):
        cid = int(data.split("_")[1])
        async with async_session() as db:
            result = await db.execute(select(Chat).where(Chat.id == cid))
            chat = result.scalar_one_or_none()
            if chat:
                chat.is_active = False
                await db.commit()
                await query.edit_message_text("تم إغلاق الدردشة.", reply_markup=await main_menu_keyboard())
            else:
                await query.edit_message_text("الدردشة غير موجودة.", reply_markup=await main_menu_keyboard())

    elif data.startswith("orders_page_"):
        page = int(data.split("_")[2])
        await show_orders(query, page)

    elif data.startswith("customers_page_"):
        page = int(data.split("_")[2])
        await show_customers(query, page)

    elif data == "main_menu":
        await query.edit_message_text("القائمة الرئيسية:", reply_markup=await main_menu_keyboard())


async def show_dashboard(query):
    async with async_session() as db:
        total_orders = (await db.execute(select(func.count(Order.id)))).scalar() or 0
        total_products = (await db.execute(select(func.count(Product.id)))).scalar() or 0
        total_customers = (await db.execute(select(func.count(User.id)).where(User.role == UserRole.CUSTOMER.value))).scalar() or 0
        total_revenue = (await db.execute(select(func.coalesce(func.sum(Order.paid_amount), 0)))).scalar() or 0
        new_orders = (await db.execute(select(func.count(Order.id)).where(Order.status == OrderStatus.NEW.value))).scalar() or 0
        pending_orders = (await db.execute(select(func.count(Order.id)).where(Order.status.in_(["reviewing", "accepted", "in_progress"])))).scalar() or 0
        unread_chats = (await db.execute(select(func.count(Chat.id)).where(Chat.is_active == True))).scalar() or 0
        total_reviews = (await db.execute(select(func.count(Review.id)))).scalar() or 0

    text = (
        f"لوحة التحكم - ورشة أبو ياسر الصرماح\n"
        f"{'='*30}\n\n"
        f"الإحصائيات:\n"
        f"إجمالي الطلبات: {total_orders}\n"
        f"طلبات جديدة: {new_orders}\n"
        f"قيد التنفيذ: {pending_orders}\n"
        f"إجمالي المنتجات: {total_products}\n"
        f"العملاء: {total_customers}\n"
        f"المدفوعات: {total_revenue} ر.ي\n"
        f"التقييمات: {total_reviews}\n"
        f"الدردشات النشطة: {unread_chats}\n"
    )
    await query.edit_message_text(text, reply_markup=await main_menu_keyboard())


async def show_orders(query, page):
    async with async_session() as db:
        q = select(Order).options(selectinload(Order.user)).order_by(Order.id.desc())
        total = (await db.execute(select(func.count(Order.id)))).scalar() or 0
        orders = (await db.execute(q.offset(page * 8).limit(8))).scalars().all()

    if not orders:
        await query.edit_message_text("لا توجد طلبات بعد.", reply_markup=await main_menu_keyboard())
        return

    text = f"الطلبات (صفحة {page+1}/{(total+7)//8})\n{'='*30}\n\n"
    buttons = []
    for o in orders:
        status = STATUS_LABELS.get(o.status, o.status)
        text += f"#{o.order_number} | {status} | {o.total} ر.ي | {o.user.name if o.user else '?'}\n"
        buttons.append([InlineKeyboardButton(f"#{o.order_number} - {status}", callback_data=f"order_{o.id}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("السابق", callback_data=f"orders_page_{page-1}"))
    if (page + 1) * 8 < total:
        nav.append(InlineKeyboardButton("التالي", callback_data=f"orders_page_{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("القائمة الرئيسية", callback_data="main_menu")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def show_order_detail(query, oid):
    async with async_session() as db:
        result = await db.execute(
            select(Order).where(Order.id == oid).options(
                selectinload(Order.user), selectinload(Order.items).selectinload(OrderItem.product),
                selectinload(Order.payments)
            )
        )
        order = result.scalar_one_or_none()
        if not order:
            await query.edit_message_text("الطلب غير موجود.", reply_markup=await main_menu_keyboard())
            return

        items_text = ""
        for item in order.items:
            pname = item.product.name if item.product else "محذوف"
            items_text += f"  - {pname} x{item.quantity} = {item.price * item.quantity} ر.ي\n"

        payments_text = ""
        for p in order.payments:
            payments_text += f"  - {p.amount} ر.ي ({p.note or 'بدون ملاحظة'}) | {p.created_at}\n"

    status = STATUS_LABELS.get(order.status, order.status)
    text = (
        f"تفاصيل الطلب #{order.order_number}\n"
        f"{'='*30}\n"
        f"العميل: {order.user.name if order.user else 'غير معروف'}\n"
        f"الوتساب: {order.whatsapp}\n"
        f"الحالة: {status}\n"
        f"الإجمالي: {order.total} ر.ي\n"
        f"المدفوع: {order.paid_amount} ر.ي\n"
        f"المتبقي: {order.remaining} ر.ي\n"
        f"التقسيط: {'نعم' if order.is_installment else 'لا'}\n"
        f"ملاحظات: {order.notes or 'لا توجد'}\n"
        f"التاريخ: {order.created_at}\n\n"
        f"الأصناف:\n{items_text}\n"
    )
    if payments_text:
        text += f"المدفوعات:\n{payments_text}\n"

    buttons = []
    status_options = ["new", "reviewing", "accepted", "in_progress", "ready", "completed", "cancelled"]
    row = []
    for s in status_options:
        label = STATUS_LABELS[s]
        row.append(InlineKeyboardButton(label, callback_data=f"status_{oid}_{s}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("رجوع للطلبات", callback_data="orders")])
    buttons.append([InlineKeyboardButton("القائمة الرئيسية", callback_data="main_menu")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def change_order_status(query, oid, status):
    async with async_session() as db:
        result = await db.execute(select(Order).where(Order.id == oid))
        order = result.scalar_one_or_none()
        if not order:
            await query.edit_message_text("الطلب غير موجود.", reply_markup=await main_menu_keyboard())
            return
        order.status = status
        await db.commit()

        if order.user_id:
            notif = Notification(
                user_id=order.user_id,
                title="تحديث حالة الطلب",
                body=f"تم تحديث حالة طلبك #{order.order_number} إلى: {STATUS_LABELS.get(status, status)}",
                link=f"/customer/order/{order.id}"
            )
            db.add(notif)
            await db.commit()

    await query.edit_message_text(f"تم تحديث حالة الطلب #{oid} إلى: {STATUS_LABELS.get(status, status)}", reply_markup=await back_main())


async def show_products(query):
    async with async_session() as db:
        products = (await db.execute(
            select(Product).options(selectinload(Product.category)).order_by(Product.id.desc()).limit(20)
        )).scalars().all()

    if not products:
        await query.edit_message_text("لا توجد منتجات بعد.", reply_markup=await main_menu_keyboard())
        return

    text = "المنتجات:\n" + "="*30 + "\n\n"
    buttons = []
    for p in products:
        cat = p.category.name if p.category else "بدون فئة"
        avail = "متوفر" if p.is_available else "غير متوفر"
        text += f"{p.name} | {p.price} ر.ي | {cat} | {avail}\n"
        buttons.append([InlineKeyboardButton(p.name, callback_data=f"prod_{p.id}")])
    buttons.append([InlineKeyboardButton("القائمة الرئيسية", callback_data="main_menu")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def show_product_detail(query, pid):
    async with async_session() as db:
        result = await db.execute(
            select(Product).where(Product.id == pid).options(selectinload(Product.category))
        )
        product = result.scalar_one_or_none()
        if not product:
            await query.edit_message_text("المنتج غير موجود.", reply_markup=await main_menu_keyboard())
            return

        review_count = (await db.execute(select(func.count(Review.id)).where(Review.product_id == pid))).scalar() or 0
        avg_rating = (await db.execute(select(func.coalesce(func.avg(Review.rating), 0)).where(Review.product_id == pid))).scalar() or 0

    cat = product.category.name if product.category else "بدون فئة"
    avail = "متوفر" if product.is_available else "غير متوفر"
    text = (
        f"المنتج: {product.name}\n"
        f"السعر: {product.price} ر.ي\n"
        f"الفئة: {cat}\n"
        f"الحالة: {avail}\n"
        f"المخزون: {product.stock}\n"
        f"التقييم: {avg_rating}/5 ({review_count} تقييم)\n"
        f"الوصف: {product.description or 'لا يوجد'}\n"
    )
    await query.edit_message_text(text, reply_markup=await back_main())


async def show_customers(query, page):
    async with async_session() as db:
        total = (await db.execute(select(func.count(User.id)).where(User.role == UserRole.CUSTOMER.value))).scalar() or 0
        customers = (await db.execute(
            select(User).where(User.role == UserRole.CUSTOMER.value)
            .options(selectinload(User.orders))
            .order_by(User.id.desc()).offset(page * 8).limit(8)
        )).scalars().all()

    if not customers:
        await query.edit_message_text("لا يوجد عملاء بعد.", reply_markup=await main_menu_keyboard())
        return

    text = f"العملاء (صفحة {page+1}/{(total+7)//8})\n{'='*30}\n\n"
    buttons = []
    for c in customers:
        order_count = len(c.orders)
        total_paid = sum(o.paid_amount for o in c.orders)
        text += f"{c.name} | {c.phone} | طلبات: {order_count} | المدفوع: {total_paid} ر.ي\n"
        buttons.append([InlineKeyboardButton(f"{c.name} ({order_count} طلبات)", callback_data=f"cust_{c.id}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("السابق", callback_data=f"customers_page_{page-1}"))
    if (page + 1) * 8 < total:
        nav.append(InlineKeyboardButton("التالي", callback_data=f"customers_page_{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("القائمة الرئيسية", callback_data="main_menu")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def show_customer_detail(query, cid):
    async with async_session() as db:
        result = await db.execute(
            select(User).where(User.id == cid).options(selectinload(User.orders))
        )
        customer = result.scalar_one_or_none()
        if not customer:
            await query.edit_message_text("العميل غير موجود.", reply_markup=await main_menu_keyboard())
            return

    total_paid = sum(o.paid_amount for o in customer.orders)
    total_remaining = sum(o.remaining for o in customer.orders)
    text = (
        f"العميل: {customer.name}\n"
        f"الهاتف: {customer.phone}\n"
        f"البريد: {customer.email}\n"
        f"الطلبات: {len(customer.orders)}\n"
        f"المدفوع: {total_paid} ر.ي\n"
        f"المتبقي: {total_remaining} ر.ي\n"
        f"محظور: {'نعم' if customer.is_banned else 'لا'}\n"
        f"تاريخ التسجيل: {customer.created_at}\n"
    )

    buttons = [
        [InlineKeyboardButton("إرسال تذكير بالدفع", callback_data=f"remind_{cid}")],
        [InlineKeyboardButton("حظر/إلغاء الحظر", callback_data=f"toggle_ban_{cid}")],
        [InlineKeyboardButton("رجوع للعملاء", callback_data="customers")],
        [InlineKeyboardButton("القائمة الرئيسية", callback_data="main_menu")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def show_reviews(query):
    async with async_session() as db:
        reviews = (await db.execute(
            select(Review).options(
                selectinload(Review.user), selectinload(Review.product)
            ).order_by(Review.id.desc()).limit(10)
        )).scalars().all()

    if not reviews:
        await query.edit_message_text("لا توجد تقييمات بعد.", reply_markup=await main_menu_keyboard())
        return

    text = "آخر التقييمات:\n" + "="*30 + "\n\n"
    for r in reviews:
        stars = "⭐" * r.rating
        user = r.user.name if r.user else "مجهول"
        product = r.product.name if r.product else "محذوف"
        text += f"{stars} | {user} | {product}\n{r.comment}\n\n"

    await query.edit_message_text(text, reply_markup=await back_main())


async def show_payments(query):
    async with async_session() as db:
        payments = (await db.execute(
            select(Payment).options(
                selectinload(Payment.order)
            ).order_by(Payment.id.desc()).limit(10)
        )).scalars().all()

    if not payments:
        await query.edit_message_text("لا توجد مدفوعات بعد.", reply_markup=await main_menu_keyboard())
        return

    text = "آخر المدفوعات:\n" + "="*30 + "\n\n"
    for p in payments:
        onum = p.order.order_number if p.order else "?"
        text += f"طلب #{onum} | {p.amount} ر.ي | {p.note or 'بدون ملاحظة'}\n{p.created_at}\n\n"

    await query.edit_message_text(text, reply_markup=await back_main())


async def show_pay_methods(query):
    async with async_session() as db:
        methods = (await db.execute(
            select(PaymentMethod).order_by(PaymentMethod.sort_order)
        )).scalars().all()

    if not methods:
        await query.edit_message_text("لا توجد طرق دفع بعد.", reply_markup=await main_menu_keyboard())
        return

    text = "طرق الدفع:\n" + "="*30 + "\n\n"
    for m in methods:
        active = "مفعّل" if m.is_active else "معطّل"
        text += f"{m.name} | {active}\n{m.details}\n\n"

    await query.edit_message_text(text, reply_markup=await back_main())


async def show_settings(query):
    settings_list = [
        "workshop_name", "workshop_phone", "workshop_location",
        "workshop_description", "workshop_hours",
        "hero_title", "hero_description", "hero_btn_text",
        "footer_text", "copyright_text",
        "about_title", "about_text",
    ]

    async with async_session() as db:
        site_data = {}
        for key in settings_list:
            result = await db.execute(select(SiteSetting).where(SiteSetting.key == key))
            setting = result.scalar_one_or_none()
            if setting:
                try:
                    site_data[key] = json.loads(setting.value)
                except Exception:
                    site_data[key] = setting.value

    text = "الإعدادات الحالية:\n" + "="*30 + "\n\n"
    buttons = []
    for key in settings_list:
        val = site_data.get(key, "")
        if isinstance(val, str) and len(val) > 30:
            val = val[:30] + "..."
        text += f"{key}: {val}\n"
        buttons.append([InlineKeyboardButton(f"تعديل {key}", callback_data=f"edit_setting_{key}")])
    buttons.append([InlineKeyboardButton("القائمة الرئيسية", callback_data="main_menu")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def show_chats(query):
    async with async_session() as db:
        chats = (await db.execute(
            select(Chat).where(Chat.is_active == True).options(
                selectinload(Chat.messages)
            ).order_by(Chat.id.desc()).limit(10)
        )).scalars().all()

    if not chats:
        await query.edit_message_text("لا توجد دردشات نشطة.", reply_markup=await main_menu_keyboard())
        return

    text = "الدردشات النشطة:\n" + "="*30 + "\n\n"
    buttons = []
    for c in chats:
        msg_count = len(c.messages)
        last_msg = c.messages[-1].text[:30] if c.messages else "فارغة"
        text += f"دردشة #{c.id} | {msg_count} رسالة | آخر: {last_msg}\n"
        buttons.append([InlineKeyboardButton(f"دردشة #{c.id}", callback_data=f"chat_{c.id}")])
    buttons.append([InlineKeyboardButton("القائمة الرئيسية", callback_data="main_menu")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def show_chat_detail(query, cid):
    async with async_session() as db:
        result = await db.execute(
            select(Chat).where(Chat.id == cid).options(
                selectinload(Chat.messages).selectinload(Message.sender)
            )
        )
        chat = result.scalar_one_or_none()
        if not chat:
            await query.edit_message_text("الدردشة غير موجودة.", reply_markup=await main_menu_keyboard())
            return

    text = f"الدردشة #{cid}\n{'='*30}\n\n"
    for msg in chat.messages[-15:]:
        sender = msg.sender.name if msg.sender else "غير معروف"
        text += f"[{sender}]: {msg.text}\n"

    buttons = [
        [InlineKeyboardButton("إغلاق الدردشة", callback_data=f"close_chat_{cid}")],
        [InlineKeyboardButton("رجوع", callback_data="chats")],
        [InlineKeyboardButton("القائمة الرئيسية", callback_data="main_menu")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return

    text = update.message.text

    if context.user_data.get("awaiting_setting"):
        key = context.user_data.pop("awaiting_setting")
        async with async_session() as db:
            result = await db.execute(select(SiteSetting).where(SiteSetting.key == key))
            setting = result.scalar_one_or_none()
            if setting:
                setting.value = json.dumps(text, ensure_ascii=False)
            else:
                db.add(SiteSetting(key=key, value=json.dumps(text, ensure_ascii=False)))
            await db.commit()
        await update.message.reply_text(f"تم تحديث الإعداد {key} بنجاح!", reply_markup=await main_menu_keyboard())

    elif context.user_data.get("awaiting_notif"):
        context.user_data.pop("awaiting_notif")
        async with async_session() as db:
            customers = (await db.execute(
                select(User).where(User.role == UserRole.CUSTOMER.value)
            )).scalars().all()
            count = 0
            for c in customers:
                notif = Notification(
                    user_id=c.id,
                    title="إشعار من الإدارة",
                    body=text,
                )
                db.add(notif)
                count += 1
            await db.commit()
        await update.message.reply_text(f"تم إرسال الإشعار لـ {count} عميل.", reply_markup=await main_menu_keyboard())

    elif context.user_data.get("active_chat"):
        cid = context.user_data.pop("active_chat")
        async with async_session() as db:
            result = await db.execute(select(User).where(User.email == settings.ADMIN_EMAIL))
            admin_user = result.scalar_one_or_none()
            if admin_user:
                msg = Message(
                    chat_id=cid,
                    sender_id=admin_user.id,
                    text=text,
                )
                db.add(msg)
                await db.commit()
        await update.message.reply_text("تم إرسال الرسالة.", reply_markup=await main_menu_keyboard())


def setup_bot() -> Application | None:
    if not BOT_TOKEN or not OWNER_ID:
        logger.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_OWNER_ID not set. Bot disabled.")
        return None

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(cb_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    return app


_bot_app: Application | None = None


async def start_bot_polling():
    global _bot_app
    _bot_app = setup_bot()
    if _bot_app:
        logger.info("Starting Telegram bot polling...")
        await _bot_app.initialize()
        await _bot_app.start()
        await _bot_app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot started.")


async def stop_bot_polling():
    global _bot_app
    if _bot_app:
        logger.info("Stopping Telegram bot...")
        await _bot_app.updater.stop()
        await _bot_app.stop()
        await _bot_app.shutdown()
        _bot_app = None
        logger.info("Telegram bot stopped.")
