"""Rebecca multi-instance adapter.

Current Rebecca (master) uses Bearer API keys and the v2 Service/User API.
This adapter deliberately does not depend on Templates for Rebecca.
"""
from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlsplit, urlunsplit

import aiohttp

logger = logging.getLogger(__name__)
_TIMEOUT = aiohttp.ClientTimeout(total=25, connect=10)
_sessions = {}
_auth_cache = {}
_services_cache = {}
_lock = asyncio.Lock()


def _pid(p):
    return int(p["id"])


def normalize_base_url(raw: str) -> str:
    """Normalize common URLs copied from the Rebecca dashboard/API docs."""
    value = (raw or "").strip().strip('"\'`')
    if not value:
        return ""
    if not value.lower().startswith(("http://", "https://")):
        value = "https://" + value
    parts = urlsplit(value)
    path = (parts.path or "").rstrip("/")
    lower = path.lower()
    known_suffixes = (
        "/dashboard",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/api",
        "/api/v2",
        "/__rebecca_api/healthz",
    )
    for suffix in known_suffixes:
        if lower == suffix:
            path = ""
            break
    return urlunsplit((parts.scheme, parts.netloc, path, "", "")).rstrip("/")


def _clean_api_key(value: str) -> str:
    key = (value or "").strip().strip('"\'`')
    if key.lower().startswith("authorization:"):
        key = key.split(":", 1)[1].strip()
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    return key


async def _session(p):
    pid = _pid(p)
    s = _sessions.get(pid)
    if s and not s.closed:
        return s
    async with _lock:
        s = _sessions.get(pid)
        if not s or s.closed:
            s = aiohttp.ClientSession(
                timeout=_TIMEOUT,
                connector=aiohttp.TCPConnector(limit=20, ttl_dns_cache=300, keepalive_timeout=75),
            )
            _sessions[pid] = s
        return s


async def _raw_get(p, path, headers=None, params=None):
    base = normalize_base_url(p.get("base_url") or "")
    if not base:
        return False, None, 0, "Base URL خالی است."
    try:
        s = await _session(p)
        async with s.get(base + path, headers=headers, params=params) as r:
            try:
                data = await r.json(content_type=None)
            except Exception:
                data = await r.text()
            return True, data, r.status, "موفق"
    except aiohttp.InvalidURL:
        return False, None, 0, "آدرس Rebecca معتبر نیست."
    except aiohttp.ClientConnectorError as e:
        return False, None, 0, f"اتصال به Rebecca برقرار نشد: {e}"
    except asyncio.TimeoutError:
        return False, None, 0, "اتصال به Rebecca timeout شد."
    except Exception as e:
        logger.exception("Rebecca GET failed")
        return False, None, 0, f"خطا در ارتباط با Rebecca: {e}"


async def _auth_headers(p, force_refresh=False):
    key = _clean_api_key(p.get("api_key") or "")
    if not key:
        return None, "API Key برای Rebecca تنظیم نشده است. از بخش ویرایش پنل، API Key را وارد کن."
    pid = _pid(p)
    if not force_refresh:
        cached = _auth_cache.get(pid)
        if cached and cached.get("key") == key:
            return {"Authorization": f"Bearer {key}"}, None
    return {"Authorization": f"Bearer {key}"}, None


async def _req(p, method, path, json=None, params=None, retry_auth=True):
    headers, err = await _auth_headers(p)
    if err:
        return False, None, err
    base = normalize_base_url(p.get("base_url") or "")
    if not base:
        return False, None, "Base URL پنل Rebecca خالی یا نامعتبر است."
    try:
        s = await _session(p)
        async with s.request(method, base + path, json=json, params=params, headers=headers) as r:
            try:
                data = await r.json(content_type=None)
            except Exception:
                data = await r.text()
            status = r.status
        if status == 401 and retry_auth:
            _auth_cache.pop(_pid(p), None)
            return await _req(p, method, path, json=json, params=params, retry_auth=False)
        if status >= 400:
            detail = data.get("detail") if isinstance(data, dict) else data
            return False, data, f"خطای Rebecca ({status}): {detail or 'بدون جزئیات'}"
        _auth_cache[_pid(p)] = {"key": _clean_api_key(p.get("api_key") or ""), "at": time.monotonic()}
        return True, data, "موفق"
    except aiohttp.InvalidURL:
        return False, None, "Base URL پنل Rebecca معتبر نیست."
    except aiohttp.ClientConnectorError as e:
        return False, None, f"اتصال به Rebecca برقرار نشد: {e}"
    except asyncio.TimeoutError:
        return False, None, "درخواست به Rebecca timeout شد."
    except Exception as e:
        logger.exception("Rebecca request failed: %s %s", method, path)
        return False, None, f"خطا در ارتباط با Rebecca: {e}"


async def test_connection(p):
    base = normalize_base_url(p.get("base_url") or "")
    if not base:
        return False, None, "❌ Base URL خالی است."

    # Health endpoint is intentionally unauthenticated in current Rebecca.
    ok, _, status, msg = await _raw_get(p, "/__rebecca_api/healthz")
    if not ok:
        return False, None, msg
    if status != 200:
        return False, None, (
            f"❌ Base URL اشتباه است یا این آدرس Rebecca نیست (HTTP {status}).\n"
            f"آدرس صحیح معمولاً ریشه پنل است، مثل:\n{base}\n"
            "نه /dashboard و نه /api را در انتهای آدرس قرار نده."
        )

    ok, data, msg = await _req(p, "GET", "/api/system")
    if not ok:
        if "(401)" in msg or "401" in msg:
            return False, data, "❌ Base URL درست است، اما API Key معتبر نیست یا دسترسی Admin ندارد. API Key را از My Account → API Keys در Rebecca کپی کن."
        if "(403)" in msg or "403" in msg:
            return False, data, "❌ API Key معتبر است، اما سطح دسترسی آن برای مدیریت پنل کافی نیست."
        return False, data, msg
    return True, data, "✅ اتصال Rebecca و احراز هویت با API Key موفق بود."


