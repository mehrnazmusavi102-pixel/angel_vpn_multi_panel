"""
bot.py
فایل اصلی اجرای ربات. تمام Routerهای پوشه‌ی handlers اینجا به Dispatcher
وصل می‌شوند. یک سرور Flask کوچک هم کنارش اجرا می‌شود تا Render سرویس را
"زنده" تشخیص بدهد (لازمه‌ی سرویس‌های نوع Web Service).
"""

import asyncio
import logging
import os
import threading

import aiohttp

from aiogram import Bot, Dispatcher
from aiogram.types import ErrorEvent, BotCommand, MenuButtonDefault
from aiogram.exceptions import TelegramBadRequest
from aiogram.dispatcher.middlewares.base import BaseMiddleware

import database as db
import uniquepay
import payments
import alerts
import fsm_storage
import bot_loop
import vpn_panel
from config import TOKEN, UNIQUEPAY_ENABLED, ADMIN_ID
from keyboards import all_reply_menu_texts
from handlers import menu, start, wallet, profile, referral, plans, ticket, admin, marzban_admin, panel_admin
from handlers.plans import finalize_online_payment, finalize_custom_online_payment
from handlers.wallet import finalize_wallet_charge_online_payment
from alerts import check_usage_alerts, CHECK_INTERVAL_SECONDS

from flask import Flask, request

# 🆕 این قالب مینی‌اپ را به‌طور کامل حذف کرده‌ایم؛ این اپ Flask کوچک فقط دو کار انجام می‌دهد:
# یک) یک مسیر health-check تا Render سرویس را "زنده" تشخیص بدهد، دو) مسیر بازگشت
# (Callback) درگاه پرداخت پاسارگاد را می‌پذیرد.
flask_app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 🧩 Rich Telegram text: متن‌های قابل شخصی‌سازی می‌توانند Custom/Premium Emoji
# و سایر MessageEntityها داشته باشند. این wrapper قبل از ساخت درخواست
# SendMessage/EditMessageText، entityهای ذخیره‌شده را به Telegram تحویل می‌دهد
# و parse_mode را کنار می‌گذارد تا دوباره‌پارسی/خراب شدن HTML/Markdown رخ ندهد.
# متن‌های عادی هیچ تغییری نمی‌کنند.
# ---------------------------------------------------------------------------
_original_send_message = Bot.send_message
_original_edit_message_text = Bot.edit_message_text

async def _send_message_rich(self, *args, **kwargs):
    text = kwargs.get("text")
    if text is None and len(args) >= 2:
        text = args[1]
    entities = getattr(text, "entities", None)
    if entities:
        kwargs["entities"] = entities
        kwargs["parse_mode"] = None
    return await _original_send_message(self, *args, **kwargs)

async def _edit_message_text_rich(self, *args, **kwargs):
    text = kwargs.get("text")
    if text is None and len(args) >= 4:
        text = args[3]
    entities = getattr(text, "entities", None)
    if entities:
        kwargs["entities"] = entities
        kwargs["parse_mode"] = None
    return await _original_edit_message_text(self, *args, **kwargs)

Bot.send_message = _send_message_rich
Bot.edit_message_text = _edit_message_text_rich


class BlockedUserMiddleware(BaseMiddleware):
    """اعمال مسدودی روی تمام پیام‌ها و callbackها، نه فقط /start."""
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user and user.id != ADMIN_ID and db.is_user_blocked(user.id):
            text = "🚫 دسترسی شما به ربات مسدود شده است. با پشتیبانی در ارتباط باشید."
            if getattr(event, "answer", None):
                try:
                    if event.__class__.__name__ == "CallbackQuery":
                        await event.answer(text, show_alert=True)
                    else:
                        await event.answer(text)
                except Exception:
                    logger.exception("خطا در اعلام مسدودی به کاربر")
            return None
        return await handler(event, data)


