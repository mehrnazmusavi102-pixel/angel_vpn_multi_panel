"""
utils.py
توابع کمکی کوچک و مشترک بین handlerها.
"""

import os
import time
import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram.types import FSInputFile
from aiogram.exceptions import TelegramBadRequest

from keyboards import main_reply_keyboard
import database as db

logger = logging.getLogger(__name__)

# حداکثر طول متن پیام معمولی تلگرام (send_message)؛ اگر متنی از این بیشتر باشد، تلگرام
# خطای «Bad Request: MESSAGE_TOO_LONG» برمی‌گرداند.
TELEGRAM_TEXT_LIMIT = 4096


def _telegram_utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2

def _truncate_utf16(text: str, max_units: int) -> str:
    raw = text.encode("utf-16-le")[:max_units * 2]
    try:
        return raw.decode("utf-16-le")
    except UnicodeDecodeError:
        return raw[:-2].decode("utf-16-le", errors="ignore")

def truncate_for_telegram(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> str:
    if text is None:
        return text
    if _telegram_utf16_len(text) <= limit:
        return text
    suffix = "\n\n… (متن به‌دلیل محدودیت طول پیام تلگرام کوتاه شد)"
    keep = max(0, limit - _telegram_utf16_len(suffix))
    truncated = _truncate_utf16(str(text), keep)
    entities = getattr(text, "entities", None)
    if entities:
        # Entityهایی که کاملاً داخل قسمت باقی‌مانده هستند حفظ می‌شوند؛ هر entity
        # که از مرز برش عبور کند حذف می‌شود تا Telegram با offset نامعتبر مواجه نشود.
        out = []
        for e in entities:
            try:
                off = int(e.get("offset", 0)); length = int(e.get("length", 0))
                if off + length <= keep:
                    out.append(dict(e))
            except Exception:
                continue
        try:
            from text_catalog import RichText
            return RichText(truncated + suffix, out)
        except Exception:
            pass
    return truncated + suffix

def is_message_too_long_error(exc: Exception) -> bool:
    """تشخیص می‌دهد که آیا یک TelegramBadRequest دقیقاً از نوع «MESSAGE_TOO_LONG» است (و مثلاً یک خطای مربوط به parse mode نیست)؛ تا همه‌جا یکسان تشخیص داده شود."""
    return "message is too long" in str(exc).lower()


def get_main_keyboard(user_id):
    """منوی دائمی پایین صفحه را همیشه برمی‌گرداند."""
    return main_reply_keyboard()

# سرور ربات (Render) با ساعت UTC کار می‌کند و همه‌ی رشده‌های زمانی ذخیره‌شده در
# دیتابیس (created_at/expires_at و ...) بر همین اساس هستند؛ برای اینکه چیزی که
# به کاربر نمایش داده می‌شود (نه چیزی که در فاکتورها/شمارش‌معکوس مقایسه می‌شود)
# همیشه بر طبق ساعت تهران باشد، این دو تابع کمکی فقط برای «نمایش» استفاده می‌شوند.
# 🐛 فیکس: قبلاً اینجا از pytz استفاده می‌شد که در requirements.txt نبود و در محیط دیپلوی (Render)
# باعتت ModuleNotFoundError: No module named 'pytz' می‌شد؛ به جای آن از zoneinfo (کتابخانه‌ی استاندارد پایتون 3.9+) استفاده شد تا وابستگی جدیدی لازم نباشد.
TEHRAN_TZ = ZoneInfo("Asia/Tehran")


def now_tehran() -> datetime:
    """اکنون را بر اساس ساعت تهران برمی‌گرداند (فقط برای نمایش به کاربر/ادمین؛
    نه برای مقایسه با زمان‌های ذخیره‌شده در دیتابیس که بر مبنای ساعت UTC سرور هستند)."""
    return datetime.now(timezone.utc).astimezone(TEHRAN_TZ)


def now_tehran_naive() -> datetime:
    """معادل now_tehran اما به‌صورت naive (بدون tzinfo)؛ برای مقایسه/تفریق با
    تاریخ‌های داخلیو‌شده‌ی بدون تایم‌زون (مثلاً تاریخ انقضای سرویس configs.expiry) لازم است."""
    return now_tehran().replace(tzinfo=None)


def utc_str_to_tehran(dt_str: str, fmt_in: str = "%Y-%m-%d %H:%M:%S"):
    """یک رشته‌ی زمانی که با ساعت UTC سرور ذخیره شده (مثل expires_at فاکتورها) را
    به یک datetime بر اساس ساعت تهران تبدیل می‌کند. در صورت خطا None برمی‌گرداند."""
    try:
        dt_utc = datetime.strptime(dt_str, fmt_in).replace(tzinfo=timezone.utc)
        return dt_utc.astimezone(TEHRAN_TZ)
    except Exception:
        return None


_DIGIT_MAP = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹" "٠١٢٣٤٥٦٧٨٩",
    "0123456789" "0123456789",
)