async def get_system_stats(p):
    return await _req(p, "GET", "/api/system")


async def get_services(p, force_refresh=False):
    pid = _pid(p)
    cached = _services_cache.get(pid)
    if cached and not force_refresh and time.monotonic() < cached["exp"]:
        return True, cached["items"], "موفق (cache)"

    services = []
    offset = 0
    total = None
    try:
        for _ in range(20):
            ok, data, msg = await _req(
                p,
                "GET",
                "/api/v2/services",
                params={"offset": offset, "limit": 100},
            )
            if not ok:
                return False, None, msg
            if not isinstance(data, dict):
                return False, None, "پاسخ Serviceهای Rebecca معتبر نیست."
            batch = data.get("services") or []
            if not isinstance(batch, list):
                return False, None, "ساختار Serviceهای Rebecca معتبر نیست."
            services.extend(x for x in batch if isinstance(x, dict))
            total = data.get("total")
            if not batch or (total is not None and len(services) >= int(total)) or len(batch) < 100:
                break
            offset += len(batch)
    except Exception as e:
        logger.exception("Rebecca services list failed")
        return False, None, f"خطا در دریافت Serviceهای Rebecca: {e}"

    # A Service without hosts cannot produce a useful subscription.
    usable = [x for x in services if x.get("has_hosts") is not False and int(x.get("host_count") or 0) > 0]
    _services_cache[pid] = {"items": usable, "exp": time.monotonic() + 300}
    return True, usable, "موفق"


async def get_service(p, service_id):
    return await _req(p, "GET", f"/api/v2/services/{int(service_id)}")


# Kept for compatibility with older code; Rebecca no longer uses Templates for creation.
async def get_templates(p, force_refresh=False):
    return False, [], "Rebecca فعلی برای ساخت سرویس از Service استفاده می‌کند و Template لازم نیست."


async def get_template(p, tid):
    return False, None, "Rebecca فعلی برای ساخت سرویس از Service استفاده می‌کند و Template لازم نیست."


def _bytes(gb):
    try:
        value = float(gb or 0)
        return int(value * 1024 ** 3) if value > 0 else 0
    except Exception:
        return 0


def _expire(days):
    try:
        return int(time.time()) + int(days) * 86400 if int(days or 0) > 0 else 0
    except Exception:
        return 0


async def create_user_from_service(p, service_id, username, device_limit=None):
    return await create_user_custom(p, service_id, username, None, None, device_limit)


async def create_user_custom(p, service_id, username, volume_gb, days, device_limit=None):
    try:
        sid = int(service_id)
    except Exception:
        return False, None, "Service ID برای Rebecca معتبر نیست."
    body = {
        "username": username,
        "service_id": sid,
        "data_limit_reset_strategy": "no_reset",
    }
    if volume_gb is not None:
        body["data_limit"] = _bytes(volume_gb)
    if days is not None:
        body["expire"] = _expire(days)
    if device_limit not in (None, 0, "0"):
        # Rebecca's current v2 API exposes IP limit, not the old HWID field.
        body["ip_limit"] = int(device_limit)
    ok, data, msg = await _req(p, "POST", "/api/v2/users", json=body)
    if ok:
        return True, data, msg
    # Compatibility fallback for older Rebecca builds that expose only /api/user.
    if data is not None and "(404)" not in msg:
        return False, data, msg
    legacy = {
        "username": username,
        "service_id": sid,
        "data_limit_reset_strategy": "no_reset",
    }
    if volume_gb is not None:
        legacy["data_limit"] = _bytes(volume_gb)
    if days is not None:
        legacy["expire"] = _expire(days)
    if device_limit not in (None, 0, "0"):
        legacy["ip_limit"] = int(device_limit)
    return await _req(p, "POST", "/api/user", json=legacy)


async def get_user(p, username):
    return await _req(p, "GET", f"/api/user/{username}")


async def renew_user(p, username, service_id, device_limit=None):
    # Service ID is the mapping reference; renewal only changes quota/time.
    return await renew_user_custom(p, username, None, None, device_limit=device_limit)


async def renew_user_custom(p, username, volume_gb, days, device_limit=None):
    body = {}
    if volume_gb is not None:
        body["data_limit"] = _bytes(volume_gb)
    if days is not None:
        body["expire"] = _expire(days)
    if device_limit not in (None, 0, "0"):
        body["ip_limit"] = int(device_limit)
    if not body:
        return False, None, "هیچ مقدار جدیدی برای تمدید Rebecca تعیین نشده است."
    return await _req(p, "PUT", f"/api/v2/users/{username}", json=body)


async def disable_user(p, username):
    return await _req(p, "PUT", f"/api/user/{username}", json={"status": "disabled"})


async def enable_user(p, username):
    return await _req(p, "PUT", f"/api/user/{username}", json={"status": "active"})


async def revoke_sub(p, username):
    return await _req(p, "POST", f"/api/user/{username}/revoke_sub")


async def delete_user(p, username):
    return await _req(p, "DELETE", f"/api/user/{username}")


def extract_link_and_username(p, data):
    if not isinstance(data, dict):
        return None, None
    link = (
        data.get("subscription_url")
        or data.get("subscription")
        or data.get("sub_url")
        or data.get("sub")
        or data.get("link")
    )
    return link, data.get("username")