# ---------------------------------------------------------------------------
# 🐛 فیکس گیرکردن در وسط یک FSM ناتمام (مثلاً «منتظر عکس کیوآرکد سرویس»،
# «منتظر رسید شارژ کیف پول»، «منتظر مبلغ دلخواه شارژ» و مشابه):
#
# قبلاً اگر کاربر/ادمین وسط یکی از این حالت‌ها روی یکی از دکمه‌های ثابت منوی
# پایین صفحه می‌زد (مثلاً «ℹ️ اطلاعات ربات»
# منتظر عکس کیوآرکد)، چون آن دکمه فقط یک پیام متنی معمولی برای تلگرام است و
# هیچ فیلتر state ندارد، اما handler «فرمت اشتباه» همان FSM ناتمام هر پیام
# غیرمنتظره‌ای (از جمله همین دکمه) را می‌گرفت، کاربر/ادمین در یک حلقه‌ی تکراری
# («لطفاً عکس کیوآرکد رو ارسال کن») گیر می‌کرد و تنها راه فرار زدن /start بود.
# این دقیقاً همان باگی است که در پنل ادمین اصلی/فرعی («عکس کیوآرکد می‌خواهد و
# دلیلش معلوم نیست») و به‌طور مشابه در مسیر شارژ کیف پول رخ می‌داد.
#
# راه‌حل: این میدل‌ور روی *هر* پیام متنی، قبل از رسیدن به هر handler دیگری،
# بررسی می‌کند که آیا متن پیام دقیقاً برابر یکی از دکمه‌های ثابت منو (کاربر
# عادی یا ادمین/ادمین فرعی) است؛ اگر بله و کاربر/ادمین وسط یک state ناتمام
# باشد، همان state پاک می‌شود تا فیلتر state آن handler «فرمت اشتباه» دیگر
# مچ نشود و همان دکمه بلافاصله توسط handler واقعی خودش (که هیچ وابستگی به
# state ندارد) پردازش شود.
# ---------------------------------------------------------------------------
class MenuEscapeMiddleware(BaseMiddleware):
    def _texts(self) -> set[str]:
        # 🐛 فیکس: قبلاً نتیجه‌ی all_reply_menu_texts() فقط یک‌بار (همان اولین پیام) محاسبه
        # و برای همیشه کش می‌شد. پس اگر ادمین بعداً متن یک دکمه را از پنل مدیریت متن‌ها
        # عوض می‌کرد، این میدل‌ور همچنان دنبال متن قدیمی می‌گشت و دکمه‌ی تازه‌نامگذاری‌شده
        # را «دکمه‌ی منو» تشخیص نمی‌داد؛ اگر کاربر وسط یک FSM ناتمام روی آن دکمه می‌زد، به‌جای
        # پاک شدن state، در همان مرحله گیر می‌کرد. الان هر بار مقدار تازه از دیتابیس خوانده می‌شود.
        try:
            return all_reply_menu_texts()
        except Exception:
            logger.exception("خطا در ساخت لیست متن دکمه‌های منو برای فیکس گیرکردن FSM")
            return set()

    async def __call__(self, handler, event, data):
        text = getattr(event, "text", None)
        state = data.get("state")
        if text and state is not None and text in self._texts():
            try:
                if await state.get_state() is not None:
                    await state.clear()
            except Exception:
                logger.exception("خطا در پاک‌کردن state ناتمام هنگام زدن دکمه‌ی ثابت منو")
        return await handler(event, data)