# 🐛 فیکس: کیبورد فارسی/عربی گوشی‌ها (به‌خصوص iOS) اغلب هنگام تایپ عدد،
# نویسه‌های نامرئی جهت‌ساز/فرمت‌دهنده (RLM, LRM, ALM, ZWNJ, ZWSP, BOM) را
# قبل/بعد/وسط رقم‌ها اضافه می‌کنند. این نویسه‌ها با چشم دیده نمی‌شوند ولی باعث
# می‌شوند isdigit()/int() آن رشته شکست بخورد (یا حتی خطای پیش‌بینی‌نشده در ادامه‌ی
# پردازش رخ بدهد، مثلاً وقتی isdigit() جایی چک نشده و مستقیم int() صدا زده می‌شود).
# قبلاً فقط ارقام فارسی/عربی تبدیل می‌شدند و این نویسه‌های نامرئی حذف نمی‌شدند.
_INVISIBLE_CHARS_MAP = dict.fromkeys(
    ord(ch) for ch in "\u200b\u200c\u200d\u200e\u200f\u061c\ufeff\u202a\u202b\u202c\u202d\u202e"
)

# ---------------------------------------------------------------------------
# قفل سبک برای جلوگیری از اجرای دوباره‌ی یک عملیات مالی/سفارش در بازه‌ی کوتاه.
#
# چرا لازم است؟ اگر ربات به هر دلیلی (مثلاً خوابیدن سرویس رایگان Render) چند
# ثانیه/دقیقه بی‌پاسخ بماند، تلگرام همه‌ی تاچ‌هایی که کاربر پشت سر هم زده را
# صف می‌کند و وقتی ربات بیدار شد، همه را یک‌جا تحویل می‌دهد. هرکدام از این
# callbackهای صف‌شده، یک نسخه‌ی «قدیمی» از پیام (قبل از هر ویرایشی) همراه خودش
# دارد؛ پس چک‌کردن متن پیام (مثلاً «آیا قبلاً تأیید شده؟») برای تشخیص تکراری
# بودن کافی نیست، چون همه‌ی نسخه‌های صف‌شده متن قدیمی یکسانی دارند.
# این قفل با کلید مشخص (کاربر+نوع عملیات) و بدون هیچ await قبل از خودش صدا
# زده می‌شود تا واقعاً به‌عنوان یک بخش اتمیک (غیرقابل‌قطع توسط تسک دیگر asyncio)
# عمل کند.
# ---------------------------------------------------------------------------
_recent_actions: dict[str, float] = {}
_ACTION_COOLDOWN_SECONDS = 15.0


def is_duplicate_action(key: str, cooldown: float = _ACTION_COOLDOWN_SECONDS) -> bool:
    """اگر همین کلید در `cooldown` ثانیه‌ی اخیر پردازش شده باشد True برمی‌گرداند.
    باید همیشه به‌عنوان اولین خط سینک هندلر (قبل از هر await) صدا زده شود."""
    now = time.monotonic()
    last = _recent_actions.get(key)
    _recent_actions[key] = now
    if len(_recent_actions) > 5000:
        cutoff = now - cooldown
        for k in [k for k, t in _recent_actions.items() if t < cutoff]:
            _recent_actions.pop(k, None)
    return last is not None and (now - last) < cooldown


def normalize_digits(text: str) -> str:
    """ارقام فارسی/عربی را به انگلیسی تبدیل می‌کند تا int() و isdigit() درست کار کنند."""
    if not text:
        return text
    return text.translate(_DIGIT_MAP)


def clean_numeric_id(text: str) -> str:
    """ورودی مثل آیدی عددی/مبلغ را پاک‌سازی می‌کند: ارقام فارسی/عربی را به انگلیسی
    تبدیل و نویسه‌های نامرئی جهت‌ساز/فرمت‌دهنده (RLM, LRM, ALM, ZWNJ, ZWSP, BOM و...) را حذف
    می‌کند تا isdigit()/int() روی مقداری که کاربران از کی‌بورد‌های فارسی/عربی تایپ می‌کنند
    (مثلاً آیدی عددی ادمین، مبلغ شارژ، کد تخفیف) درست کار کند.
    """
    if not text:
        return text
    cleaned = text.translate(_INVISIBLE_CHARS_MAP)
    return normalize_digits(cleaned).strip()


