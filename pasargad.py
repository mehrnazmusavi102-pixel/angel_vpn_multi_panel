"""
pasargad.py
کلاینت آسنکرون (aiohttp) برای پنل پاسارگارد (PasarGuard) — دومین گزینه‌ی پنل
VPN پشتیبانی‌شده در کنار پنل مرزبان، برای ساخت/تمدید/فعال‌سازی خودکار
سرویس‌های V2Ray/VPN از طریق REST API رسمی پنل.

مستندات رسمی: https://github.com/PasarGuard/panel (خود پنل هم روی مسیر /docs
Swagger دارد؛ در صورت فعال بودن پرچم DOCS در تنظیمات پنل).

⚠️ توجه: پاسارگارد یک درگاه پرداخت آنلاین نیست، بلکه — دقیقاً مثل مرزبان —
یک پنل مدیریت سرویس VPN/پراکسی است. این فایل هرگز نباید با منطق پرداخت
(uniquepay.py) قاطی شود.

این ماژول عمداً دقیقاً هم‌ساختار marzban.py نوشته شده تا هر دو بتوانند از
طریق یک لایه‌ی مشترک (vpn_panel.py) به‌صورت یکسان صدا زده شوند. اگر نسخه‌ی
نصب‌شده‌ی پنل پاسارگارد شما مسیرهای متفاوتی داشت (پاسارگارد به‌سرعت در حال
تغییر است)، مسیرهای زیر را مطابق Swagger پنل خودتان (`/docs`) اصلاح کنید.

تنظیمات اتصال (PASARGAD_BASE_URL / PASARGAD_USERNAME / PASARGAD_PASSWORD) فقط
از .env خوانده می‌شوند و عمداً از پنل ادمین قابل ویرایش نیستند.
"""

import asyncio
import logging
import time

import aiohttp

from config import PASARGAD_BASE_URL, PASARGAD_USERNAME, PASARGAD_PASSWORD

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=20, connect=10)
_token_cache = {"token": None, "expires_at": 0.0}
_templates_cache = {"items": None, "expires_at": 0.0}

# 🐛 فیکس: بعضی قطعی‌های شبکه/DNS بین سرور ربات و پنل پاسارگارد فقط چند ثانیه طول می‌کشند؛ قبلاً حتی
# یک قطعی لحظه‌ای همین یک تلاش (بدون هیچ تلاش مجدد) را کاملاً fail می‌کرد و کاربر/ادمین
# خطای «شبکه/سرور در دسترس نیست» می‌دید با اینکه اسلاگ/پلن کاملاً درست بود (دقیقاً همان
# «planSlug ارسال‌شده» در پیام خطای ادمین). حالا فقط خطاهای واقعاً شبکه‌ای/اتصالی (نه
# پاسخ‌های واقعی 4xx/5xx خود پنل) قبل از fail نهایی چند بار با یک مکث کوتاه دوباره امتحان می‌شوند.
_MAX_PANEL_RETRIES = 2  # در مجموع تا ۳ بار تلاش (تلاش اول + ۲ تلاش مجدد)
_PANEL_RETRY_DELAY = 1.5