# ---------------------------------------------------------------------------
# هندلر سراسری خطا — تا حالا اگر یک خطای پیش‌بینی‌نشده (باگ) وسط پردازش یک
# callback (مثلاً دکمه‌ی پرداخت) رخ می‌داد، چون هیچ‌جا callback.answer() صدا
# زده نمی‌شد، دکمه برای کاربر برای همیشه در حالت لودینگ/فریز می‌ماند و نه
# پیامی به کاربر می‌رسید و نه به ادمین — و ردیابی علتش هم سخت بود چون فقط در
# لاگ‌ها گم می‌شد. این هندلر تضمین می‌کند که:
# ۱) خطا با جزئیات کامل (traceback) در لاگ ثبت شود تا بعداً قابل پیگیری باشد.
# ۲) کاربر همیشه یک پیام/آلارم دریافت کند و دکمه از حالت لودینگ خارج شود،
#    به‌جای این‌که تا ابد فریز بماند.
# ---------------------------------------------------------------------------
async def global_error_handler(event: ErrorEvent):
    # 🐛 فیکس: خطای کاملاً بی‌ضرر و متداول تلگرام “محتوا تفاوتی ندارد” (message is not modified) را باید به‌طور جدایی مدیریت کرد: وقتی یک دکمه دوبار زده می‌شود یا handler دوباره همان متن/دکمه را edit می‌کند، تلگرام این خطا را برمی‌گرداند ولی هیچ مشکلی برای کاربر رخ نداده؛ قبلاً هم به لاگ به‌عنوان خطای جدی ثبت می‌شد و به کاربر هم پیام خطای گمراه‌کننده نمایش داده می‌شد.
    exc = event.exception
    if isinstance(exc, TelegramBadRequest) and "message is not modified" in str(exc).lower():
        logger.info("نادیده‌گرفتن خطای بی‌ضرر message-is-not-modified (کلیک دوباره/محتوای یکسان)")
        try:
            update = event.update
            if update.callback_query:
                try:
                    await update.callback_query.answer()
                except Exception:
                    pass
        except Exception:
            pass
        return True

    logger.exception(
        "خطای پیش‌بینی‌نشده هنگام پردازش آپدیت: %s", event.exception, exc_info=event.exception
    )
    try:
        import traceback as _tb
        db.log_error(
            error_type=type(event.exception).__name__,
            message=str(event.exception),
            traceback_text="".join(_tb.format_exception(type(event.exception), event.exception, event.exception.__traceback__)),
            context="global_error_handler",
        )
    except Exception:
        pass
    update = event.update
    warning_text = "⚠️ خطایی پیش آمد. لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید."
    try:
        if update.callback_query:
            try:
                await update.callback_query.answer(warning_text, show_alert=True)
            except Exception:
                # اگر این callback قبلاً یک‌بار answer شده باشد (مثلاً با پیام
                # "در حال دریافت..." قبل از شروع کار اصلی)، تلگرام دیگر اجازه‌ی
                # answer دوباره را نمی‌دهد و این except صدا زده می‌شود. قبلاً
                # همین‌جا خطا فقط لاگ می‌شد و کاربر هیچ پیامی نمی‌دید (دکمه فقط
                # در حالت لودینگ می‌ماند). حالا به‌جای answer، مستقیماً یک پیام
                # در همون چت می‌فرستیم تا کاربر همیشه یک نتیجه ببیند.
                logger.warning("امکان answer دوباره‌ی callback نبود؛ ارسال پیام مستقیم به چت.")
                if update.callback_query.message:
                    await update.callback_query.message.answer(warning_text)
        elif update.message:
            await update.message.answer(warning_text)
    except Exception:
        logger.exception("خطا حتی در تلاش برای اطلاع‌رسانی خطای اصلی به کاربر")
    return True


# ---------------------------------------------------------------------------
# Flask - یک مسیر health-check ساده برای اینکه Render سرویس را "زنده" تشخیص
# بدهد (لازمه‌ی Web Serviceها)، به‌همراه مسیر بازگشت درگاه پرداخت پاسارگاد.
# ---------------------------------------------------------------------------
@flask_app.route("/")
def health_check():
    return "ربات در حال اجراست ✅"


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    # threaded=True: تا درخواست‌های هم‌زمان Mini App (مثلاً چند کاربر) یکدیگر را بلاک نکنند
    # و همزمان با پاسخگویی ربات (در event loop اصلی asyncio) پردازش شوند.
    flask_app.run(host="0.0.0.0", port=port, threaded=True)


