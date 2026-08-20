import asyncio
import logging
import json

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import async_session
from app.models import (
    User, Order, OrderItem, Product, Category, Payment,
    Notification, Chat, Message, SiteSetting, PaymentMethod,
    Review, UserRole, OrderStatus
)

logger = logging.getLogger("telegram_bot")

BOT_TOKEN = settings.TELEGRAM_BOT_TOKEN
OWNER_ID = settings.TELEGRAM_OWNER_ID

STATUS_LABELS = {
    "new": "🟢 جديد",
    "reviewing": "🔵 قيد المراجعة",
    "accepted": "✅ مقبول",
    "in_progress": "⚙️ قيد التنفيذ",
    "ready": "📦 جاهز",
    "completed": "🎉 مكتمل",
    "cancelled": "❌ ملغي",
}


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


async def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 لوحة التحكم", callback_data="dashboard")],
        [
            InlineKeyboardButton("📋 الطلبات", callback_data="orders"),
            InlineKeyboardButton("📦 المنتجات", callback_data="products"),
        ],
        [
            InlineKeyboardButton("👥 العملاء", callback_data="customers"),
            InlineKeyboardButton("⭐ المراجعات", callback_data="reviews"),
        ],
        [
            InlineKeyboardButton("💰 المدفوعات", callback_data="payments"),
            InlineKeyboardButton("💳 طرق الدفع", callback_data="pay_methods"),
        ],
        [
            InlineKeyboardButton("⚙️ الإعدادات", callback_data="settings"),
            InlineKeyboardButton("🔔 إشعار للعملاء", callback_data="send_notif"),
        ],
        [
            InlineKeyboardButton("💬 الدردشات", callback_data="chats"),
        ],
    ])


async def back_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ القائمة الرئيسية", callback_data="main_menu")]
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    logger.info("Bot /start received from user %s (owner: %s)", uid, OWNER_ID)
    if not is_owner(uid):
        await update.message.reply_text(f"❌ هذا البوت خاص بالمالك فقط.\nID: {uid}")
        return
    await update.message.reply_text(
        "🔧 *لوحة تحكم ورشة أبو ياسر الصرماح*\n\nاختر من القائمة:",
        parse_mode="Markdown",
        reply_markup=await main_menu_kb(),
    )


async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if not is_owner(uid):
        await query.edit_message_text("❌ غير مصرح لك.")
        return

    data = query.data

    if data == "main_menu":
        await query.edit_message_text("🔧 القائمة الرئيسية:", reply_markup=await main_menu_kb())

    elif data == "dashboard":
        await show_dashboard(query)

    elif data == "orders":
        await show_orders(query, 0)

    elif data.startswith("orders_page_"):
        page = int(data.split("_")[2])
        await show_orders(query, page)

    elif data.startswith("order_"):
        oid = int(data.split("_")[1])
        await show_order_detail(query, oid)

    elif data.startswith("status_"):
        parts = data.split("_")
        oid = int(parts[1])
        status = parts[2]
        await change_order_status(query, oid, status, context)

    elif data == "products":
        await show_products(query)

    elif data.startswith("prod_"):
        pid = int(data.split("_")[1])
        await show_product_detail(query, pid)

    elif data == "customers":
        await show_customers(query, 0)

    elif data.startswith("customers_page_"):
        page = int(data.split("_")[2])
        await show_customers(query, page)

    elif data.startswith("cust_"):
        cid = int(data.split("_")[1])
        await show_customer_detail(query, cid)

    elif data.startswith("remind_"):
        cid = int(data.split("_")[1])
        await send_reminder(query, cid)

    elif data.startswith("toggle_ban_"):
        cid = int(data.split("_")[1])
        await toggle_ban(query, cid)

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
        await query.edit_message_text(
            f"✏️ أرسل القيمة الجديدة للإعداد:\n\n*{key}*",
            parse_mode="Markdown",
            reply_markup=await back_kb(),
        )

    elif data == "send_notif":
        context.user_data["awaiting_notif"] = True
        await query.edit_message_text(
            "📝 أرسل نص الإشعار الذي تريد إرساله لجميع العملاء:",
            reply_markup=await back_kb(),
        )

    elif data == "chats":
        await show_chats(query)

    elif data.startswith("chat_"):
        cid = int(data.split("_")[1])
        context.user_data["active_chat"] = cid
        await show_chat_detail(query, cid)

    elif data.startswith("close_chat_"):
        cid = int(data.split("_")[1])
        await close_chat(query, cid)