def parse_int_in_range(text: str, min_value: int, max_value: int) -> int | None:
    """متن را به عدد صحیح تبدیل می‌کند و اگر در بازه‌ی مجاز نبود None برمی‌گرداند."""
    if not text:
        return None
    cleaned = normalize_digits(text).strip()
    if not cleaned.isdigit():
        return None
    value = int(cleaned)
    if not (min_value <= value <= max_value):
        return None
    return value


# ---------------------------------------------------------------------------
# ⏰ مدیریت فاکتور/مهلت پرداخت و نمایش پیشرفت مرحله‌ای در پیام‌های ربات


# ---------------------------------------------------------------------------
# ⏰ مدیریت فاکتور/مهلت پرداخت و نمایش پیشرفت مرحله‌ای در پیام‌های ربات

def format_deadline_time(expires_at: str) -> str:
    """از رشته YYYY-MM-DD HH:MM:SS (که با ساعت UTC سرور ذخیره شده) ساعت:دقیقه را
    بر اساس ساعت تهران برمی‌گرداند (مثلاً برای پیام «فاکتور تا فلان ساعت معتبر است»)."""
    dt_tehran = utc_str_to_tehran(expires_at)
    if dt_tehran is not None:
        return dt_tehran.strftime("%H:%M")
    try:
        return expires_at.split(" ")[1][:5]
    except Exception:
        return ""


def progress_bar(step: int, total: int) -> str:
    """یک نوار پیشرفت ایموجی‌ای ساده برای نمایش مرحله X از Y در پیام‌های مسیر خرید."""
    step = max(1, min(step, total))
    filled = "🟩" * step + "⬜️" * (total - step)
    label = filled + " مرحله " + str(step) + " از " + str(total)
    return label + chr(10) + chr(10)


# ---------------------------------------------------------------------------
# 🧪 تست: نمایش یک استیکر (ویدیویی) درست بالای یک منو، به‌ازای هر مرحله از
# مسیر خرید. هر بار که این تابع دوباره برای همان چت صدا زده شود، آخرین جفت
# «استیکر + پیام منو»یی که خودش قبلاً فرستاده حذف می‌شود و استیکر/منوی جدید
# جای آن‌ها می‌نشیند؛ یعنی همیشه استیکرِ مرحله‌ی فعلی، درست بالای منوی همان
# مرحله دیده می‌شود.
#
# عمداً این وضعیت (شناسه‌ی پیام استیکر/منوی قبلی) در یک دیکشنری ساده در حافظه
# نگه داشته می‌شود، نه در FSMContext؛ چون خیلی از handlerها همین وسط
# state.clear() صدا می‌زنند (برای پاک کردن state/دیتای قبلی) و اگر این
# اطلاعات را داخل state ذخیره می‌کردیم، با هر state.clear() گم می‌شد و دیگر
# نمی‌توانستیم پیام قبلی را برای حذف پیدا کنیم.
_last_sticker_menu: dict[int, dict[str, int | None]] = {}

# ---------------------------------------------------------------------------
# ⚡ کش‌های حافظه‌ای برای رفع کندی: قبلاً هر بار جابه‌جایی بین منوها باعث
# می‌شد (۱) فایل استیکر پیش‌فرض دوباره از روی دیسک به تلگرام آپلود شود و
# (۲) یک کوئری دیتابیس برای بررسی سفارشی‌سازی ادمین اجرا شود (که وقتی
# دیتابیس روی سرویس ابری/Turso است یعنی یک رفت‌وبرگشت شبکه‌ای اضافه). با کش
# کردن نتیجه‌ی این دو در حافظه، این هزینه‌ها از مسیر اصلی هر جابه‌جایی حذف
# می‌شوند. کش سفارشی‌سازی ادمین فقط با صدا زدن invalidate_section_sticker_cache
# (بعد از هر تغییر در پنل ادمین) باطل می‌شود.
_default_sticker_file_id_cache: dict[str, str] = {}
_section_sticker_override_cache: dict[str, dict | None] = {}
_section_sticker_override_cache_loaded: set[str] = set()