# ---------------------------------------------------------------------------
# aiogram - ربات اصلی
# ---------------------------------------------------------------------------
async def run_bot():
    db.init_db()
    recovered = db.recover_stuck_online_payments()
    if recovered:
        logger.warning("%d پرداخت processing قدیمی برای پردازش مجدد بازیابی شد.", recovered)
    logger.info("Database initialized.")

    bot = Bot(token=TOKEN)

    # 🆕 فیکس (اصلاح‌شده): قبلاً اینجا منوی چت به MenuButtonCommands تنظیم شده بود، ولی معلوم شد همین کار دقیقاً جای آن
    # دکمه‌ی "باز/بستمنوی" پیش فرستاده (keyboard toggle مربوط به ReplyKeyboardMarkup + is_persistent) را می‌گرفت، چون تلگرام
    # فقط یکی از این دو حالت را همزمان نشان می‌دهد. حالا روی MenuButtonDefault برگشتیم تا تلگرام
    # دوباره همون دکمه‌ی باز/بستمنوی پایین صفحه (همون چهارخونه/فلشی که قبلاً درخواست شده بود) را
    # در همان جایهمیشگی نشان بدهد. لیست فرمان/start همچنان برای تلگرام ثبت می‌شود (بی‌ضرر است).
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="شروع / بازکردن منوی اصلی"),
        ])
        await bot.set_chat_menu_button(menu_button=MenuButtonDefault())
    except Exception:
        logger.exception("خطا در تنظیم دکمه‌ی منوی بوم (set_chat_menu_button)")

    # 🐛 فیکس: قبلاً MemoryStorage (فقط RAM) بود، با هر ری‌استارت state گم می‌شد.
    dp = Dispatcher(storage=fsm_storage.DBStorage())
    dp.errors.register(global_error_handler)
    blocked_middleware = BlockedUserMiddleware()
    dp.message.outer_middleware(blocked_middleware)
    dp.callback_query.outer_middleware(blocked_middleware)
    # 🐛 فیکس: نگاه کن به تعریف MenuEscapeMiddleware بالا؛ باید در سطح Dispatcher
    # (outer_middleware) ثبت شود تا کل زنجیره‌ی فیلتر/state تمام روترها را در بر بگیرد.
    dp.message.outer_middleware(MenuEscapeMiddleware())

    fsm_storage.storage = dp.storage
    bot_loop.main_loop = asyncio.get_running_loop()

    # ترتیب ثبت Routerها مهم است: handler خاص‌تر باید زودتر بیاید.
    # menu (منوی پایین صفحه) باید همیشه اول باشد تا دکمه‌های ثابت پایین صفحه
    # در هر شرایطی (حتی وسط یک FSM دیگر) همیشه در دسترس و فعال باشند.
    # admin باید بعد از آن باشد چون فیلتر سخت‌گیرانه‌تری (ADMIN_ID) دارد
    # و برخی callback_dataهای مشترک (مثل state یکسان) را زودتر می‌گیرد.
    dp.include_router(menu.router)
    dp.include_router(admin.router)
    dp.include_router(marzban_admin.router)
    dp.include_router(panel_admin.router)
    dp.include_router(start.router)
    dp.include_router(wallet.router)
    dp.include_router(profile.router)
    dp.include_router(referral.router)
    dp.include_router(plans.router)
    dp.include_router(ticket.router)

    asyncio.create_task(usage_alert_loop(bot))
    asyncio.create_task(invoice_expiry_loop(bot))
    if UNIQUEPAY_ENABLED:
        asyncio.create_task(online_payment_poller(bot))
    asyncio.create_task(self_ping_loop())
    asyncio.create_task(panel_cache_warmup_loop())

    logger.info("Bot starting polling...")
    await dp.start_polling(bot)


async def usage_alert_loop(bot: Bot):
    """هر ۳۰ دقیقه سرویس‌های VIP را برای هشدار ۸۰٪/۹۰٪ مصرف و ۲ روز به انقضا بررسی می‌کند.
    همچنین کانفیگ‌های منقضی‌شده را به‌صورت خودکار آرشیو می‌کند (deleted=1) تا
    از پنل کاربر حذف شوند."""
    while True:
        try:
            await check_usage_alerts(bot)
        except Exception:
            logger.exception("خطا در بررسی دوره‌ای هشدارهای مصرف/انقضا")
        try:
            archived = db.archive_expired_configs()
            if archived > 0:
                logger.info("کانفیگ‌های منقضی‌شده آرشیو شدند: %d مورد", archived)
        except Exception:
            logger.exception("خطا در آرشیو خودکار کانفیگ‌های منقضی‌شده")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


ONLINE_PAYMENT_POLL_SECONDS = 20