async def show_dashboard(query):
    async with async_session() as db:
        total_orders = (await db.execute(select(func.count(Order.id)))).scalar() or 0
        total_products = (await db.execute(select(func.count(Product.id)))).scalar() or 0
        total_customers = (await db.execute(
            select(func.count(User.id)).where(User.role == UserRole.CUSTOMER.value)
        )).scalar() or 0
        total_revenue = (await db.execute(
            select(func.coalesce(func.sum(Order.paid_amount), 0))
        )).scalar() or 0
        new_orders = (await db.execute(
            select(func.count(Order.id)).where(Order.status == OrderStatus.NEW.value)
        )).scalar() or 0
        pending_orders = (await db.execute(
            select(func.count(Order.id)).where(
                Order.status.in_(["reviewing", "accepted", "in_progress"])
            )
        )).scalar() or 0
        active_chats = (await db.execute(
            select(func.count(Chat.id)).where(Chat.is_active == True)
        )).scalar() or 0
        total_reviews = (await db.execute(select(func.count(Review.id)))).scalar() or 0

    text = (
        f"📊 *لوحة التحكم*\n"
        f"{'━' * 25}\n\n"
        f"📋 الطلبات: *{total_orders}*\n"
        f"   🟢 جديدة: *{new_orders}*\n"
        f"   ⚙️ قيد التنفيذ: *{pending_orders}*\n\n"
        f"📦 المنتجات: *{total_products}*\n"
        f"👥 العملاء: *{total_customers}*\n"
        f"💰 الإيرادات: *{total_revenue} ر.ي*\n"
        f"⭐ التقييمات: *{total_reviews}*\n"
        f"💬 الدردشات: *{active_chats}*\n"
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=await main_menu_kb())


