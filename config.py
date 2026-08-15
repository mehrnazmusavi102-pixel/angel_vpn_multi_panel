"""
config.py
تمام تنظیمات ربات از اینجا خوانده می‌شود.
هیچ مقدار حساس (توکن، آیدی ادمین، شماره کارت) نباید مستقیم داخل کد نوشته شود؛
همه از فایل .env خوانده می‌شوند.
"""

import os
from dotenv import load_dotenv

load_dotenv()

def _get_env(key: str, required: bool = True, default=None):
    value = os.environ.get(key, default)
    if required and (value is None or value == ""):
        raise RuntimeError(f"متغیر محیطی الزامی '{key}' در فایل .env تنظیم نشده است.")
    return value

TOKEN = _get_env("TOKEN")
ADMIN_ID = int(_get_env("ADMIN_ID"))

CARD_NUMBER = _get_env("CARD_NUMBER")
CARD_HOLDER = _get_env("CARD_HOLDER")

# ---------------------------------------------------------------------------
# درگاه پرداخت آنلاین یونیک‌پی (UniquePay) — کارت‌به‌کارت با تایید خودکار.
# اگر UNIQUEPAY_BUSINESS_TOKEN خالی باشد، این روش پرداخت به‌طور خودکار از
# لیست روش‌های پرداخت (ربات و مینی‌اپ) مخفی می‌شود.
# ---------------------------------------------------------------------------
UNIQUEPAY_BASE_URL = _get_env("UNIQUEPAY_BASE_URL", required=False, default="https://uniquepay.top")
UNIQUEPAY_BUSINESS_TOKEN = _get_env("UNIQUEPAY_BUSINESS_TOKEN", required=False, default="")
# آدرسی که کاربر پس از پرداخت موفق به آن هدایت می‌شود (باید با دامنه‌ی
# ثبت‌شده برای بیزینس در پنل uniquepay.top مطابقت داشته باشد؛ برای ربات
# می‌توان لینک خود ربات را ثبت کرد).
UNIQUEPAY_REDIRECT_URL = _get_env("UNIQUEPAY_REDIRECT_URL", required=False, default="")
UNIQUEPAY_ENABLED = bool(UNIQUEPAY_BUSINESS_TOKEN)
# حداقل مبلغی که درگاه پرداخت آنلاین (یونیک‌پی) قبول می‌کند. برای مبالغ
# مساوی یا کمتر از این عدد، درگاه آنلاین (نه در خرید پلن، نه در شارژ کیف
# پول) نمایش داده نمی‌شود و کاربر باید از کارت‌به‌کارت یا کیف پول استفاده کند.
ONLINE_PAYMENT_MIN_AMOUNT = 50_000
# سقف امن شارژ کیف پول؛ جلوی اینوویس‌ها و رسیدهای غیرمنطقی را می‌گیرد.
MAX_WALLET_TOPUP = int(_get_env("MAX_WALLET_TOPUP", required=False, default="100000000"))

DATABASE_PATH = _get_env("DATABASE_PATH", required=False, default="database.db")

# اتصال به Turso (دیتابیس ابری رایگان و دائمی، سازگار با SQLite).
# اگر این دو مقدار در .env تنظیم شده باشند، ربات به‌جای فایل محلی SQLite
# (که با هر دیپلوی روی Render پاک می‌شود) از Turso استفاده می‌کند و اطلاعات
# کاربران برای همیشه حفظ می‌شود. اگر خالی بمانند، ربات مثل قبل از فایل محلی
# استفاده می‌کند (مناسب اجرای تستی روی سیستم شخصی).
TURSO_DATABASE_URL = _get_env("TURSO_DATABASE_URL", required=False, default="")
TURSO_AUTH_TOKEN = _get_env("TURSO_AUTH_TOKEN", required=False, default="")