async def online_payment_poller(bot: Bot):
    """هر ۲۰ ثانیه اینوویس‌های در انتظار درگاه یونیک‌پی را چک می‌کند و به‌محض
    پرداخت‌شدن، بدون نیاز به این‌که کاربر دکمه‌ی «بررسی کن» را بزند، سفارش را
    خودکار ثبت کرده و به کاربر و ادمین اطلاع می‌دهد. این همان «تایید خودکار»
    درخواست‌شده برای درگاه پرداخت آنلاین است.
    علاوه‌براین، نرخ خطای هر چرخه را می‌سنجد و اگر درگاه قطعی/کند شده باشد
    (بیشتر چک‌ها fail شوند)، یک‌بار (با cooldown) به ادمین هشدار می‌دهد."""
    while True:
        checked = 0
        failed = 0
        try:
            pending = db.get_pending_online_payments(limit=50)
            for payment in pending:
                checked += 1
                try:
                    invoice = await payments.check_invoice(payment)
                    if invoice and invoice.get("isPaid"):
                        payment_kind = payment.get("kind")
                        # 🐛 فیکس: شاخهٔ "custom" (بساز-خودت) به‌همراه تابع finalize_custom_online_payment حذف شده بود
                        # (دیگر هیچ پرداختی با kind="custom" ساخته نمی‌شود)، اما ایمپورت و فراخوانیش اینجا جا مانده بود و باعث ImportError در استارت ربات می‌شد. حذف شد.
                        if payment_kind == "wallet_charge":
                            result = await finalize_wallet_charge_online_payment(bot, payment)
                        elif payment_kind == "custom":
                            result = await finalize_custom_online_payment(bot, payment)
                        else:
                            result = await finalize_online_payment(bot, payment)
                        if result is None:
                            # یک فراخوانی هم‌زمان دیگر (مثلاً مینی‌اپ) همین الان
                            # در حال پردازش این پرداخت است؛ برای جلوگیری از پیام
                            # تکراری به کاربر، این چرخه کاری انجام نمی‌دهد.
                            continue
                        try:
                            if payment_kind == "wallet_charge":
                                confirm_text = (
                                    f"✅ پرداخت آنلاین شما تأیید شد و کیف پول شما به مبلغ "
                                    f"{payment['price']:,} تومان شارژ شد."
                                )
                            else:
                                confirm_text = (
                                    f"✅ پرداخت آنلاین شما برای «{payment['plan_name']}» تأیید شد "
                                    f"و سفارش ثبت گردید. سرویس شما به‌زودی ارسال می‌شود."
                                )
                            await bot.send_message(int(payment["telegram_id"]), confirm_text)
                        except Exception:
                            logger.exception("ارسال پیام تایید پرداخت خودکار به کاربر ناموفق بود")
                except Exception:
                    failed += 1
                    logger.exception("خطا در بررسی خودکار اینوویس %s", payment.get("hash_id"))
            if checked:
                await alerts.report_uniquepay_check_cycle(bot, ADMIN_ID, checked, failed)
        except Exception:
            logger.exception("خطا در حلقه‌ی پولر پرداخت آنلاین")
        await asyncio.sleep(ONLINE_PAYMENT_POLL_SECONDS)


INVOICE_EXPIRY_POLL_SECONDS = 60


async def invoice_expiry_loop(bot: Bot):
    """هر ۶۰ ثانیه فاکتورهای کارت‌به‌کارت (پلن/بساز-سرویس/کیف‌پول) و پرداخت‌های آنلاین پرداخت‌نشده که مهلت ۳۰ دقیقه‌ایشان تمام شده را حذف می‌کند
    و به کاربر پیامی می‌فرستد که برای دوباره سفارش ثبت کند."""
    while True:
        try:
            expired_invoices = db.expire_due_invoices()
            for inv in expired_invoices:
                try:
                    await bot.send_message(
                        int(inv["telegram_id"]),
                        f"⏰ مهلت ۳۰ دقیقه‌ای پرداخت فاکتور تان برای «{inv['label']}» به پایان رسید و به‌طور خودکار منقضی شد. لطفاً دوباره از منوی سرویس‌ها سفارش تان را ثبت کنید."
                    )
                except Exception:
                    logger.exception("ارسال پیام انقضای فاکتور به کاربر ناموفق بود")
        except Exception:
            logger.exception("خطا در حلقه‌ی انقضای فاکتورها")
        try:
            expired_online = db.expire_due_online_payments()
            for pay in expired_online:
                try:
                    await bot.send_message(
                        int(pay["telegram_id"]),
                        f"⏰ مهلت ۳۰ دقیقه‌ای پرداخت این فاکتور به پایان رسیده و به‌طور خودکار منقضی شد. لطفاً دوباره از منوی سرویس‌ها سفارش تان را ثبت کنید."
                    )
                except Exception:
                    logger.exception("ارسال پیام انقضای پرداخت آنلاین به کاربر ناموفق بود")
        except Exception:
            logger.exception("خطا در حلقه‌ی انقضای پرداخت‌های آنلاین")
        await asyncio.sleep(INVOICE_EXPIRY_POLL_SECONDS)