def invalidate_section_sticker_cache(section_key: str) -> None:
    """باید بعد از هر تغییر ادمین روی استیکر یک بخش (آپلود/غیرفعال/فعال/ریست)
    صدا زده شود تا کش حافظه‌ای به‌روز شود و تغییر بلافاصله برای کاربران اعمال شود."""
    _section_sticker_override_cache.pop(section_key, None)
    _section_sticker_override_cache_loaded.discard(section_key)


# نگاشت یک کلید کوتاه و معنادار (که در کد handlerها استفاده می‌شود) به نام
# فایل واقعی استیکر روی دیسک (پوشه‌ی stickers/ کنار همین پروژه).
STICKERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stickers")
STICKER_FILES = {
    "free_test": "test.webm",       # دکمه‌ی «🎁 تست رایگان»
    "buy_plans": "service.webm",    # دکمه‌ی «🛒 خرید اشتراک»
    "plan_select": "plan.webm",     # دکمه‌ی «🚀 سرور VIP»
}

# عنوان فارسی قابل‌نمایش هر بخش، برای استفاده در پنل مدیریت استیکرها (handlers/admin.py).
STICKER_SECTION_LABELS = {
    "start_welcome": "👋 شروع با /start",
    "join_confirmed": "✅ تایید عضویت در کانال‌ها",
    "free_test": "🎁 تست رایگان",
    "buy_plans": "🛒 خرید اشتراک",
    "plan_select": "🚀 انتخاب پلن VIP",
    "my_configs_empty": "📱 سرویس‌های من (بدون سرویس)",
    "my_configs_has": "📱 سرویس‌های من (دارای سرویس)",
    "wallet": "💰 کیف پول",
    "referral": "👥 دعوت دوستان",
    "profile": "👤 پروفایل من",
    "guides_empty": "📚 راهنما (بدون محتوا)",
    "guides_has": "📚 راهنما (دارای محتوا)",
    "support": "👨‍💻 پشتیبانی",
    "agency_request": "🤝 درخواست نمایندگی",
    "vip_category_list": "🚀 ليست پلان‌های دسته VIP",
    "discount_code_entry": "🎟 ورود کد تخفیف",
    "my_configs_list_empty": "📋 ليست سرویس‌های یک دسته (خالی)",
    "my_configs_list_has": "📋 ليست سرویس‌های یک دسته (دارای سرویس)",
    "config_detail": "📦 جزئیات یک سرویس",
    "config_delete_confirm": "🗑 تایید حذف سرویس",
    "wallet_free": "💰 موجودی آزاد (قابل استفاده)",
    "wallet_locked": "🔒 موجودی مسدود (در انتظار)",
    "wallet_transactions": "📋 تراکنش‌های کیف پول",
    "wallet_charge": "💵 شارژ کیف پول",
    "purchase_history": "🛒 تاریخچه خرید",
    "ticket_write": "✍️ نوشتن پیام تیکت",
    "plan_payment_method": "💳 انتخاب روش پرداخت (خرید پلن)",
    "plan_pay_wallet": "👛 پرداخت با کیف پول (خرید پلن)",
    "plan_pay_online": "🌐 پرداخت آنلاین (خرید پلن)",
    "plan_pay_card": "💳 پرداخت کارت‌به‌کارت (خرید پلن)",
    "walletcharge_method": "💳 انتخاب روش شارژ کیف پول",
    "walletcharge_pay_card": "💳 شارژ با کارت‌به‌کارت",
    "walletcharge_pay_online": "🌐 شارژ آنلاین کیف پول",
    # 🔔 پیام‌های اطلاع‌رسانی (نه منو): این کلیدها استیکر پیش‌فرض ندارند و
    # فقط وقتی ادمین از پنل ادمین برایشان چیزی آپلود/فعال کند نمایش داده
    # می‌شوند (send_notification_sticker پایین همین فایل).
    "notif_personal_message": "✉️ پیام شخصی ادمین به کاربر",
    "notif_broadcast": "📢 پیام همگانی",
    "notif_expiry": "⏰ هشدار پایان سرویس",
    "notif_usage_80": "🔔 هشدار مصرف ۸۰٪ حجم",
    "notif_usage_90": "🔥 هشدار اتمام حجم (۹۰٪)",
    "notif_wallet_charge": "💳 شارژ کیف پول توسط ادمین",
    "notif_service_delivery": "📦 ارسال سرویس توسط ادمین",
    "notif_purchase_approved": "✅ تایید پرداخت کارت‌به‌کارت",
    "notif_receipt_rejected": "❌ رد رسید (خرید/شارژ)",
}


