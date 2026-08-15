"""
marzban.py
کلاینت آسنکرون (aiohttp) برای پنل مرزبان (Marzban) — ساخت/تمدید/فعال‌سازی
خودکار سرویس‌های V2Ray/VPN از طریق REST API رسمی پنل مرزبان.

مستندات رسمی: https://github.com/Gozargah/Marzban (بخش /docs روی خود پنل هم Swagger دارد).

هیچ‌جای دیگری نباید مستقیم به pep.shaparak.ir/پنل مرزبان درخواست بزند؛ همه باید از همین
چند تابع استفاده کنند (مشابه پنل قبلی).

تنظیمات اتصال (MARZBAN_BASE_URL / MARZBAN_USERNAME / MARZBAN_PASSWORD) فقط از .env خوانده
می‌شوند (مثل قبلاً UNIQUEPAY_BUSINESS_TOKEN) — این مقادیر محرمانه هستند و عمداً از
پنل ادمین قابل ویرایش نیستند.
"""

import asyncio
import logging
import time

import aiohttp

from config import MARZBAN_BASE_URL, MARZBAN_USERNAME, MARZBAN_PASSWORD

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=20, connect=10)
_token_cache = {"token": None, "expires_at": 0.0}
_templates_cache = {"items": None, "expires_at": 0.0}

# 🐛 فیکس: بعضی قطعی‌های شبکه/DNS بین سرور ربات و پنل مرزبان فقط چند ثانیه طول می‌کشند؛ قبلاً حتی
# یک قطعی لحظه‌ای همین یک تلاش (بدون هیچ تلاش مجدد) را کاملاً fail می‌کرد و کاربر/ادمین
# خطای «شبکه/سرور در دسترس نیست» می‌دید با اینکه اسلاگ/پلن کاملاً درست بود (دقیقاً همان
# «planSlug ارسال‌شده» در پیام خطای ادمین). حالا فقط خطاهای واقعاً شبکه‌ای/اتصالی (نه
# پاسخ‌های واقعی 4xx/5xx خود پنل) قبل از fail نهایی چند بار با یک مکث کوتاه دوباره امتحان می‌شوند.
_MAX_PANEL_RETRIES = 2  # در مجموع تا ۳ بار تلاش (تلاش اول + ۲ تلاش مجدد)
_PANEL_RETRY_DELAY = 1.5

# 🆕 فیکس سرعت: قبلاً هر تک درخواست (حتی فقط برای گرفتن توکن) یک
# aiohttp.ClientSession کاملاً تازه می‌ساخت، یعنی هر بار یک اتصال TCP+TLS از صفر
# باز می‌شد؛ همین اصلی‌ترین عامل کند بودن (۱۰-۱۵ ثانیه) ارتباط با پنل مرزبان
# بود. حالا یک session مشترک با keep-alive نگه‌داری می‌شود تا اتصال بین
# درخواست‌های پی‌درپی دوباره استفاده شود. هیچ مسیر/متد/پیلود API تغییر نکرده —
# فقط لایه‌ی شبکه‌ی زیرین بهینه شده.
_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is not None and not _session.closed:
        return _session
    async with _session_lock:
        if _session is None or _session.closed:
            connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300, keepalive_timeout=75)
            _session = aiohttp.ClientSession(timeout=_TIMEOUT, connector=connector)
        return _session


async def _get_token():
    """توکن ادمین پنل مرزبان را می‌گیرد (و تا نزدیک انقضا کش می‌کند)."""
    if not (MARZBAN_BASE_URL and MARZBAN_USERNAME and MARZBAN_PASSWORD):
        return None, "اطلاعات اتصال پنل مرزبان (آدرس/یوزرنیم/پسورد) در .env کامل تنظیم نشده."

    now = time.monotonic()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"], None

    payload = {"username": MARZBAN_USERNAME, "password": MARZBAN_PASSWORD, "grant_type": "password"}
    for attempt in range(_MAX_PANEL_RETRIES + 1):
        try:
            session = await _get_session()
            async with session.post(f"{MARZBAN_BASE_URL}/api/admin/token", data=payload) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = None
                if resp.status != 200 or not isinstance(data, dict) or not data.get("access_token"):
                    detail = (data or {}).get("detail") if isinstance(data, dict) else None
                    return None, f"ورود به پنل مرزبان ناموفق بود ({resp.status}): {detail or 'بدون جزئیات'}"
                token = data["access_token"]
                _token_cache["token"] = token
                _token_cache["expires_at"] = now + 20 * 60
                return token, None
        except Exception:
            if attempt < _MAX_PANEL_RETRIES:
                await asyncio.sleep(_PANEL_RETRY_DELAY)
                continue
            logger.exception("خطا در اتصال به پنل مرزبان هنگام دریافت توکن")
            return None, "خطا در برقراری ارتباط با پنل مرزبان (شبکه/سرور در دسترس نیست)."


