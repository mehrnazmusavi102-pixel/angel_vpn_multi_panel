"""
bot_info.py
«اطلاعات ربات» — مجموعه‌ی تنظیمات هویتی/کسب‌وکاری (نه تنظیمات فنی حساس) که هم
می‌توانند از .env (config.py) خوانده شوند و هم — بدون نیاز به ری‌دیپلوی —
از پنل ادمین (بخش «ℹ️ اطلاعات ربات») در دیتابیس بازنویسی/ویرایش شوند.

این دقیقاً همان الگوی موجود plan_overrides (database.py: get_setting/set_setting)
است: هر کلید ابتدا از جدول settings دیتابیس خوانده می‌شود؛ اگر چیزی برایش
ذخیره نشده باشد (هنوز ادمین ویرایشش نکرده)، مقدار پیش‌فرض از config.py (که
خودش از .env می‌آید) برگردانده می‌شود.

این ماژول مخصوص هویت/برندینگ و اطلاعات تماس کسب‌وکار است (نام ربات، متن خوش‌آمد،
شماره کارت، کانال‌های اجباری، لینک پشتیبانی و ...) — نه اطلاعات محرمانه‌ی
اتصال به سرویس‌های ثالث (مثل رمز پنل مرزبان یا گواهی پاسارگاد) که همچنان فقط
از طریق .env تنظیم می‌شوند.
"""

import json
import logging

import config
import database as db

logger = logging.getLogger(__name__)

_PREFIX = "botinfo_"

# کلید داخلی -> (مقدار پیش‌فرض از config.py، برچسب فارسی برای پنل ادمین)
_FIELDS = {
    "welcome_text": ("👋 به ربات ما خوش آمدید!", "👋 متن خوش‌آمدگویی /start"),
    "card_number": (None, "💳 شماره کارت (برای پرداخت کارت‌به‌کارت)"),
    "card_holder": (None, "👤 نام صاحب کارت"),
    "support_url": ("", "👨‍💻 لینک پشتیبانی (آیدی/کانال تلگرام)"),
    "bot_username": (None, "🤖 یوزرنیم ربات (بدون @)"),
    "connection_guide_url": (None, "📘 لینک آموزش اتصال"),
    "order_log_channel_id": (None, "📋 آیدی عددی کانال لاگ سفارش‌ها"),
    "config_name_prefix": ("tg", "🏷 پیشوند نام کانفیگ‌های ساخته‌شده (فقط حروف/عدد انگلیسی و _)"),
}


def _default_for(key: str):
    if key == "card_number":
        return config.CARD_NUMBER
    if key == "card_holder":
        return config.CARD_HOLDER
    if key == "bot_username":
        return config.BOT_USERNAME
    if key == "connection_guide_url":
        return config.CONNECTION_GUIDE_URL
    if key == "order_log_channel_id":
        return str(config.ORDER_LOG_CHANNEL_ID)
    default, _label = _FIELDS.get(key, (None, None))
    return default


def get(key: str) -> str:
    """مقدار مؤثر فعلی یک فیلد «اطلاعات ربات» را برمی‌گرداند: اول از دیتابیس
    (اگر ادمین قبلاً از پنل ذخیره کرده)، وگرنه پیش‌فرض .env/config.py."""
    stored = db.get_setting(_PREFIX + key)
    if stored is not None and stored != "":
        return stored
    default = _default_for(key)
    return default if default is not None else ""


def set(key: str, value: str, entities: list[dict] | None = None) -> None:
    """ذخیره مقدار یک فیلد اطلاعات ربات.

    برای welcome_text، entityهای تلگرام (Bold/Link/Premium Emoji و...) هم
    جداگانه در settings ذخیره می‌شوند تا بعد از ویرایش از پنل از بین نروند.
    """
    if key not in _FIELDS:
        raise ValueError(f"فیلد نامعتبر برای اطلاعات ربات: {key}")
    db.set_setting(_PREFIX + key, value)
    if key == "welcome_text":
        db.set_setting(
            _PREFIX + key + "_entities",
            json.dumps(entities or [], ensure_ascii=False, separators=(",", ":")),
        )