async def show_orders(query, page):
    async with async_session() as db:
        total = (await db.execute(select(func.count(Order.id)))).scalar() or 0
        orders = (await db.execute(
            select(Order).options(selectinload(Order.user))
            .order_by(Order.id.desc()).offset(page * 8).limit(8)
        )).scalars().all()

    if not orders:
        await query.edit_message_text("📭 لا توجد طلبات بعد.", reply_markup=await main_menu_kb())
        return

    pages = (total + 7) // 8
    text = f"📋 *الطلبات* (صفحة {page+1}/{pages})\n{'━' * 25}\n\n"
    buttons = []
    for o in orders:
        status = STATUS_LABELS.get(o.status, o.status)
        uname = o.user.name if o.user else "غير معروف"
        text += f"*#{o.order_number}* | {status}\n💰 {o.total} ر.ي | 👤 {uname}\n\n"
        buttons.append([
            InlineKeyboardButton(
                f"#{o.order_number} - {STATUS_LABELS.get(o.status, o.status)}",
                callback_data=f"order_{o.id}"
            )
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ السابق", callback_data=f"orders_page_{page-1}"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton("▶️ التالي", callback_data=f"orders_page_{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("◀️ القائمة الرئيسية", callback_data="main_menu")])

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def show_order_detail(query, oid):
    async with async_session() as db:
        result = await db.execute(
            select(Order).where(Order.id == oid).options(
                selectinload(Order.user),
                selectinload(Order.items).selectinload(OrderItem.product),
                selectinload(Order.payments),
            )
        )
        order = result.scalar_one_or_none()
        if not order:
            await query.edit_message_text("❌ الطلب غير موجود.", reply_markup=await main_menu_kb())
            return

        items_text = ""
        for item in order.items:
            pname = item.product.name if item.product else "محذوف"
            items_text += f"  • {pname} × {item.quantity} = {item.price * item.quantity} ر.ي\n"

        payments_text = ""
        for p in order.payments:
            payments_text += f"  • {p.amount} ر.ي — {p.note or 'بدون ملاحظة'}\n"

    status = STATUS_LABELS.get(order.status, order.status)
    text = (
        f"📋 *تفاصيل الطلب #{order.order_number}*\n"
        f"{'━' * 25}\n\n"
        f"👤 العميل: *{order.user.name if order.user else 'غير معروف'}*\n"
        f"📱 الوتساب: `{order.whatsapp}`\n"
        f"📌 الحالة: {status}\n"
        f"💰 الإجمالي: *{order.total} ر.ي*\n"
        f"✅ المدفوع: *{order.paid_amount} ر.ي*\n"
        f"❌ المتبقي: *{order.remaining} ر.ي*\n"
        f"📅 التاريخ: {order.created_at}\n"
        f"📝 ملاحظات: {order.notes or 'لا توجد'}\n\n"
    )
    if items_text:
        text += f"📦 *الأصناف:*\n{items_text}\n"
    if payments_text:
        text += f"💳 *المدفوعات:*\n{payments_text}\n"

    buttons = []
    statuses = [
        ("new", "🟢 جديد"), ("reviewing", "🔵 مراجعة"),
        ("accepted", "✅ قبول"), ("in_progress", "⚙️ تنفيذ"),
        ("ready", "📦 جاهز"), ("completed", "🎉 تم"),
        ("cancelled", "❌ إلغاء"),
    ]
    row = []
    for s_code, s_label in statuses:
        row.append(InlineKeyboardButton(s_label, callback_data=f"status_{oid}_{s_code}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("◀️ رجوع للطلبات", callback_data="orders")])
    buttons.append([InlineKeyboardButton("◀️ القائمة الرئيسية", callback_data="main_menu")])

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def change_order_status(query, oid, status, context):
    async with async_session() as db:
        result = await db.execute(select(Order).where(Order.id == oid).options(selectinload(Order.user)))
        order = result.scalar_one_or_none()
        if not order:
            await query.edit_message_text("❌ الطلب غير موجود.", reply_markup=await main_menu_kb())
            return
        order.status = status
        if order.user_id:
            db.add(Notification(
                user_id=order.user_id,
                title="تحديث حالة الطلب",
                body=f"تم تحديث حالة طلبك #{order.order_number} إلى: {STATUS_LABELS.get(status, status)}",
                link=f"/customer/order/{order.id}",
            ))
        await db.commit()

    await query.edit_message_text(
        f"✅ تم تحديث حالة الطلب #{order.order_number}\n\n"
        f"{STATUS_LABELS.get(status, status)}",
        reply_markup=await back_kb(),
    )


async def show_products(query):
    async with async_session() as db:
        products = (await db.execute(
            select(Product).options(selectinload(Product.category))
            .order_by(Product.id.desc()).limit(20)
        )).scalars().all()

    if not products:
        await query.edit_message_text("📭 لا توجد منتجات بعد.", reply_markup=await main_menu_kb())
        return

    text = "📦 *المنتجات*\n" + "━" * 25 + "\n\n"
    buttons = []
    for p in products:
        cat = p.category.name if p.category else "بدون فئة"
        avail = "✅" if p.is_available else "❌"
        text += f"{avail} *{p.name}* | {p.price} ر.ي | {cat}\n"
        buttons.append([InlineKeyboardButton(f"📦 {p.name}", callback_data=f"prod_{p.id}")])
    buttons.append([InlineKeyboardButton("◀️ القائمة الرئيسية", callback_data="main_menu")])

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def show_product_detail(query, pid):
    async with async_session() as db:
        result = await db.execute(
            select(Product).where(Product.id == pid).options(selectinload(Product.category))
        )
        product = result.scalar_one_or_none()
        if not product:
            await query.edit_message_text("❌ المنتج غير موجود.", reply_markup=await main_menu_kb())
            return

        review_count = (await db.execute(
            select(func.count(Review.id)).where(Review.product_id == pid)
        )).scalar() or 0
        avg_rating = (await db.execute(
            select(func.coalesce(func.avg(Review.rating), 0)).where(Review.product_id == pid)
        )).scalar() or 0

    cat = product.category.name if product.category else "بدون فئة"
    avail = "✅ متوفر" if product.is_available else "❌ غير متوفر"
    stars = "⭐" * round(float(avg_rating))
    text = (
        f"📦 *{product.name}*\n"
        f"{'━' * 25}\n\n"
        f"💰 السعر: *{product.price} ر.ي*\n"
        f"📂 الفئة: {cat}\n"
        f"📌 الحالة: {avail}\n"
        f"📊 المخزون: {product.stock}\n"
        f"⭐ التقييم: {stars} ({avg_rating}/5 — {review_count} تقييم)\n\n"
        f"📝 *الوصف:*\n{product.description or 'لا يوجد وصف'}\n"
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=await back_kb())


async def show_customers(query, page):
    async with async_session() as db:
        total = (await db.execute(
            select(func.count(User.id)).where(User.role == UserRole.CUSTOMER.value)
        )).scalar() or 0
        customers = (await db.execute(
            select(User).where(User.role == UserRole.CUSTOMER.value)
            .options(selectinload(User.orders))
            .order_by(User.id.desc()).offset(page * 8).limit(8)
        )).scalars().all()

    if not customers:
        await query.edit_message_text("📭 لا يوجد عملاء بعد.", reply_markup=await main_menu_kb())
        return

    pages = (total + 7) // 8
    text = f"👥 *العملاء* (صفحة {page+1}/{pages})\n" + "━" * 25 + "\n\n"
    buttons = []
    for c in customers:
        order_count = len(c.orders)
        total_paid = sum(o.paid_amount for o in c.orders)
        ban = "🚫" if c.is_banned else ""
        text += f"{ban} *{c.name}* | {c.phone}\n   📋 {order_count} طلبات | 💰 {total_paid} ر.ي\n\n"
        buttons.append([
            InlineKeyboardButton(f"👤 {c.name}", callback_data=f"cust_{c.id}")
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ السابق", callback_data=f"customers_page_{page-1}"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton("▶️ التالي", callback_data=f"customers_page_{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("◀️ القائمة الرئيسية", callback_data="main_menu")])

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def show_customer_detail(query, cid):
    async with async_session() as db:
        result = await db.execute(
            select(User).where(User.id == cid).options(selectinload(User.orders))
        )
        customer = result.scalar_one_or_none()
        if not customer:
            await query.edit_message_text("❌ العميل غير موجود.", reply_markup=await main_menu_kb())
            return

    total_paid = sum(o.paid_amount for o in customer.orders)
    total_remaining = sum(o.remaining for o in customer.orders)
    ban = "🚫 محظور" if customer.is_banned else "✅ نشط"
    text = (
        f"👤 *{customer.name}*\n"
        f"{'━' * 25}\n\n"
        f"📱 الهاتف: `{customer.phone}`\n"
        f"📧 البريد: `{customer.email}`\n"
        f"📋 الطلبات: *{len(customer.orders)}*\n"
        f"💰 المدفوع: *{total_paid} ر.ي*\n"
        f"❌ المتبقي: *{total_remaining} ر.ي*\n"
        f"📌 الحالة: {ban}\n"
        f"📅 التسجيل: {customer.created_at}\n"
    )
    buttons = [
        [InlineKeyboardButton("🔔 تذكير بالدفع", callback_data=f"remind_{cid}")],
        [InlineKeyboardButton(
            "🚫 حظر" if not customer.is_banned else "✅ إلغاء الحظر",
            callback_data=f"toggle_ban_{cid}"
        )],
        [InlineKeyboardButton("◀️ رجوع للعملاء", callback_data="customers")],
        [InlineKeyboardButton("◀️ القائمة الرئيسية", callback_data="main_menu")],
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def send_reminder(query, cid):
    async with async_session() as db:
        result = await db.execute(select(User).where(User.id == cid))
        customer = result.scalar_one_or_none()
        if customer:
            db.add(Notification(
                user_id=customer.id,
                title="تذكير بالدفع",
                body="يرجى سداد المبلغ المتبقي على طلبك. تواصل معنا لمزيد من التفاصيل.",
            ))
            await db.commit()
            await query.edit_message_text(
                f"🔔 تم إرسال تذكير لـ *{customer.name}*",
                parse_mode="Markdown",
                reply_markup=await back_kb(),
            )
        else:
            await query.edit_message_text("❌ العميل غير موجود.", reply_markup=await main_menu_kb())


async def toggle_ban(query, cid):
    async with async_session() as db:
        result = await db.execute(select(User).where(User.id == cid))
        customer = result.scalar_one_or_none()
        if customer:
            customer.is_banned = not customer.is_banned
            await db.commit()
            action = "🚫 تم حظر" if customer.is_banned else "✅ تم إلغاء الحظر"
            await query.edit_message_text(
                f"{action}: *{customer.name}*",
                parse_mode="Markdown",
                reply_markup=await back_kb(),
            )
        else:
            await query.edit_message_text("❌ العميل غير موجود.", reply_markup=await main_menu_kb())


async def show_reviews(query):
    async with async_session() as db:
        reviews = (await db.execute(
            select(Review).options(
                selectinload(Review.user), selectinload(Review.product)
            ).order_by(Review.id.desc()).limit(10)
        )).scalars().all()

    if not reviews:
        await query.edit_message_text("📭 لا توجد تقييمات بعد.", reply_markup=await main_menu_kb())
        return

    text = "⭐ *آخر التقييمات*\n" + "━" * 25 + "\n\n"
    for r in reviews:
        stars = "⭐" * r.rating + "☆" * (5 - r.rating)
        user = r.user.name if r.user else "مجهول"
        product = r.product.name if r.product else "محذوف"
        text += f"{stars}\n👤 {user} | 📦 {product}\n💬 {r.comment}\n\n"

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=await back_kb())


async def show_payments(query):
    async with async_session() as db:
        payments = (await db.execute(
            select(Payment).options(selectinload(Payment.order))
            .order_by(Payment.id.desc()).limit(10)
        )).scalars().all()

    if not payments:
        await query.edit_message_text("📭 لا توجد مدفوعات بعد.", reply_markup=await main_menu_kb())
        return

    text = "💰 *آخر المدفوعات*\n" + "━" * 25 + "\n\n"
    for p in payments:
        onum = p.order.order_number if p.order else "?"
        text += f"📋 طلب #{onum} | 💰 *{p.amount} ر.ي*\n📝 {p.note or 'بدون ملاحظة'}\n📅 {p.created_at}\n\n"

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=await back_kb())


async def show_pay_methods(query):
    async with async_session() as db:
        methods = (await db.execute(
            select(PaymentMethod).order_by(PaymentMethod.sort_order)
        )).scalars().all()

    if not methods:
        await query.edit_message_text("📭 لا توجد طرق دفع بعد.", reply_markup=await main_menu_kb())
        return

    text = "💳 *طرق الدفع*\n" + "━" * 25 + "\n\n"
    for m in methods:
        active = "✅ مفعّل" if m.is_active else "❌ معطّل"
        text += f"*{m.name}* | {active}\n📝 {m.details}\n\n"

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=await back_kb())


async def show_settings(query):
    keys = [
        ("workshop_name", "اسم الورشة"), ("workshop_phone", "هاتف الورشة"),
        ("workshop_location", "الموقع"), ("workshop_hours", "ساعات العمل"),
        ("hero_title", "عنوان البانر"), ("hero_description", "وصف البانر"),
        ("footer_text", "نص الفوتر"), ("copyright_text", "حقوق النشر"),
        ("about_title", "عنوان من نحن"), ("about_text", "نص من نحن"),
        ("facebook", "فيسبوك"), ("twitter", "تويتر"),
        ("instagram", "إنستغرام"), ("tiktok", "تيك توك"),
    ]

    async with async_session() as db:
        site_data = {}
        for key, _ in keys:
            result = await db.execute(select(SiteSetting).where(SiteSetting.key == key))
            setting = result.scalar_one_or_none()
            if setting:
                try:
                    site_data[key] = json.loads(setting.value)
                except Exception:
                    site_data[key] = setting.value

    text = "⚙️ *الإعدادات*\n" + "━" * 25 + "\n\n"
    buttons = []
    for key, label in keys:
        val = site_data.get(key, "")
        if isinstance(val, str) and len(val) > 25:
            val = val[:25] + "..."
        text += f"*{label}*: {val or 'فارغ'}\n"
        buttons.append([InlineKeyboardButton(f"✏️ {label}", callback_data=f"edit_setting_{key}")])
    buttons.append([InlineKeyboardButton("◀️ القائمة الرئيسية", callback_data="main_menu")])

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def show_chats(query):
    async with async_session() as db:
        chats = (await db.execute(
            select(Chat).where(Chat.is_active == True)
            .options(selectinload(Chat.messages), selectinload(Chat.user))
            .order_by(Chat.id.desc()).limit(10)
        )).scalars().all()

    if not chats:
        await query.edit_message_text("📭 لا توجد دردشات نشطة.", reply_markup=await main_menu_kb())
        return

    text = "💬 *الدردشات النشطة*\n" + "━" * 25 + "\n\n"
    buttons = []
    for c in chats:
        msg_count = len(c.messages)
        last_msg = c.messages[-1].text[:25] if c.messages else "فارغة"
        uname = c.user.name if c.user else "غير معروف"
        text += f"💬 #{c.id} | 👤 {uname} | {msg_count} رسالة\n   آخر: {last_msg}\n\n"
        buttons.append([InlineKeyboardButton(f"💬 #{c.id} — {uname}", callback_data=f"chat_{c.id}")])
    buttons.append([InlineKeyboardButton("◀️ القائمة الرئيسية", callback_data="main_menu")])

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def show_chat_detail(query, cid):
    async with async_session() as db:
        result = await db.execute(
            select(Chat).where(Chat.id == cid).options(
                selectinload(Chat.messages).selectinload(Message.sender),
                selectinload(Chat.user),
            )
        )
        chat = result.scalar_one_or_none()
        if not chat:
            await query.edit_message_text("❌ الدردشة غير موجودة.", reply_markup=await main_menu_kb())
            return

    uname = chat.user.name if chat.user else "غير معروف"
    text = f"💬 *دردشة #{cid}* — {uname}\n" + "━" * 25 + "\n\n"
    for msg in chat.messages[-15:]:
        sender = msg.sender.name if msg.sender else "?"
        icon = "👤" if msg.sender_id != chat.user_id else "🧑‍💼"
        text += f"{icon} [{sender}]: {msg.text}\n"

    buttons = [
        [InlineKeyboardButton("🔴 إغلاق الدردشة", callback_data=f"close_chat_{cid}")],
        [InlineKeyboardButton("◀️ رجوع", callback_data="chats")],
        [InlineKeyboardButton("◀️ القائمة الرئيسية", callback_data="main_menu")],
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def close_chat(query, cid):
    async with async_session() as db:
        result = await db.execute(select(Chat).where(Chat.id == cid))
        chat = result.scalar_one_or_none()
        if chat:
            chat.is_active = False
            await db.commit()
            await query.edit_message_text("🔴 تم إغلاق الدردشة.", reply_markup=await back_kb())
        else:
            await query.edit_message_text("❌ الدردشة غير موجودة.", reply_markup=await main_menu_kb())


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
        await update.message.reply_text(
            f"✅ تم تحديث الإعداد *{key}* بنجاح!",
            parse_mode="Markdown",
            reply_markup=await main_menu_kb(),
        )

    elif context.user_data.get("awaiting_notif"):
        context.user_data.pop("awaiting_notif")
        async with async_session() as db:
            customers = (await db.execute(
                select(User).where(User.role == UserRole.CUSTOMER.value)
            )).scalars().all()
            count = 0
            for c in customers:
                db.add(Notification(
                    user_id=c.id,
                    title="إشعار من الورشة",
                    body=text,
                ))
                count += 1
            await db.commit()
        await update.message.reply_text(
            f"✅ تم إرسال الإشعار لـ *{count}* عميل.",
            parse_mode="Markdown",
            reply_markup=await main_menu_kb(),
        )

    elif context.user_data.get("active_chat"):
        cid = context.user_data.pop("active_chat")
        async with async_session() as db:
            result = await db.execute(
                select(User).where(User.email == settings.ADMIN_EMAIL)
            )
            admin_user = result.scalar_one_or_none()
            if admin_user:
                db.add(Message(chat_id=cid, sender_id=admin_user.id, text=text))
                await db.commit()
        await update.message.reply_text("✅ تم إرسال الرسالة.", reply_markup=await main_menu_kb())


_app_ref = None


async def start_bot_polling():
    global _app_ref
    if not BOT_TOKEN or not OWNER_ID:
        logger.warning("Telegram bot disabled: missing BOT_TOKEN or OWNER_ID")
        return

    logger.info("Starting Telegram bot...")
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(cb_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    _app_ref = app

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot polling started! Owner ID: %s", OWNER_ID)

    me = await app.bot.get_me()
    logger.info("Bot username: @%s", me.username)

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass


async def stop_bot_polling():
    global _app_ref
    if _app_ref:
        logger.info("Stopping Telegram bot...")
        try:
            await _app_ref.updater.stop()
            await _app_ref.stop()
            await _app_ref.shutdown()
        except Exception as e:
            logger.error("Error stopping bot: %s", e)
        _app_ref = None