async def _request(method: str, path: str, json_body: dict | None = None, params: dict | None = None):
    token, err = await _get_token()
    if err:
        return False, None, err

    headers = {"Authorization": f"Bearer {token}"}
    data = None
    status = None
    for attempt in range(_MAX_PANEL_RETRIES + 1):
        try:
            session = await _get_session()
            async with session.request(
                method, f"{MARZBAN_BASE_URL}{path}", json=json_body, params=params, headers=headers
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = None
                status = resp.status
            break
        except Exception:
            if attempt < _MAX_PANEL_RETRIES:
                await asyncio.sleep(_PANEL_RETRY_DELAY)
                continue
            logger.exception("خطا در ارتباط با پنل مرزبان (%s %s)", method, path)
            return False, None, "خطا در برقراری ارتباط با پنل مرزبان (شبکه/سرور در دسترس نیست)."

    if status == 401:
        _token_cache["token"] = None
        return False, data, "توکن نامعتبر/منقضی بود (401). لطفاً دوباره تلاش کنید."
    if status == 404:
        return False, data, f"مورد مورد نظر در پنل مرزبان پیدا نشد (404). مسیر: {path}"
    if status == 409:
        detail = (data or {}).get("detail") if isinstance(data, dict) else None
        return False, data, f"تداخل (409): {detail or 'این نام کاربری از قبل وجود دارد.'}"
    if status >= 400:
        detail = (data or {}).get("detail") if isinstance(data, dict) else None
        return False, data, f"خطای پنل مرزبان ({status}): {detail or data or 'بدون جزئیات'}"

    return True, data, "موفق"


async def test_connection():
    """بررسی اتصال/احراز هویت — وضعیت کلی پنل را برمی‌گرداند."""
    return await _request("GET", "/api/system")


async def get_system_stats():
    return await _request("GET", "/api/system")


async def get_templates(force_refresh: bool = False):
    """لیست تمپلیت‌ها با کش کوتاه‌مدت.
    سرعت ساخت کانفیگ را بالا می‌برد چون برای هر سفارش لازم نیست دوباره لیست کامل تمپلیت‌ها از پنل خوانده شود.
    """
    now = time.monotonic()
    if (not force_refresh) and _templates_cache.get("items") is not None and now < _templates_cache.get("expires_at", 0):
        return True, _templates_cache["items"], "موفق (cache)"
    ok, data, msg = await _request("GET", "/api/user_template")
    if ok:
        _templates_cache["items"] = data
        # 🆕 فیکس سرعت: قبلاً این کش فقط ۵ دقیقه معتبر بود، یعنی هر چند دقیقه یک‌بار برای ساخت هر سرویس یک رفت‌وبرگشت
        # شبکه‌ای کامل به پنل برای گرفتن کل لیست تمپلیت‌ها اضافه می‌شد و همین یکی از عوامل کند بودن ساخت سرویس بود.
        # تمپلیت‌ها به‌ندرت تغییر می‌کنند، و در «📦 مشاهده بسته‌های مرزبان» همیشه با force_refresh
        # تازه خوانده می‌شوند؛ پس کش را به ۳۰ دقیقه افزایش دادیم تا تعداد رفت‌وبرگشت به پنل کمتر شود.
        _templates_cache["expires_at"] = now + 1800
    return ok, data, msg


async def get_template(template_id: int):
    """🆕 فیکس: برای هماهنگی و یکدستی با pasargad.py (که endpoint تک‌موردی‌اش در برخی نسخه‌ها
    404 می‌دهد)، اینجا هم به‌جای صدا زدن endpoint تک‌موردی، از روی لیست کامل تمپلیت‌ها فیلتر می‌کنیم."""
    ok, items, msg = await get_templates()
    if not ok:
        return False, None, msg
    items = items if isinstance(items, list) else []
    for it in items:
        if isinstance(it, dict) and str(it.get("id")) == str(template_id):
            return True, it, "موفق"
    # اگر کش قدیمی شده باشد یا ادمین تازه تمپلیت ساخته باشد، یک‌بار بدون کش تلاش می‌کنیم.
    ok, items, msg = await get_templates(force_refresh=True)
    if ok:
        items = items if isinstance(items, list) else []
        for it in items:
            if isinstance(it, dict) and str(it.get("id")) == str(template_id):
                return True, it, "موفق"
    return False, None, f"تمپلیت با شناسه‌ی {template_id} در لیست تمپلیت‌های پنل پیدا نشد (NOT_FOUND)."


def _proxies_for_template(template: dict) -> dict:
    inbounds = template.get("inbounds") or {}
    if inbounds:
        return {protocol: {} for protocol in inbounds.keys()}
    return {"vless": {}}


def _group_ids_for_template(template: dict) -> list:
    """🆕 فیکس: نسخه‌های جدیدتر پنل پاسارگارد (گروە v3+، و برخی نسخه‌های مرزبان/مرزنشین) دیگر از سیستم قدیمی تگ/inbounds برای تعیین اینکه کاربر به کدام استخرهای ترافیک وصل می‌شود استفاده نمی‌کند، بلکه از میدان "group_ids" استفاده می‌کند. اگر تمپلیت این میدان را داشته باشد ولی اینجا نادیده گرفته شود، کاربر ساخته‌شده هیچ گروه/استخری ندارد و کانفیگش درونش کاملاً خالی می‌ماند (همین باگی که مشتری گزارش کرده بود: اکانت ساخته می‌شد ولی هیچ کانفیگی توش نبود)."""
    group_ids = template.get("group_ids")
    if isinstance(group_ids, list) and group_ids:
        return group_ids
    groups = template.get("groups")
    if isinstance(groups, list) and groups:
        ids = []
        for g in groups:
            if isinstance(g, dict) and g.get("id") is not None:
                ids.append(g["id"])
            elif isinstance(g, (int, str)):
                ids.append(g)
        return ids
    return []


async def create_user_from_template(template_id: int, username: str, device_limit=None):
    """یک کاربر جدید در پنل مرزبان بر اساس یک تمپلیت (حجم/مدت از روی تمپلیت
    خوانده می‌شود) می‌سازد. خروجی: (ok, data, message)."""
    ok, template, msg = await get_template(template_id)
    if not ok:
        return False, None, f"دریافت اطلاعات تمپلیت ناموفق بود: {msg}"

    data_limit = template.get("data_limit") or 0
    expire_duration = template.get("expire_duration") or 0
    expire = int(time.time()) + expire_duration if expire_duration else 0

    body = {
        "username": username,
        "proxies": _proxies_for_template(template),
        "inbounds": template.get("inbounds") or {},
        "expire": expire,
        "data_limit": data_limit,
        "data_limit_reset_strategy": "no_reset",
        "status": "active",
    }
    group_ids = _group_ids_for_template(template)
    if group_ids:
        body["group_ids"] = group_ids
    # device_limit عمداً نادیده گرفته می‌شود: مرزبان (Marzban) وانیلا هیچ فیلد رسمی API برای سقف
    # تعداد دستگاه/کاربر همزمان (device limit / HWID) ندارد؛ این پارامتر فقط برای هماهنگی امضای
    # تابع با پاسارگارد (که این قابلیت را واقعاً پشتیبانی می‌کند) این‌جا پذیرفته می‌شود.
    return await _request("POST", "/api/user", json_body=body)


def _data_limit_bytes(volume_gb) -> int:
    """حجم گیگابایت را به بایت تبدیل می‌کند (0/خالی = نامحدود)."""
    try:
        gb = float(volume_gb or 0)
    except (TypeError, ValueError):
        gb = 0
    return int(gb * 1024 ** 3) if gb > 0 else 0


def _expire_from_days(days) -> int:
    """تعداد روز را به timestamp انقضا (از همین لحظه) تبدیل می‌کند (0/خالی = همیشگی)."""
    try:
        d = int(days or 0)
    except (TypeError, ValueError):
        d = 0
    return int(time.time()) + d * 86400 if d > 0 else 0


async def create_user_custom(template_id: int, username: str, volume_gb, days, device_limit=None):
    """🆕 مثل create_user_from_template ولی به‌جای خواندن حجم/مدت از روی خود تمپلیت، دقیقاً همان
    حجم/مدتی که پلن یا سفارش مشتری مشخص کرده را اعمال می‌کند؛ تمپلیت فقط برای
    تنظیمات پروتکل (inbounds/proxies یعنی اینکه این بسته از کدام استخر ترافیک بخورد)
    به کار می‌رود؛ برخلاف create_user_from_template دیگر از data_limit/expire_duration خود تمپلیت
    استفاده نمی‌کند. خروجی: (ok, data, message)."""
    ok, template, msg = await get_template(template_id)
    if not ok:
        return False, None, f"دریافت اطلاعات تمپلیت ناموفق بود: {msg}"

    body = {
        "username": username,
        "proxies": _proxies_for_template(template),
        "inbounds": template.get("inbounds") or {},
        "expire": _expire_from_days(days),
        "data_limit": _data_limit_bytes(volume_gb),
        "data_limit_reset_strategy": "no_reset",
        "status": "active",
    }
    group_ids = _group_ids_for_template(template)
    if group_ids:
        body["group_ids"] = group_ids
    # device_limit عمداً نادیده گرفته می‌شود (نگاه کنید به توضیح بالای create_user_from_template).
    return await _request("POST", "/api/user", json_body=body)


async def renew_user(username: str, template_id: int, device_limit=None):
    """حجم/مدت کاربر را بر اساس یک تمپلیت از نو تنظیم می‌کند (تمدید)."""
    ok, template, msg = await get_template(template_id)
    if not ok:
        return False, None, f"دریافت اطلاعات تمپلیت ناموفق بود: {msg}"
    data_limit = template.get("data_limit") or 0
    expire_duration = template.get("expire_duration") or 0
    expire = int(time.time()) + expire_duration if expire_duration else 0
    body = {"data_limit": data_limit, "expire": expire, "status": "active"}
    # device_limit عمداً نادیده گرفته می‌شود (نگاه کنید به توضیح بالای create_user_from_template).
    return await _request("PUT", f"/api/user/{username}", json_body=body)


async def renew_user_custom(username: str, volume_gb, days, device_limit=None):
    """🆕 تمدید با حجم/مدت دقیقی که ادمین وارد می‌کند (بدون نیاز به انتخاب تمپلیت،
    چون تمدید اصلاً پروتکل/inbounds را تغییر نمی‌دهد)."""
    body = {"data_limit": _data_limit_bytes(volume_gb), "expire": _expire_from_days(days), "status": "active"}
    # device_limit عمداً نادیده گرفته می‌شود (نگاه کنید به توضیح بالای create_user_from_template).
    return await _request("PUT", f"/api/user/{username}", json_body=body)


async def get_user(username: str):
    return await _request("GET", f"/api/user/{username}")


async def disable_user(username: str):
    return await _request("PUT", f"/api/user/{username}", json_body={"status": "disabled"})


async def enable_user(username: str):
    return await _request("PUT", f"/api/user/{username}", json_body={"status": "active"})


async def revoke_sub(username: str):
    """لینک ساب فعلی این کاربر را باطل و یک لینک/توکن ساب کاملاً جدید برایش می‌سازد (لینک قبلی دیگر کار نمی‌کند)."""
    return await _request("POST", f"/api/user/{username}/revoke_sub")


async def delete_user(username: str):
    return await _request("DELETE", f"/api/user/{username}")


def extract_link_and_username(payload: dict):
    """از پاسخ ساخت/دریافت کاربر، لینک اشتراک و یوزرنام را استخراج می‌کند.

    🐛 فیکس: قبلاً فقط کلید "subscription_url" چک می‌شد؛ برخی نسخه‌های پنل این مقدار را زیر کلیدهای دیگری برمی‌گرداند، و قبلاً هروقت این کلید پیدا نمی‌شد، لینک هرگز استخراج نمی‌شد و ربات مجبور می‌شد سرویس را به‌صورت دستی از ادمین بخواهد. حالا چند نام متداول دیگر و لیست "links" را هم بررسی می‌کند.
    """
    if not isinstance(payload, dict):
        return None, None
    link = (
        payload.get("subscription_url")
        or payload.get("sub_url")
        or payload.get("subscriptionUrl")
        or payload.get("subscription")
        or payload.get("sub")
    )
    if not link:
        links = payload.get("links")
        if isinstance(links, list) and links and isinstance(links[0], str):
            link = links[0]
    if link and isinstance(link, str) and link.startswith("/"):
        link = MARZBAN_BASE_URL + link
    username = payload.get("username")
    return link, username