# 🐛 فیکس: اینجا قبلاً دو کانال متعلق به کسب‌وکار اولیه‌ی ربات ثابت بود که برای هر نسخه‌ی قالب سفید (وایت‌لبل) هم کاربرهای جدید را مجبور عضویت در کانال‌های مالک اولیه می‌کرد. حالا خالی است؛ هر مالک قالب باید کانال(های) خودش را از منوی ادمین ← «اطلاعات ربات» (یا مشابه) اضافه کند؛ تا وقتی هیچ کانالی توسط ادمین اضافه نشده، عضویت اجباری گرفته نمی‌شود و کاربران مستقیم وارد ربات می‌شوند.
REQUIRED_CHANNELS = []

VIP_PLANS = {
    # 🐛 فیکس: قبلاً "volume_gb" اینجا ست نشده بود، در نتیجه هنگام seed اولیه‌ی
    # دیتابیس مقدار volume_gb این پلن‌ها صفر ذخیره می‌شد و چون آزادسازی پاداش
    # معرف با شرط volume_gb >= REFERRAL_MIN_VOLUME_GB انجام می‌شود، خرید همین
    # پلن‌های پیش‌فرض (که محصول اصلی سایت‌اند) هرگز پاداش دعوت را آزاد نمی‌کرد.
    "plan_1": {"name": "10 گیگ | کاربر و زمان ∞", "price": 75000, "days": 0, "volume_gb": 10},
    "plan_3": {"name": "20 گیگ | کاربر و زمان ∞", "price": 150000, "days": 0, "volume_gb": 20},
    "plan_6": {"name": "30 گیگ | کاربر و زمان ∞", "price": 225000, "days": 0, "volume_gb": 30},
    "plan_7": {"name": "50 گیگ | کاربر و زمان ∞", "price": 300000, "days": 0, "volume_gb": 50},
    "plan_8": {"name": "100 گیگ | کاربر و زمان ∞", "price": 500000, "days": 0, "volume_gb": 100},
}
# ⚠️ توجه: از این به بعد VIP_PLANS فقط برای «بار اول» (seed) دیتابیس استفاده می‌شود.
# منبع اصلی و همیشه‌به‌روز پلن‌های VIP، جدول‌های vip_categories/vip_plans در
# database.py است (چون از پنل ادمین قابل افزودن/ویرایش/حذف است — بخش «دسته‌بندی VIP»).
# برای گرفتن لیست واقعی/فعلی پلن‌های VIP همیشه از db.get_all_vip_plans_flat() یا
# db.get_vip_plans(category_id) استفاده کنید، نه از این دیکشنری.
# 🐛 اگر این پروژه از قبل یک‌بار روی یک دیتابیس اجرا شده و پلن‌های پیش‌فرض با
# volume_gb=0 ذخیره شده‌اند، اجرای مجدد init_db() این seed قدیمی را عوض
# نمی‌کند (فقط «بار اول که دیتابیس کاملاً خالی است» seed می‌شود). برای دیتابیس‌های
# قدیمی، مقدار حجم هر پلن را یک‌بار از پنل ادمین → «دسته‌بندی VIP» ویرایش/ذخیره
# کنید تا مقدار صحیح در دیتابیس هم ثبت شود.

# پلن «تست» — با زدن دکمه‌ی «🎁 تست رایگان» دقیقاً مثل بقیه‌ی پلن‌ها
# (با همان مراحل کیف‌پول/کارت‌به‌کارت) به کاربر نمایش داده می‌شود.
FREE_TEST_PLAN_KEY = "plan_test"
FREE_TEST_PLAN = {"name": "1 گیگ 7 روزه", "price": 2000, "days": 7, "volume_gb": 1}

# ⚠️ PLANS/plan_type اینجا فقط «snapshot اولیه» هستند (برای seed دیتابیس و fallback).
# چون از این پس دسته‌بندی‌ها و پلن‌های VIP از پنل ادمین اضافه/ویرایش می‌شوند، این دو
# دیگر منبع درستی نیستند و در همه‌جای ربات به‌جایشان از db.get_all_plans() و
# db.plan_type(plan_key) استفاده می‌کنیم (که همیشه لیست واقعی/به‌روز را از دیتابیس می‌خوانند).
PLANS = {**VIP_PLANS, FREE_TEST_PLAN_KEY: FREE_TEST_PLAN}


