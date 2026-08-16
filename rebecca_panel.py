"""Rebecca adapter for the current v2 Service-based API.

Authentication:
- API key entered from the bot admin panel is sent as ``Authorization: Bearer``.
- Legacy/static header forms are kept as fallbacks for older Rebecca builds.

Current Rebecca master no longer allows manual inbound selection when creating a
user. A user must belong to a Service; the Service's hosts/inbounds determine the
resulting subscription/config. Therefore the bot maps plans to Rebecca Service IDs,
not Templates.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)
_TIMEOUT = aiohttp.ClientTimeout(total=20, connect=10)
_SESSIONS: dict[int, aiohttp.ClientSession] = {}
_AUTH: dict[int, dict[str, Any]] = {}
_SERVICES: dict[int, dict[str, Any]] = {}
_CAPS: dict[int, dict[str, Any]] = {}
_LOCK = asyncio.Lock()


def _pid(panel: dict) -> int:
    return int(panel["id"])


async def _session(panel: dict) -> aiohttp.ClientSession:
    pid = _pid(panel)
    session = _SESSIONS.get(pid)
    if session and not session.closed:
        return session
    async with _LOCK:
        session = _SESSIONS.get(pid)
        if not session or session.closed:
            session = aiohttp.ClientSession(
                timeout=_TIMEOUT,
                connector=aiohttp.TCPConnector(
                    limit=20, ttl_dns_cache=300, keepalive_timeout=75
                ),
            )
            _SESSIONS[pid] = session
        return session


def _api_key(panel: dict) -> str:
    return str(panel.get("api_key") or "").strip()


async def _auth_headers(panel: dict, force_refresh: bool = False):
    """Return the working authentication header(s) for this Rebecca instance.

    Current Rebecca's admin authenticator accepts API keys through the same
    Bearer authentication path as JWTs, so Bearer is intentionally tried first.
    X-API-Key/api-key remain compatibility fallbacks for older/custom builds.
    """
    pid = _pid(panel)
    key = _api_key(panel)
    cache = _AUTH.setdefault(pid, {"mode": None})

    if key:
        if not force_refresh and cache.get("mode"):
            return dict(cache["mode"]), None
        return None, [
            {"Authorization": f"Bearer {key}"},
            {"X-API-Key": key},
            {"api-key": key},
        ]

    return None, "API Key پنل Rebecca تنظیم نشده است. از پنل ادمین ربات آن را وارد کن."


async def _req(panel: dict, method: str, path: str, json: dict | None = None, params: dict | None = None):
    auth, err = await _auth_headers(panel)
    if err:
        return False, None, err

    base = str(panel.get("base_url") or "").rstrip("/")
    if not base:
        return False, None, "Base URL پنل Rebecca تنظیم نشده است."

    candidates = auth if isinstance(auth, list) else [auth]
    last_data = None
    last_status = None

    try:
        session = await _session(panel)
        for headers in candidates:
            async with session.request(
                method,
                f"{base}{path}",
                json=json,
                params=params,
                headers=headers,
            ) as response:
                try:
                    data = await response.json(content_type=None)
                except Exception:
                    data = None
                status = response.status

            last_data, last_status = data, status

            # A 401 means this auth form was rejected; try the next key form.
            if status == 401:
                continue

            if status >= 400:
                detail = data.get("detail") if isinstance(data, dict) else data
                return False, data, f"خطای Rebecca ({status}): {detail or 'بدون جزئیات'}"

            if _api_key(panel):
                _AUTH[_pid(panel)]["mode"] = dict(headers)
            return True, data, "موفق"

        detail = last_data.get("detail") if isinstance(last_data, dict) else last_data
        return False, last_data, f"احراز هویت Rebecca با API Key ناموفق بود ({last_status or 401}): {detail or 'کلید نامعتبر یا بدون دسترسی لازم'}"
    except Exception as exc:
        logger.exception("Rebecca request failed: %s %s", method, path)
        return False, None, f"خطا در ارتباط با Rebecca: {exc}"


async def test_connection(panel: dict):
    # /api/v2/services is a better real-world test because it also verifies that
    # the API key has permission to read the Service catalog used by mappings.
    ok, data, msg = await _req(panel, "GET", "/api/v2/services", params={"limit": 1})
    if ok:
        return True, data, "اتصال Rebecca و API Key با موفقیت تأیید شد."
    return False, data, msg


async def get_system_stats(panel: dict):
    return await _req(panel, "GET", "/api/system")


async def get_services(panel: dict, force_refresh: bool = False):
    """Fetch usable Rebecca Services for plan mapping.

    Current Rebecca exposes GET /api/v2/services and returns:
    {"services": [...], "total": N}.
    Only services with at least one host are returned because a service without
    hosts cannot produce a usable subscription/config for a new user.
    """
    pid = _pid(panel)
    cache = _SERVICES.get(pid)
    now = time.monotonic()
    if cache and not force_refresh and now < cache["expires_at"]:
        return True, cache["items"], "موفق (cache)"

    ok, data, msg = await _req(panel, "GET", "/api/v2/services", params={"limit": 200, "offset": 0})
    if not ok:
        return False, None, msg

    raw_items = data.get("services", []) if isinstance(data, dict) else data
    if not isinstance(raw_items, list):
        return False, None, "پاسخ Serviceهای Rebecca معتبر نیست."

    usable = []
    for item in raw_items:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        host_count = int(item.get("host_count") or 0)
        # Broken services or services without hosts cannot create a useful user.
        if host_count <= 0 or item.get("broken") is True:
            continue
        usable.append(item)

    _SERVICES[pid] = {"items": usable, "expires_at": now + 300}
    return True, usable, "موفق"


async def get_service(panel: dict, service_id: int | str):
    ok, items, msg = await get_services(panel)
    if not ok:
        return False, None, msg
    wanted = str(service_id)
    for item in items or []:
        if str(item.get("id")) == wanted:
            return True, item, "موفق"

    # Refresh once in case the service was just created/changed in Rebecca.
    ok, items, msg = await get_services(panel, force_refresh=True)
    if ok:
        for item in items or []:
            if str(item.get("id")) == wanted:
                return True, item, "موفق"
    return False, None, f"Service با شناسه {service_id} در Rebecca پیدا نشد یا Host فعال ندارد."


def _bytes(gb) -> int:
    try:
        value = float(gb or 0)
    except (TypeError, ValueError):
        value = 0
    return int(value * 1024**3) if value > 0 else 0


def _expire(days) -> int:
    try:
        value = int(days or 0)
    except (TypeError, ValueError):
        value = 0
    return int(time.time()) + value * 86400 if value > 0 else 0


def _limit(value) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


async def _build_service_user(panel: dict, service_id, username: str, volume_gb=None, days=None, device_limit=None):
    ok, service, msg = await get_service(panel, service_id)
    if not ok:
        return False, None, msg

    sid = int(service["id"])
    body: dict[str, Any] = {
        "username": username,
        "service_id": sid,
        "status": "active",
        "data_limit_reset_strategy": "no_reset",
    }

    if volume_gb is not None:
        body["data_limit"] = _bytes(volume_gb)
    if days is not None:
        body["expire"] = _expire(days)

    # Rebecca's current API exposes IP-limit rather than the old Marzban
    # hwid_limit field. Keep the plan's device limit meaningful when possible.
    # This is deliberately called ip_limit in the Rebecca payload; we do not
    # falsely claim it is an HWID field.
    limit = _limit(device_limit)
    if limit > 0:
        body["ip_limit"] = limit

    # Current master supports both /api/user and /api/v2/users; prefer v2.
    for path in ("/api/v2/users", "/api/user"):
        ok, data, msg = await _req(panel, "POST", path, json=body)
        if ok:
            return True, data, msg
        # 404/405 is naturally handled by trying the compatibility endpoint;
        # validation/auth errors are returned immediately by _req.
        if isinstance(data, dict) and data.get("detail") and "not found" not in str(data.get("detail")).lower():
            if "405" not in msg and "404" not in msg:
                return False, data, msg

    return False, data, msg


async def create_user_from_template(panel: dict, template_id, username: str, device_limit=None):
    """Compatibility alias: remote_ref is treated as a Rebecca Service ID."""
    return await _build_service_user(panel, template_id, username, None, None, device_limit)


async def create_user_custom(panel: dict, template_id, username: str, volume_gb, days, device_limit=None):
    """Compatibility alias: remote_ref is treated as a Rebecca Service ID."""
    return await _build_service_user(panel, template_id, username, volume_gb, days, device_limit)


async def get_user(panel: dict, username: str):
    return await _req(panel, "GET", f"/api/user/{username}")


async def renew_user(panel: dict, username: str, service_id, device_limit=None):
    # Service assignment is preserved on update. The mapping is validated so a
    # stale mapping cannot silently renew a user against a deleted Service.
    ok, _, msg = await get_service(panel, service_id)
    if not ok:
        return False, None, msg
    return await renew_user_custom(panel, username, None, None, device_limit)


async def renew_user_custom(panel: dict, username: str, volume_gb=None, days=None, device_limit=None):
    body: dict[str, Any] = {}
    if volume_gb is not None:
        body["data_limit"] = _bytes(volume_gb)
    if days is not None:
        body["expire"] = _expire(days)
    limit = _limit(device_limit)
    if limit > 0:
        body["ip_limit"] = limit
    return await _req(panel, "PUT", f"/api/v2/users/{username}", json=body) if body else await get_user(panel, username)


async def disable_user(panel: dict, username: str):
    # Rebecca exposes user mutation actions under /api/user/{username}/...
    return await _req(panel, "PUT", f"/api/user/{username}", json={"status": "disabled"})


async def enable_user(panel: dict, username: str):
    return await _req(panel, "PUT", f"/api/user/{username}", json={"status": "active"})


async def revoke_sub(panel: dict, username: str):
    for path in (f"/api/user/{username}/revoke_sub", f"/api/user/{username}/revoke"):
        ok, data, msg = await _req(panel, "POST", path)
        if ok:
            return ok, data, msg
    return False, data, msg


async def delete_user(panel: dict, username: str):
    return await _req(panel, "DELETE", f"/api/user/{username}")


def extract_link_and_username(panel: dict, data):
    if not isinstance(data, dict):
        return None, None
    link = (
        data.get("subscription_url")
        or data.get("subscription")
        or data.get("sub_url")
        or data.get("sub")
        or data.get("link")
    )
    # Current Rebecca may return a UserDetail with subscription_url plus username.
    return link, data.get("username")