async def _delete_messages_in_background(bot, chat_id: int, message_ids: list[int]) -> None:
    """پیام‌های قدیمی (استیکر/منوی مرحله‌ی قبل) را در پس‌زمینه حذف می‌کند
    تا کاربر منتظر تمام‌شدن حذف نماند و منوی جدید هرچه سریع‌تر ظاهر شود
    (قبلاً این دو delete_message قبل از فرستادن منوی جدید، به‌صورت بلاک‌کننده اجرا
    می‌شدند و کاربر منتظر دو رفت‌وبرگشت اضافه به تلگرام می‌ماند)."""
    for msg_id in message_ids:
        try:
            await bot.delete_message(chat_id, msg_id)
        except Exception:
            pass


def _get_section_sticker_override(sticker_key: str) -> dict | None:
    """نسخه‌ی کش‌شده‌ی db.get_section_sticker؛ قبلاً این کوئری روی هر جابه‌جایی
    منو (حتی وقتی ادمین چیزی سفارشی نکرده بود) اجرا می‌شد و به‌خصوص وقتی دیتابیس
    روی سرویس ابری (Turso) است، کندی محسوسی اضافه می‌کرد."""
    if sticker_key in _section_sticker_override_cache_loaded:
        return _section_sticker_override_cache.get(sticker_key)
    override = None
    try:
        import database as db  # lazy import: از وابستگی حلقوی بین ماژول‌ها جلوگیری می‌شود
        override = db.get_section_sticker(sticker_key)
    except Exception:
        logger.exception("خطا در خواندن تنظیمات استیکر بخش '%s' از دیتابیس", sticker_key)
    _section_sticker_override_cache[sticker_key] = override
    _section_sticker_override_cache_loaded.add(sticker_key)
    return override