# 🆕 فیکس سرعت: قبلاً هر تک درخواست (حتی فقط برای گرفتن توکن) یک
# aiohttp.ClientSession کاملاً تازه می‌ساخت، یعنی هر بار یک اتصال TCP+TLS از صفر
# باز می‌شد؛ همین اصلی‌ترین عامل کند بودن (۱۰-۱۵ ثانیه) ارتباط با پنل پاسارگارد
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
    """توکن ادمین پنل پاسارگارد را می‌گیرد (و تا نزدیک انقضا کش می‌کند)."""
    if not (PASARGAD_BASE_URL and PASARGAD_USERNAME and PASARGAD_PASSWORD):
        return None, "اطلاعات اتصال پنل پاسارگارد (آدرس/یوزرنیم/پسورد) در .env کامل تنظیم نشده."

    now = time.monotonic()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"], None

    payload = {"username": PASARGAD_USERNAME, "password": PASARGAD_PASSWORD, "grant_type": "password"}
    for attempt in range(_MAX_PANEL_RETRIES + 1):
        try:
            session = await _get_session()
            async with session.post(f"{PASARGAD_BASE_URL}/api/admin/token", data=payload) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = None
                if resp.status != 200 or not isinstance(data, dict) or not data.get("access_token"):
                    detail = (data or {}).get("detail") if isinstance(data, dict) else None
                    return None, f"ورود به پنل پاسارگارد ناموفق بود ({resp.status}): {detail or 'بدون جزئیات'}"
                token = data["access_token"]
                _token_cache["token"] = token
                _token_cache["expires_at"] = now + 20 * 60
                return token, None
        except Exception:
            if attempt < _MAX_PANEL_RETRIES:
                await asyncio.sleep(_PANEL_RETRY_DELAY)
                continue
            logger.exception("خطا در اتصال به پنل پاسارگارد هنگام دریافت توکن")
            return None, "خطا در برقراری ارتباط با پنل پاسارگارد (شبکه/سرور در دسترس نیست)."


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
                method, f"{PASARGAD_BASE_URL}{path}", json=json_body, params=params, headers=headers
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
            logger.exception("خطا در ارتباط با پنل پاسارگارد (%s %s)", method, path)
            return False, None, "خطا در برقراری ارتباط با پنل پاسارگارد (شبکه/سرور در دسترس نیست)."

    if status == 401:
        _token_cache["token"] = None
        return False, data, "توکن نامعتبر/منقضی بود (401). لطفاً دوباره تلاش کنید."
    if status == 404:
        return False, data, f"مورد مورد نظر در پنل پاسارگارد پیدا نشد (404). مسیر: {path}"
    if status == 409:
        detail = (data or {}).get("detail") if isinstance(data, dict) else None
        return False, data, f"تداخل (409): {detail or 'این نام کاربری از قبل وجود دارد.'}"
    if status >= 400:
        detail = (data or {}).get("detail") if isinstance(data, dict) else None
        return False, data, f"خطای پنل پاسارگارد ({status}): {detail or data or 'بدون جزئیات'}"

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
    ok, data, msg = await _request("GET", "/api/user_templates")
    if ok:
        _templates_cache["items"] = data
        # 🆕 فیکس سرعت: قبلاً این کش فقط ۵ دقیقه معتبر بود، یعنی هر چند دقیقه یک‌بار برای ساخت هر سرویس یک رفت‌وبرگشت
        # شبکه‌ای کامل به پنل برای گرفتن کل لیست تمپلیت‌ها اضافه می‌شد و همین یکی از عوامل کند بودن ساخت سرویس بود.
        # تمپلیت‌ها به‌ندرت تغییر می‌کنند، و در «📦 مشاهده بسته‌های پاسارگارد» همیشه با force_refresh
        # تازه خوانده می‌شوند؛ پس کش را به ۳۰ دقیقه افزایش دادیم تا تعداد رفت‌وبرگشت به پنل کمتر شود.
        _templates_cache["expires_at"] = now + 1800
    return ok, data, msg


async def get_template(template_id: int):
    """🆕 فیکس: برخی نسخه‌های پنل پاسارگارد روی endpoint تک‌موردی /api/user_templates/{id}
    همیشه 404 می‌دهند حتی وقتی همون تمپلیت در لیست کلی وجود دارد — برای همین به‌جای
    صدا زدن مستقیم آن از روی لیست کامل تمپلیت‌ها (که مطمئناً کار می‌کند) فیلتر می‌کنیم."""
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


def _normalize_hwid_limit(device_limit) -> int:
    """مقدار HWID را به عدد صحیح تبدیل می‌کند.

    در لایه‌های داخلی ربات نام پارامتر برای سازگاری با پنل‌های مختلف
    ``device_limit`` است، اما PasarGuard در API خودش این مقدار را با کلید
    ``hwid_limit`` دریافت می‌کند.

    مقدار 0/خالی/None یعنی بدون محدودیت HWID و در این حالت کلید ارسال نمی‌شود.
    """
    try:
        limit = int(device_limit) if device_limit not in (None, "") else 0
    except (TypeError, ValueError):
        limit = 0
    return max(limit, 0)


def _apply_device_limit(body: dict, device_limit) -> int:
    """محدودیت HWID را با نام فیلد صحیح PasarGuard به payload اضافه می‌کند."""
    limit = _normalize_hwid_limit(device_limit)
    if limit > 0:
        # مهم: PasarGuard از ``hwid_limit`` استفاده می‌کند، نه ``device_limit``.
        body["hwid_limit"] = limit
    else:
        # اگر body از یک template/درخواست قبلی آمده باشد، مقدار ناخواسته باقی نماند.
        body.pop("hwid_limit", None)
    return limit


