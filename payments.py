"""
payments.py
لایه‌ی درگاه پرداخت آنلاین — تنها درگاه پشتیبانی‌شده یونیک‌پی (UniquePay) است.
همه‌جای پروژه که لازم است یک فاکتور/اینویس پرداخت آنلاین بسازد (خرید پلن، بساز
سرویس خودت، شارژ کیف پول) باید از همین‌جا (نه مستقیم از uniquepay.py) استفاده کند تا
در صورت نیاز به تغییر/افزودن درگاه آنلاین دیگر در آینده، فقط همین فایل تغییر کند.

⚠️ پاسارگارد (PasarGuard) اینجا ربطی ندارد — پاسارگارد یک پنل VPN (در کنار پنل
مرزبان) است، نه یک درگاه پرداخت. برای مسیریابی پنل‌ها به vpn_panel.py مراجعه
کنید.
"""

import logging

import config
import uniquepay

logger = logging.getLogger(__name__)

DEFAULT_GATEWAY = "uniquepay"


def available_gateways() -> list:
    gateways = []
    if config.UNIQUEPAY_ENABLED:
        gateways.append("uniquepay")
    return gateways


def get_active_gateway() -> str | None:
    gateways = available_gateways()
    if not gateways:
        return None
    return gateways[0]


def online_payment_enabled() -> bool:
    return bool(available_gateways())


async def create_invoice(hash_id: str, amount: int, redirect_url: str | None = None):
    """خروجی هماهنگ با شکل موردانتظار uniquepay.create_invoice: دیکشنری با
    کلیدهای paymentLink/refId یا None در صورت شکست. فیلد 'provider' هم برمی‌گردد تا هنگام
    ذخیره در online_payments استفاده شود."""
    gateway = get_active_gateway()
    if gateway == "uniquepay":
        data = await uniquepay.create_invoice(hash_id, amount, redirect_url)
        if not data or not data.get("paymentLink"):
            return None
        return {"paymentLink": data.get("paymentLink"), "refId": data.get("refId"), "provider": "uniquepay"}

    return None


async def check_invoice(payment: dict):
    """پولینگ دستی (دکمه‌ی «بررسی کن») برای تأیید پرداخت یونیک‌پی."""
    provider = (payment or {}).get("provider") or "uniquepay"
    if provider == "uniquepay":
        return await uniquepay.check_invoice(payment["hash_id"])
    return None