async def show_menu_with_sticker(
    bot,
    chat_id: int,
    sticker_key: str | None,
    text: str,
    reply_markup=None,
    parse_mode: str | None = None,
    show_main_keyboard: bool = True,
):
    """یک پیام منوی تازه می‌فرستد (همیشه پیام جدید، نه ویرایش پیام قبلی) و اگر
    sticker_key داده شده باشد، درست بالای همان منو یک استیکر می‌فرستد.

    🚀 پرفورمنس: اگر استیکر پیش‌فرض (غیرسفارشی) باشد، فقط دفعه‌ی اول از روی دیسک
    آپلود می‌شود و file_id برگشتی‌شده تلگرام در حافظه کش می‌شود؛ دفعات بعدی فقط
    همان file_id را می‌فرستد (بدون خواندن دوباره‌ی فایل از دیسک)؛ همین بزرگ‌ترین عامل کندی قبلی بود.

    ✅ منوی دائمی پایین صفحه (main_reply_keyboard) همراه با هر استیکری که اینجا فرستاده
    می‌شود دوباره تازه می‌شود؛ قبلاً فقط در /start فرستاده می‌شد و با حذف همان پیام در
    جابه‌جایی بعدی از دید کاربر گم می‌شد و کاربر مجبور می‌شد دوباره /start بزند.

    ⚠️ show_main_keyboard=False: فقط برای صفحه‌ی «عضویت اجباری در کانال‌ها» (پیش از
    تأیید عضویت) استفاده شود؛ چون هیچ‌کدام از handlerهای منوی پایین صفحه، عضویت
    کاربر را دوباره چک نمی‌کنند، اگر آنجا هم منوی پایین صفحه فعال شود کاربرِ
    هنوز-عضونشده می‌تواند بدون عضویت واقعی از دکمه‌های پایین صفحه استفاده کند.

    پیام‌های قبلی (استیکر/منوی مرحله‌ی قبل) در پس‌زمینه حذف می‌شوند تا کاربر منتظر
    تمام‌شدن حذف نماند و منوی جدید در سریع‌ترین حالت ممکن ظاهر شود. اگر sticker_key
    مقدار None باشد، فقط پیام منو (بدون استیکر جدید) فرستاده می‌شود؛ برای مرحله‌هایی
    که نباید استیکری در آن‌ها نمایش داده شود (مثلاً مرحله‌ی نهایی انتخاب/انجام پرداخت).
    """
    # منوی قبلی حذف نمی‌شود.
    prev = _last_sticker_menu.get(chat_id)

    new_sticker_msg_id = None
    if sticker_key:
        # ابتدا بررسی می‌شود که آیا ادمین از پنل ادمین برای این بخش چیزی سفارشی کرده
        # (استیکر خاص یا غیرفعال‌سازی کامل)؛ اگر چیزی سفارشی نشده باشد، از استیکر
        # پیش‌فرض داخل پروژه استفاده می‌شود (از کش در صورت موجود).
        override = _get_section_sticker_override(sticker_key)

        sticker_source = None  # ("file_id", value) یا ("path", value)
        if override is not None:
            if override.get("is_enabled") and override.get("file_id"):
                sticker_source = ("file_id", override["file_id"])
            # اگر ادمین صریحاً این بخش رو غیرفعال کرده باشد (is_enabled=0)، هیچ استیکری نشون داده نمی‌شه.
        else:
            cached_file_id = _default_sticker_file_id_cache.get(sticker_key)
            if cached_file_id:
                sticker_source = ("file_id", cached_file_id)
            else:
                filename = STICKER_FILES.get(sticker_key)
                if filename:
                    candidate_path = os.path.join(STICKERS_DIR, filename)
                    # یک قالب آماده-فروش تمام فایل‌های استیکر پیش‌فرض خالی (حجم صفر) هستند، که دقیقاً همین حالت پیش‌فرض قالب است: تا وقتی ادمین از پنل ادمین (بخش «مدیریت استیکرها») استیکری آپلود نکند، هیچ خطایی رخ نمی‌دهد و فقط منوی بدون استیکر فرستاده می‌شود.
                    try:
                        has_content = os.path.getsize(candidate_path) > 0
                    except OSError:
                        has_content = False
                    if has_content:
                        sticker_source = ("path", candidate_path)

        if sticker_source:
            sticker_reply_markup = get_main_keyboard(chat_id) if show_main_keyboard else None
            try:
                if sticker_source[0] == "file_id":
                    sticker_msg = await bot.send_sticker(
                        chat_id, sticker=sticker_source[1], reply_markup=sticker_reply_markup,
                    )
                else:
                    sticker_msg = await bot.send_sticker(
                        chat_id, sticker=FSInputFile(sticker_source[1]), reply_markup=sticker_reply_markup,
                    )
                    # فقط برای استیکرهای پیش‌فرض (غیرسفارشی) کش می‌شود تا دفعه‌ی
                    # بعد به‌جای آپلود دوباره‌ی فایل از دیسک، همان file_id تلگرام مستقیم
                    # استفاده شود (بزرگ‌ترین عامل کندی قبلی همین بود).
                    if sticker_msg.sticker and sticker_msg.sticker.file_id:
                        _default_sticker_file_id_cache[sticker_key] = sticker_msg.sticker.file_id
                new_sticker_msg_id = sticker_msg.message_id
            except Exception:
                logger.exception("خطا در ارسال استیکر تست '%s'", sticker_key)

    if new_sticker_msg_id is None and show_main_keyboard:
        # 🆕 فیکس: تا امروز وقتی استیکری فرستاده نمی‌شد (چون ادمین برای این بخش
        # استیکری آپلود نکرده بود، یا این مرحله عمداً بدون استیکر است — مثلاً
        # مراحل نهایی پرداخت/رسید)، منوی دائمی پایین صفحه هیچ‌وقت در این مسیر
        # تازه نمی‌شد. چون پیام مرحله‌ی قبل (که همان منو را حمل می‌کرد) همین‌جا
        # در جابه‌جایی بعدی حذف می‌شود، بعد از چند مرحله (دقیقاً مثل کل فرایند
        # خرید) منو کامل از دید کاربر گم می‌شد. برای همان تضمینی که برای
        # استیکرهای واقعی وجود دارد، این‌جا هم یک پیام کاملاً نامرئی فقط برای
        # حمل/تازه‌سازی همان منو فرستاده می‌شود (و مثل استیکر، در مرحله‌ی بعد
        # خودش پاک می‌شود؛ هیچ اثر دیداری اضافه‌ای برای کاربر ندارد).
        # 🐛 فیکس: کاراکتر قبلی «⠀» (U+2800 Braille Pattern Blank) توسط تلگرام به‌عنوان متن کاملاً خالی رد می‌شد
        # (خطای دقیق تلگرام: "text must be non-empty")، در نتیجه این پیام نامرئی هرگز ارسال نمی‌شد و منوی دائمی
        # پایین صفحه (وقتی استیکری فرستاده نمی‌شود) دوباره تازه نمی‌شد. به جایش از "⠀"، از "ㅤ"
        # (U+3164 Hangul Filler) استفاده می‌شود: دقیقاً مثل قبل برای چشم کاربر کاملاً خالی/نامرئی است، ولی
        # چون یک حرف واقعی (نه کاراکتر جداکننده/فضای‌خالی) است، تلگرام آن را خالی تلقی نمی‌کند.
        try:
            invisible_msg = await bot.send_message(chat_id, "ㅤ", reply_markup=get_main_keyboard(chat_id))
            new_sticker_msg_id = invisible_msg.message_id
        except Exception:
            logger.exception("خطا در ارسال پیام نامرئی تازه‌سازی منوی پایین صفحه")

    try:
        menu_msg = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        # 🆕 فیکس: اگر متنی که ادمین از پنل ویرایش کرده (مثلاً پیام خوش‌آمدگویی /start یا هر متن قابل‌ویرایش دیگری) از سقف مجاز تلگرام برای متن پیام (۴۰۹۶ کاراکتر) بلندتر باشد، تلگرام خطای «Bad Request: MESSAGE_TOO_LONG» برمی‌گرداند و قبلاً هیچ‌وقت دوباره تلاشی نمی‌شد (چون فقط حالت parse_mode دست‌کاری می‌شد)؛ برای کاربرانی که تازه روی /start می‌زدند (بیشتر از همه کاربران جدید) کل منوی /start با ارور مواجه می‌شد. حالا اگر خطا دقیقاً همین باشد، متن کوتاه شده دوباره فرستاده می‌شود تا کاربر هیچ‌وقت با خطا مواجه نشود.
        if is_message_too_long_error(e):
            logger.error(
                "متن منو (پیش‌نمایش %d کاراکتر) از سقف تلگرام (۴۰۹۶) بیشتر بود؛ کوتاه شد و دوباره فرستاده شد.", len(text),
            )
            safe_text = truncate_for_telegram(text)
            try:
                menu_msg = await bot.send_message(chat_id=chat_id, text=safe_text, reply_markup=reply_markup, parse_mode=parse_mode)
            except TelegramBadRequest:
                try:
                    menu_msg = await bot.send_message(chat_id=chat_id, text=safe_text, reply_markup=reply_markup, parse_mode=None)
                except Exception:
                    # 🆕 لایه‌ی محافظتی نهایی: اگر حتی نسخه‌ی کوتاه‌شده و بدون‌فرمت هم رد شد (مثلاً چون خودِ کیبورد/دکمه مشکل دارد، نه متن)، دیگر هیچ تلاش دیگری برای این نسخه نمی‌شود؛ همان پیام حداقلی نهایی (بدون کیبورد سفارشی) فرستاده می‌شود تا کاربر هیچ‌وقت با سکوت کامل مواجه نشود.
                    logger.exception("حتی نسخه‌ی کوتاه‌شده‌ی پیام منو هم ارسال نشد؛ آخرین تلاش بدون کیبورد/فرمت انجام می‌شود")
                    menu_msg = await _send_last_resort_menu_message(bot, chat_id, text)
        else:
            # 🆕 فیکس: اگر متن (مثلاً متن سفارشی ویرایش کارت که ادمین از پنل ویرایش کرده) شامل کاراکترهای خاص HTML/Markdown نامعتبر (مثلاً < یا > تکی بدون بسته شدن) باشد و تلگرام نتواند پارسش کند، یا حتی خودِ reply_markup (مثلاً لینک نامعتبر یک دکمه‌ی کانال اجباری) مشکل داشته باشد، قبلاً فقط parse_mode دوباره تلاش می‌شد و اگر مشکل از کیبورد بود همان تلاش دوباره هم شکست می‌خورد و کاربر هیچ پیامی دریافت نمی‌کرد. حالا اگر تلاش دوم هم شکست بخورد، آخرین لایه‌ی محافظتی (بدون کیبورد/فرمت) اجرا می‌شود.
            logger.exception("خطا در ارسال پیام منو با parse_mode='%s'، دوباره بدون فرمت ارسال می‌شود", parse_mode)
            try:
                menu_msg = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=None)
            except Exception:
                logger.exception("خطای غیرمنتظره‌ی دیگر (احتمالاً خودِ کیبورد/دکمه نامعتبر است)؛ آخرین تلاش بدون کیبورد/فرمت انجام می‌شود")
                menu_msg = await _send_last_resort_menu_message(bot, chat_id, text)
    except Exception:
        # 🆕 لایه‌ی محافظتی نهایی برای هر خطای کاملاً غیرمنتظره‌ی دیگر (نه فقط TelegramBadRequest) که ممکن است در ارسال پیام منو رخ دهد؛ هدف این است که این تابع هرگز کاربر را بدون هیچ پاسخی رها نکند.
        logger.exception("خطای کاملاً غیرمنتظره در ارسال پیام منو؛ آخرین تلاش بدون کیبورد/فرمت انجام می‌شود")
        menu_msg = await _send_last_resort_menu_message(bot, chat_id, text)
    _last_sticker_menu[chat_id] = {"sticker_msg_id": new_sticker_msg_id, "menu_msg_id": menu_msg.message_id}
    return menu_msg