def plan_type(plan_key: str) -> str:
    """نگه‌داشته‌شده فقط برای سازگاری با کد قدیمی؛ در کد جدید از db.plan_type استفاده کنید."""
    if plan_key == FREE_TEST_PLAN_KEY:
        return "test"
    return "vip"


# ---------------------------------------------------------------------------
# کانال «اعتماد» — همه‌ی سفارش‌های نهایی‌شده (شامل تست رایگان) با یک قالب
# ثابت در این کانال لاگ می‌شوند. پیش‌فرض همان کانال اعتمادی است که در بالا
# در REQUIRED_CHANNELS تعریف شده؛ اگر بخواهید کانال دیگری باشد، در .env
# مقدار ORDER_LOG_CHANNEL_ID را ست کنید.
# 🐛 فیکس: پیش‌فرض قبلی هاردکد به آیدی کانال اعتماد نسخهی اصلی بود (مشابه باگ قبلی REQUIRED_CHANNELS)؛ در نسخه‌های جدید وایت‌لبل، اگر این ENV تنظیم نشود، سفارش‌های خودرا اشتباهی در کانال اعتماد نسخهی اصلی لاگ می‌کرد. مقدار "0" معنی غیرفعال است (alerts.py این حالت را چک می‌کند).
ORDER_LOG_CHANNEL_ID = int(_get_env("ORDER_LOG_CHANNEL_ID", required=False, default="0"))

# ---------------------------------------------------------------------------
# نمایندگی — تخفیف ۵۰٪ روی محصولات VIP برای آیدی‌های عددی‌ای که ادمین به‌عنوان
# «نماینده» ثبت می‌کند (از طریق دیتابیس؛ جدول agents در database.py).
AGENCY_VIP_DISCOUNT_PERCENT = int(_get_env("AGENCY_VIP_DISCOUNT_PERCENT", required=False, default="50"))

# متن معرفی سرویس‌ها که هنگام ورود به «🎁 خرید اشتراک» نمایش داده می‌شود.
PLANS_INTRO_TEXT = (
    "🛒 *خرید اشتراک*\n\n"
    "🚀 *سرور VIP (V2Ray)*\n"
    "فیلترشکن پرسرعت و پایدار؛ مناسب وب‌گردی با IP ثابت.\n"
    "✅ حتی در «اینترنت ملی» بدون قطعی\n\n"
    "لطفاً سرویس مورد نظر خود را از منوی زیر انتخاب کنید 👇"
)

# مبلغی که با ثبت‌نام هر فرد دعوت‌شده، به‌صورت "قفل" به معرف تعلق می‌گیرد
# و فقط بعد از اینکه فرد دعوت‌شده یک خرید واجد شرط (حجم >= REFERRAL_MIN_VOLUME_GB)
# داشته باشد، آزاد می‌شود.
REFERRAL_LOCK_AMOUNT = int(_get_env("REFERRAL_LOCK_AMOUNT", required=False, default="20000"))

# حداقل حجم (به گیگابایت) خریدی که فرد دعوت‌شده باید انجام دهد تا پاداش دعوت
# معرفش آزاد شود. خریدهای زیر این حجم (مثل تست رایگان یا پلن‌های ۵ گیگ) پاداش
# را آزاد نمی‌کنند.
REFERRAL_MIN_VOLUME_GB = int(_get_env("REFERRAL_MIN_VOLUME_GB", required=False, default="10"))
BOT_USERNAME = _get_env("BOT_USERNAME", required=False, default="BusinessSVPNBot")

# اگر UNIQUEPAY_REDIRECT_URL در .env تنظیم نشده باشد، پیش‌فرض لینک خود ربات
# است (باید این آدرس هم در پنل uniquepay.top به‌عنوان دامنه‌ی مجاز ثبت شود).
UNIQUEPAY_REDIRECT_URL = UNIQUEPAY_REDIRECT_URL or ("https://t.me/" + BOT_USERNAME)

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