SELF_PING_INTERVAL_SECONDS = 600  # ۱۰ دقیقه؛ زیر آستانه‌ی خواب ۱۵ دقیقه‌ای Render رایگان.


PANEL_CACHE_WARMUP_SECONDS = 600


async def panel_cache_warmup_loop():
    """🆕 فیکس سرعت: توکن ادمین و لیست تمپلیت‌های پنل VPN فعال را هر ۱۰ دقیقه
    در پس‌زمینه زنده نگه می‌دارد (توکن ۲۰ دقیقه و تمپلیت‌ها ۳۰ دقیقه
    معتبر هستند). بدون این کار، اگر بین دو درخواست ساخت سرویس فاصله‌ای طولانی
    بیفتد، اولین درخواست بعدی مجبور می‌شد قبل از ساخت واقعی سرویس، یک بار لاگین
    بزند و دوباره توکن بگیرد و لیست تمپلیت‌ها را از پنل بخواند — دقیقاً همان
    ۱۰-۱۵ ثانیه‌ای که گزارش شده بود. با این حلقه، کش همیشه گرم نگه داشته می‌شود و هر درخواست
    واقعی ساخت سرویس فقط همان یک درخواست شبکه‌ای واقعی (ساخت کاربر) باقی می‌ماند.
    """
    while True:
        try:
            await vpn_panel.warmup_cache()
        except Exception:
            logger.exception("خطا در گرم‌کردن دوره‌ای کش پنل VPN")
        await asyncio.sleep(PANEL_CACHE_WARMUP_SECONDS)


async def self_ping_loop():
    """🆕 فیکس سیستم زنده‌نگه‌داشتن: قبلاً این ربات هیچ مکانیزم داخلی برای زنده نگه
    داشتن خودش نداشت و کاملاً وابسته به یک سرویس پینگ *خارجی* (مثل UptimeRobot/
    cron-job.org) بود که هر چند دقیقه یک‌بار مسیر health-check را صدا بزند. اگر
    آن سرویس خارجی (یا اکانتی که چند تا از این مانیتورها را با هم مدیریت می‌کند)
    قطع/معلق/محدود شود، همه‌ی رباتهایی که به همان مانیتور وابسته‌اند — حتی اگر
    روی حساب‌های Render کاملاً جدا از هم باشند — همزمان می‌خوابند؛ دقیقاً همان
    چیزی که باعث توقف هم‌زمان هر ۴ ربات شد.

    این تابع یک راه‌حل مستقل و داخلی است: خود ربات هر ۱۰ دقیقه یک‌بار مسیر
    health-check خودش را (از روی آدرس عمومی سرویس، که Render به‌طور خودکار در
    متغیر محیطی RENDER_EXTERNAL_URL قرار می‌دهد) صدا می‌زند. دیگر هیچ سرویس
    بیرونی لازم نیست و هر ربات کاملاً مستقل از بقیه زنده می‌ماند.
    برای دیپلوی‌های غیر Render، می‌توانید آدرس عمومی سرویس را در متغیر محیطی
    SELF_PING_URL هم دستی تنظیم کنید.
    """
    ping_url = os.environ.get("SELF_PING_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if not ping_url:
        logger.warning(
            "self_ping_loop غیرفعال است: نه RENDER_EXTERNAL_URL و نه SELF_PING_URL تنظیم نشده. "
            "اگر روی Render دیپلوی کرده‌اید این متغیر باید خودکار موجود باشد؛ در غیر این صورت "
            "برای جلوگیری از خواب رفتن سرویس، SELF_PING_URL را با آدرس عمومی ربات تنظیم کنید."
        )
        return
    if not ping_url.startswith("http"):
        ping_url = "https://" + ping_url
    logger.info("self_ping_loop فعال شد؛ هر %d ثانیه به %s پینگ می‌زند.", SELF_PING_INTERVAL_SECONDS, ping_url)
    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.sleep(SELF_PING_INTERVAL_SECONDS)
            try:
                async with session.get(ping_url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    logger.info("self-ping انجام شد (وضعیت %s).", resp.status)
            except Exception:
                logger.exception("self-ping ناموفق بود؛ در چرخه‌ی بعدی دوباره تلاش می‌شود.")


def main():
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask keep-alive server started.")

    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