async def _request_with_hwid_verification(
    method: str,
    path: str,
    json_body: dict,
    requested_hwid: int,
    username: str,
):
    """درخواست ساخت/ویرایش کاربر را می‌فرستد و نتیجه HWID را واقعاً بررسی می‌کند.

    این کار جلوی حالتی را می‌گیرد که API پاسخ موفق بدهد ولی پنل مقدار HWID
    مورد انتظار را ذخیره نکرده باشد.
    """
    ok, data, msg = await _request(method, path, json_body=json_body)
    if not ok:
        return ok, data, msg

    if requested_hwid <= 0:
        return ok, data, msg

    returned_hwid = data.get("hwid_limit") if isinstance(data, dict) else None

    # اگر پاسخ endpoint مقدار را برنگرداند، یک GET قطعی انجام می‌دهیم.
    if returned_hwid is None:
        verify_ok, verify_data, verify_msg = await get_user(username)
        if verify_ok and isinstance(verify_data, dict):
            returned_hwid = verify_data.get("hwid_limit")
        elif not verify_ok:
            logger.warning(
                "HWID verification GET failed for user=%s: %s",
                username,
                verify_msg,
            )

    try:
        returned_hwid_int = int(returned_hwid) if returned_hwid is not None else None
    except (TypeError, ValueError):
        returned_hwid_int = None

    if returned_hwid_int != requested_hwid:
        logger.error(
            "PasarGuard HWID mismatch for user=%s: requested=%s returned=%s payload=%s",
            username,
            requested_hwid,
            returned_hwid_int,
            json_body,
        )
        return (
            False,
            data,
            f"محدودیت HWID در پاسارگارد اعمال نشد. "
            f"مقدار درخواستی: {requested_hwid} | مقدار ثبت‌شده: "
            f"{returned_hwid_int if returned_hwid_int is not None else 'نامشخص'}",
        )

    return ok, data, msg


async def create_user_from_template(template_id: int, username: str, device_limit=None):
    """یک کاربر جدید در پنل پاسارگارد بر اساس یک تمپلیت (حجم/مدت از روی تمپلیت
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
    requested_hwid = _apply_device_limit(body, device_limit)
    return await _request_with_hwid_verification(
        "POST", "/api/user", body, requested_hwid, username
    )


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
    تنظیمات پروتکل (inbounds/proxies) به کار می‌رود. خروجی: (ok, data, message)."""
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
    requested_hwid = _apply_device_limit(body, device_limit)
    return await _request_with_hwid_verification(
        "POST", "/api/user", body, requested_hwid, username
    )


async def renew_user(username: str, template_id: int, device_limit=None):
    """حجم/مدت کاربر را بر اساس یک تمپلیت از نو تنظیم می‌کند (تمدید)."""
    ok, template, msg = await get_template(template_id)
    if not ok:
        return False, None, f"دریافت اطلاعات تمپلیت ناموفق بود: {msg}"
    data_limit = template.get("data_limit") or 0
    expire_duration = template.get("expire_duration") or 0
    expire = int(time.time()) + expire_duration if expire_duration else 0
    body = {"data_limit": data_limit, "expire": expire, "status": "active"}
    requested_hwid = _apply_device_limit(body, device_limit)
    return await _request_with_hwid_verification(
        "PUT", f"/api/user/{username}", body, requested_hwid, username
    )


async def renew_user_custom(username: str, volume_gb, days, device_limit=None):
    """🆕 تمدید با حجم/مدت دقیقی که ادمین وارد می‌کند (بدون نیاز به انتخاب تمپلیت)."""
    body = {"data_limit": _data_limit_bytes(volume_gb), "expire": _expire_from_days(days), "status": "active"}
    requested_hwid = _apply_device_limit(body, device_limit)
    return await _request_with_hwid_verification(
        "PUT", f"/api/user/{username}", body, requested_hwid, username
    )


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

    🐛 فیکس: قبلاً فقط کلید "subscription_url" چک می‌شد؛ برخی نسخه‌های پنل پاسارگارد این مقدار را زیر کلیدهای دیگری برمی‌گرداند، و قبلاً هروقت این کلید پیدا نمی‌شد، لینک هرگز استخراج نمی‌شد و ربات مجبور می‌شد سرویس را به‌صورت دستی از ادمین بخواهد. حالا چند نام متداول دیگر و لیست "links" را هم بررسی می‌کند.
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
        link = PASARGAD_BASE_URL + link
    username = payload.get("username")
    return link, username