# 🛠 «بساز سرویس خودت» — قیمت و محدوده
CUSTOM_BUILD_PRICE_PER_GB = int(_get_env("CUSTOM_BUILD_PRICE_PER_GB", required=False, default="5000"))
CUSTOM_BUILD_PRICE_PER_30_DAYS = int(_get_env("CUSTOM_BUILD_PRICE_PER_30_DAYS", required=False, default="5000"))
CUSTOM_BUILD_MIN_GB, CUSTOM_BUILD_MAX_GB = 5, 1000
CUSTOM_BUILD_MIN_DAYS, CUSTOM_BUILD_MAX_DAYS = 30, 1000

# لینک آموزش اتصال که زیر پیام ارسال کانفیگ به خریدار نمایش داده می‌شود.
CONNECTION_GUIDE_URL = _get_env(
    "CONNECTION_GUIDE_URL", required=False, default="https://t.me/businesss_vpn/16?single"
)

# ---------------------------------------------------------------------------
# 🔗 پنل مرزبان (Marzban) — ساخت/تمدید/فعال‌سازی خودکار سرویس از
# طریق REST API رسمی پنل مرزبان (نه سرویس reseller خاصی، بلکه خود پنل مرزبانی
# که روی سرور خودتان نصب می‌کنید).
# ⚠️ MARZBAN_USERNAME/MARZBAN_PASSWORD مقادیر کاملاً محرمانه‌اند: هرگز داخل کد commit
# نشود، فقط در .env (سمت سرور) قرار بگیرند. اگر خالی باشند، کل بخش «اتصال
# پنل مرزبان» در ربات به‌طور خودکار غیرفعال/مخفی می‌شود.
# ---------------------------------------------------------------------------
MARZBAN_BASE_URL = _get_env("MARZBAN_BASE_URL", required=False, default="").rstrip("/")
MARZBAN_USERNAME = _get_env("MARZBAN_USERNAME", required=False, default="")
MARZBAN_PASSWORD = _get_env("MARZBAN_PASSWORD", required=False, default="")
MARZBAN_ENABLED = bool(MARZBAN_BASE_URL and MARZBAN_USERNAME and MARZBAN_PASSWORD)

# ---------------------------------------------------------------------------
# 🛡️ پنل پاسارگارد (PasarGuard) — دومین گزینه‌ی پنل VPN پشتیبانی‌شده (در کنار
# پنل مرزبان) برای ساخت/تمدید/فعال‌سازی خودکار سرویس از طریق REST API پنل.
# پاسارگارد یک درگاه پرداخت آنلاین نیست — درست مثل مرزبان یک پنل مدیریت
# سرویس VPN است. اگر هردو پنل (مرزبان و پاسارگارد) متصل باشند، ادمین از پنل
# ادمین (بخش اتصال پنل VPN) می‌تواند یکی را به‌عنوان «پنل متصل» انتخاب کند.
# ⚠️ PASARGAD_USERNAME/PASARGAD_PASSWORD مقادیر کاملاً محرمانه‌اند و فقط در
# .env قرار می‌گیرند. اگر خالی باشند، این پنل در ربات غیرفعال/مخفی می‌شود.
# ---------------------------------------------------------------------------
PASARGAD_BASE_URL = _get_env("PASARGAD_BASE_URL", required=False, default="").rstrip("/")
PASARGAD_USERNAME = _get_env("PASARGAD_USERNAME", required=False, default="")
PASARGAD_PASSWORD = _get_env("PASARGAD_PASSWORD", required=False, default="")
PASARGAD_ENABLED = bool(PASARGAD_BASE_URL and PASARGAD_USERNAME and PASARGAD_PASSWORD)

# ---------------------------------------------------------------------------
# 🦋 پنل Rebecca — سازگار با REST API ریکا/Rebecca و نسخه‌های مبتنی بر Marzban
# ---------------------------------------------------------------------------
REBECCA_BASE_URL = _get_env("REBECCA_BASE_URL", required=False, default="").rstrip("/")
REBECCA_USERNAME = _get_env("REBECCA_USERNAME", required=False, default="")
REBECCA_PASSWORD = _get_env("REBECCA_PASSWORD", required=False, default="")
REBECCA_ENABLED = bool(REBECCA_BASE_URL and REBECCA_USERNAME and REBECCA_PASSWORD)