async def _send_last_resort_menu_message(bot, chat_id: int, text: str):
    """آخرین لایه‌ی محافظتی داخل show_menu_with_sticker: وقتی حتی نسخه‌ی کوتاه‌شده/بدون‌فرمت هم ارسال نشد (مثلاً چون خودِ کیبورد همراهش نامعتبر بود، نه متن)، بدون هیچ کیبورد سفارشی و فرمتی، و با متنی کاملاً کوتاه، یک پیام حداقلی می‌فرستد تا کاربر هرگز با سکوت کامل مواجه نشود."""
    minimal_text = truncate_for_telegram(text, 1000) if text else "🏠 خوش آمدید!"
    return await bot.send_message(chat_id=chat_id, text=minimal_text)


async def send_notification_sticker(bot, chat_id: int, sticker_key: str) -> None:
    """برای پیام‌های اطلاع‌رسانیِ تکی (نه مسیر منو) - مثل پیام شخصی ادمین،
    پیام همگانی، هشدار انقضا/مصرف سرویس، شارژ کیف پول توسط ادمین یا ارسال
    سرویس توسط ادمین - اگر ادمین از پنل ادمین (بخش «مدیریت استیکرها») برای
    همین sticker_key چیزی آپلود و فعال کرده باشد، همان استیکر را درست قبل از
    پیام اصلی می‌فرستد.

    برخلاف show_menu_with_sticker:
    - این کلیدها هیچ استیکر پیش‌فرض پروژه‌ای ندارند (در STICKER_FILES نیستند)؛
      یعنی تا وقتی ادمین چیزی آپلود نکند، هیچ استیکری فرستاده نمی‌شود و هیچ
      رفتار فعلی تغییر نمی‌کند.
    - هیچ پیام قبلی حذف نمی‌شود و کیبورد پایین صفحه دوباره فرستاده نمی‌شود،
      چون این پیام‌ها مستقل از مسیر منو و در هر لحظه‌ای ممکن است ارسال شوند.
    - هر خطایی (مثلاً کاربر ربات را بلاک کرده) بی‌صدا نادیده گرفته می‌شود تا
      ارسال پیام اصلی بعد از آن هیچ‌وقت به‌خاطر این استیکر متوقف نشود.
    """
    override = _get_section_sticker_override(sticker_key)
    if not override or not override.get("is_enabled") or not override.get("file_id"):
        return
    try:
        await bot.send_sticker(chat_id, sticker=override["file_id"])
    except Exception:
        logger.exception("خطا در ارسال استیکر اطلاع‌رسانی '%s'", sticker_key)