def get_rich(key: str):
    """مقدار فیلد را همراه با MessageEntityهای ذخیره‌شده برمی‌گرداند."""
    from text_catalog import RichText

    value = get(key)
    if key != "welcome_text":
        return value
    raw = db.get_setting(_PREFIX + key + "_entities")
    entities = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                entities = parsed
        except Exception:
            logger.exception("خطا در خواندن entityهای متن خوش‌آمدگویی")
    return RichText(value, entities)


def labels() -> dict:
    return {k: v[1] for k, v in _FIELDS.items()}


def all_values() -> dict:
    return {k: get(k) for k in _FIELDS}


def get_support_url() -> str:
    """لینک پشتیبانی را به-صورت یک URL معتبر برای دکمهٔ شیشهٔای تلگرام برمی‌گرداند.

    🐛 فیکس: ادمین طبق برچسب این فیلد (آیدی/کانال تلگرام) اگر فقط یوزرنیم (مثلاً "@mysupport" یا "mysupport") وارد می‌کرد، بدون http/https ذخیره می‌شد و مستقیم به-عنوان url یک دکمهٔ اینلاین پاس داده می‌شد. تلگرام برای چنین urlهایی خطای BUTTON_URL_INVALID برمی‌گرداند و کل پیام (منوی پشتیبانی) با خطا مواجه می‌شد؛ این تابع همین مشکل بود.
    """
    raw = (get("support_url") or "").strip()
    if not raw:
        return "https://t.me/"
    if raw.startswith(("http://", "https://", "tg://")):
        return raw
    if raw.startswith("@"):
        raw = raw[1:]
    if raw.startswith("t.me/") or raw.startswith("telegram.me/") or raw.startswith("www."):
        return "https://" + raw
    return "https://t.me/" + raw


# ---------------------------------------------------------------------------
# کانال‌های عضویت اجباری — به‌صورت یک آرایه‌ی JSON در همان جدول settings
# ذخیره می‌شود؛ اگر ادمین چیزی تنظیم نکرده باشد، از REQUIRED_CHANNELS در
# config.py (که خودش می‌تواند از .env بیاید) استفاده می‌شود.
# ---------------------------------------------------------------------------
def get_required_channels() -> list:
    stored = db.get_setting(_PREFIX + "required_channels")
    if stored:
        try:
            parsed = json.loads(stored)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            logger.exception("خطا در خواندن required_channels ذخیره‌شده در دیتابیس")
    return config.REQUIRED_CHANNELS


def set_required_channels(channels: list) -> None:
    db.set_setting(_PREFIX + "required_channels", json.dumps(channels, ensure_ascii=False))


def add_required_channel(channel_id, name: str, url: str) -> None:
    # 🐛 فیکس: کانال‌های پیش‌فرض داخل config.py با id عددی (int) ذخیره می‌شدند، در حالی
    # که فرم افزودن از پنل ادمین همیشه channel_id را به‌صورت str (از message.text) می‌فرستاد.
    # مقایسه‌ی مستقیم c.get("id") != channel_id بین int و str همیشه True می‌شد، پس اینجا همیشه با str() مقایسه می‌کنیم.
    target = str(channel_id)
    channels = get_required_channels()
    channels = [c for c in channels if str(c.get("id")) != target]
    channels.append({"id": channel_id, "name": name, "url": url})
    set_required_channels(channels)


def remove_required_channel(channel_id) -> None:
    # 🐛 فیکس اصلی: دکمه‌ی حذف توی پنل ادمین، callback_data را به‌صورت رشته‌متن (str) می‌فرستد، درحالی
    # که کانال‌های پیش‌فرض داخل config.py با id عددی (int) ذخیره شده بودند؛ مقایسهی int != str در پایتون
    # همیشه True است و دکمه‌ی حذف هرگز هیچ کانالی را واقعاً از لیست حذف نمی‌کرد. با str() مقایسه، فارق نوع حذف می‌شود.
    target = str(channel_id)
    channels = [c for c in get_required_channels() if str(c.get("id")) != target]
    set_required_channels(channels)
