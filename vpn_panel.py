"""
vpn_panel.py
لایه‌ی یکپارچه‌ی پنل VPN — بین دو پنل پشتیبانی‌شده مرزبان (marzban.py) و پاسارگارد
(pasargad.py) بر اساس تنظیم «پنل فعال» (از پنل ادمین قابل‌انتخاب) یکی از این دو را
انتخاب می‌کند.

همه‌جای پروژه که لازم است یک کاربر در پنل ساخته/تمدید/غیرفعال/فعال‌شود باید از
همین‌جا (نه مستقیم از marzban.py یا pasargad.py) استفاده کند تا هروقت ادمین پنل فعال را
تغییر داد، تمام عملیات بدون هیچ تغییری دیگر به سمت پنل جدید بروند.
"""

import database as db
import config
import marzban
import pasargad
import rebecca

PANEL_PRIORITY = ("marzban", "pasargad", "rebecca")

_PANELS = {
    "marzban": marzban,
    "pasargad": pasargad,
    "rebecca": rebecca,
}

PANEL_LABELS = {
    "marzban": "مرزبان (Marzban)",
    "pasargad": "پاسارگارد (PasarGuard)",
    "rebecca": "Rebecca",
}


def available_panels() -> list:
    panels = []
    if config.MARZBAN_ENABLED:
        panels.append("marzban")
    if config.PASARGAD_ENABLED:
        panels.append("pasargad")
    if config.REBECCA_ENABLED:
        panels.append("rebecca")
    return panels


def routing_panel() -> str | None:
    """پنلی که برای عملیات عمومی بدون source صریح استفاده می‌شود.

    انتخاب دستی/سراسری پنل حذف شده است؛ در این حالت فقط از اولویت داخلی
    پنل‌های متصل استفاده می‌کنیم. عملیات روی سرویس‌های موجود باید از source
    ذخیره‌شده‌ی همان سرویس استفاده کند.
    """
    panels = available_panels()
    for name in PANEL_PRIORITY:
        if name in panels:
            return name
    return None


def _active_module():
    name = routing_panel()
    if not name:
        return None
    return _PANELS[name]


_NOT_CONNECTED_MSG = "هیچ پنل VPNی به ربات وصل نیست. از بخش «اتصال پنل VPN» در پنل ادمین یکی از پنل‌ها را وصل کنید."


async def test_connection():
    mod = _active_module()
    if mod is None:
        return False, None, _NOT_CONNECTED_MSG
    return await mod.test_connection()


async def get_system_stats():
    mod = _active_module()
    if mod is None:
        return False, None, _NOT_CONNECTED_MSG
    return await mod.get_system_stats()


async def get_templates():
    mod = _active_module()
    if mod is None:
        return False, None, _NOT_CONNECTED_MSG
    return await mod.get_templates()


async def get_template(template_id: int):
    mod = _active_module()
    if mod is None:
        return False, None, _NOT_CONNECTED_MSG
    return await mod.get_template(template_id)


async def create_user_from_template(template_id: int, username: str, device_limit=None):
    mod = _active_module()
    if mod is None:
        return False, None, _NOT_CONNECTED_MSG
    return await mod.create_user_from_template(template_id, username, device_limit=device_limit)


async def create_user_custom(template_id: int, username: str, volume_gb, days, device_limit=None):
    """🆕 مثل create_user_from_template ولی حجم/مدت را مستقیماً از پلن/سفارش می‌گیرد، نه از روی تمپلیت.
    🆕 فیکس HWID Limit: device_limit (سقف کاربر همزمان پلن) به پنل فعال پاس داده می‌شود؛ روی پنل‌هایی
    که این قابلیت را پشتیبانی نکنند (مثلاً مرزبان وانیلا) بی‌اثر نادیده گرفته می‌شود."""
    mod = _active_module()
    if mod is None:
        return False, None, _NOT_CONNECTED_MSG
    return await mod.create_user_custom(template_id, username, volume_gb, days, device_limit=device_limit)


async def renew_user(username: str, template_id: int, device_limit=None):
    mod = _active_module()
    if mod is None:
        return False, None, _NOT_CONNECTED_MSG
    return await mod.renew_user(username, template_id, device_limit=device_limit)


async def renew_user_custom(username: str, volume_gb, days, device_limit=None):
    """🆕 تمدید با حجم/مدت دقیق (بدون نیاز به انتخاب تمپلیت)."""
    mod = _active_module()
    if mod is None:
        return False, None, _NOT_CONNECTED_MSG
    return await mod.renew_user_custom(username, volume_gb, days, device_limit=device_limit)


async def get_user(username: str):
    mod = _active_module()
    if mod is None:
        return False, None, _NOT_CONNECTED_MSG
    return await mod.get_user(username)


async def disable_user(username: str):
    mod = _active_module()
    if mod is None:
        return False, None, _NOT_CONNECTED_MSG
    return await mod.disable_user(username)


async def enable_user(username: str):
    mod = _active_module()
    if mod is None:
        return False, None, _NOT_CONNECTED_MSG
    return await mod.enable_user(username)


async def revoke_sub(username: str):
    mod = _active_module()
    if mod is None:
        return False, None, _NOT_CONNECTED_MSG
    return await mod.revoke_sub(username)


async def delete_user(username: str):
    mod = _active_module()
    if mod is None:
        return False, None, _NOT_CONNECTED_MSG
    return await mod.delete_user(username)


def extract_link_and_username(payload: dict):
    mod = _active_module()
    if mod is None:
        return None, None
    return mod.extract_link_and_username(payload)


async def warmup_cache():
    """🆕 فیکس سرعت: فقط توکن ادمین و لیست تمپلیت‌های پنل فعال را از قبل در کش تازه نگه می‌دارد
    (فراخوانده‌شده از یک حلقه‌ی پس‌زمینه‌ی دوره‌ای در bot.py). با این کار
    وقتی یک مشتری درخواست ساخت سرویس می‌دهد، تقریباً همیشه هر دو کش (توکن
    و تمپلیت‌ها) از قبل گرم هستند و تنها همان یک درخواست واقعی ساخت (POST /api/user)
    باقی می‌ماند — دقیقاً همان ۲-۳ ثانیه‌ای که کاربر انتظار دارد.
    بی‌ضرر است (مثلاً پنل لحظه‌ای در دسترس نباشد) را بی‌سروصدا رد می‌کند و دفعه‌ی بعدی
    درخواست واقعی مشتری همان مسیر معمولی (توکن/تمپلیت که زمانش رسیده) را طی می‌کند.
    """
    mod = _active_module()
    if mod is None:
        return
    try:
        await mod.get_templates()
    except Exception:
        pass