async def send_admin_task_message(bot, main_admin_id: int, permission: str, text: str, reply_markup=None, parse_mode=None):
    """ارسال کار عملیاتی ادمین: اگر ادمین فرعی مسئول آن مجوز وجود دارد،
    پیام عملیاتی فقط برای او/آن‌ها ارسال می‌شود؛ وگرنه برای ادمین اصلی.
    خروجی: لیست آیدی گیرنده‌های عملیاتی."""
    try:
        import database as db
        targets = db.get_admin_notification_targets(permission)
    except Exception:
        targets = []
    if not targets:
        targets = [str(main_admin_id)]
    sent = []
    for tid in targets:
        try:
            await bot.send_message(int(tid), text, reply_markup=reply_markup, parse_mode=parse_mode)
            sent.append(str(tid))
        except Exception:
            pass
    return sent

async def forward_admin_task_message(bot, main_admin_id: int, permission: str, from_chat_id: int, message_id: int):
    try:
        import database as db
        targets = db.get_admin_notification_targets(permission)
    except Exception:
        targets = []
    if not targets:
        targets = [str(main_admin_id)]
    sent = []
    for tid in targets:
        try:
            await bot.forward_message(int(tid), from_chat_id, message_id)
            sent.append(str(tid))
        except Exception:
            pass
    return sent
