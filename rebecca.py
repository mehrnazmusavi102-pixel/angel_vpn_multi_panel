"""
Rebecca panel adapter.

Rebecca exposes a REST API and documents it through Swagger/OpenAPI when DOCS=True.
The current Rebecca project is derived from the Marzban family of panels, so this
adapter intentionally follows the common /api/admin/token + /api/user contract,
while probing a small set of version differences instead of hard-coding one build.

Important HWID rule:
- We only send hwid_limit when the connected Rebecca API advertises that field in
  OpenAPI, or when a successful user response proves the field exists.
- If the bot asks for a finite HWID limit and Rebecca does not expose HWID support,
  creation/renewal fails rather than silently selling an unenforced device limit.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from config import REBECCA_BASE_URL, REBECCA_USERNAME, REBECCA_PASSWORD

logger = logging.getLogger(__name__)
_TIMEOUT = aiohttp.ClientTimeout(total=20, connect=10)
_MAX_RETRIES = 2
_RETRY_DELAY = 1.2
_token_cache = {"token": None, "expires_at": 0.0}
_templates_cache = {"items": None, "expires_at": 0.0}
_capabilities = {"hwid": None, "checked_at": 0.0}
_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()


async def _get_session():
    global _session
    if _session is not None and not _session.closed:
        return _session
    async with _session_lock:
        if _session is None or _session.closed:
            _session = aiohttp.ClientSession(
                timeout=_TIMEOUT,
                connector=aiohttp.TCPConnector(limit=20, ttl_dns_cache=300, keepalive_timeout=75),
            )
        return _session


async def _get_token():
    if not (REBECCA_BASE_URL and REBECCA_USERNAME and REBECCA_PASSWORD):
        return None, "اطلاعات اتصال پنل Rebecca در .env کامل تنظیم نشده است."
    now = time.monotonic()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"], None
    payload = {"username": REBECCA_USERNAME, "password": REBECCA_PASSWORD, "grant_type": "password"}
    for attempt in range(_MAX_RETRIES + 1):
        try:
            session = await _get_session()
            async with session.post(f"{REBECCA_BASE_URL}/api/admin/token", data=payload) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = None
                if resp.status != 200 or not isinstance(data, dict) or not data.get("access_token"):
                    detail = data.get("detail") if isinstance(data, dict) else None
                    return None, f"ورود به Rebecca ناموفق بود ({resp.status}): {detail or 'بدون جزئیات'}"
                _token_cache["token"] = data["access_token"]
                _token_cache["expires_at"] = time.monotonic() + 18 * 60
                return _token_cache["token"], None
        except Exception:
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_DELAY)
            else:
                logger.exception("Rebecca token request failed")
                return None, "خطا در برقراری ارتباط با پنل Rebecca (شبکه/سرور در دسترس نیست)."


async def _request(method: str, path: str, json_body: dict | None = None, params: dict | None = None):
    token, err = await _get_token()
    if err:
        return False, None, err
    headers = {"Authorization": f"Bearer {token}"}
    for attempt in range(_MAX_RETRIES + 1):
        try:
            session = await _get_session()
            async with session.request(method, f"{REBECCA_BASE_URL}{path}", json=json_body, params=params, headers=headers) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = None
                status = resp.status
            if status == 401:
                _token_cache["token"] = None
                return False, data, "توکن Rebecca نامعتبر/منقضی بود (401)."
            if status >= 400:
                detail = data.get("detail") if isinstance(data, dict) else None
                return False, data, f"خطای پنل Rebecca ({status}): {detail or data or 'بدون جزئیات'}"
            return True, data, "موفق"
        except Exception:
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_DELAY)
            else:
                logger.exception("Rebecca request failed: %s %s", method, path)
                return False, None, "خطا در برقراری ارتباط با پنل Rebecca (شبکه/سرور در دسترس نیست)."


async def _detect_capabilities(force=False):
    now = time.monotonic()
    if not force and _capabilities["hwid"] is not None and now - _capabilities["checked_at"] < 1800:
        return _capabilities
    ok, data, _ = await _request("GET", "/openapi.json")
    supports = False
    if ok and isinstance(data, dict):
        raw = str(data).lower()
        supports = "hwid_limit" in raw or "hwid" in raw
    _capabilities.update(hwid=supports, checked_at=now)
    return _capabilities


async def test_connection():
    ok, data, msg = await _request("GET", "/api/system")
    if not ok:
        # Some Rebecca builds may expose system stats differently; auth itself is
        # still a valid connection test, so fall back to OpenAPI.
        token, err = await _get_token()
        if err:
            return False, data, msg
        cap = await _detect_capabilities(force=True)
        return True, {"hwid_supported": bool(cap.get("hwid"))}, "اتصال و احراز هویت Rebecca موفق بود."
    cap = await _detect_capabilities()
    if isinstance(data, dict):
        data = dict(data)
        data["hwid_supported"] = bool(cap.get("hwid"))
    return True, data, "اتصال Rebecca موفق بود."


async def get_system_stats():
    return await _request("GET", "/api/system")


async def get_templates(force_refresh: bool = False):
    now = time.monotonic()
    if not force_refresh and _templates_cache["items"] is not None and now < _templates_cache["expires_at"]:
        return True, _templates_cache["items"], "موفق (cache)"
    last = None
    for path in ("/api/user_templates", "/api/user_template", "/api/user_templates/simple"):
        ok, data, msg = await _request("GET", path)
        last = (ok, data, msg)
        if ok and isinstance(data, (list, dict)):
            items = data.get("items", data) if isinstance(data, dict) else data
            if isinstance(items, list):
                _templates_cache["items"] = items
                _templates_cache["expires_at"] = now + 1800
                return True, items, msg
    return last or (False, None, "تمپلیت‌های Rebecca قابل دریافت نیستند.")


async def get_template(template_id: int):
    ok, items, msg = await get_templates()
    if not ok:
        return False, None, msg
    for item in items or []:
        if isinstance(item, dict) and str(item.get("id")) == str(template_id):
            return True, item, "موفق"
    ok, items, msg = await get_templates(force_refresh=True)
    if ok:
        for item in items or []:
            if isinstance(item, dict) and str(item.get("id")) == str(template_id):
                return True, item, "موفق"
    return False, None, f"تمپلیت Rebecca با شناسه {template_id} پیدا نشد."


def _proxies_for_template(template: dict) -> dict:
    inbounds = template.get("inbounds") or {}
    return {protocol: {} for protocol in inbounds.keys()} if inbounds else {"vless": {}}


def _group_ids_for_template(template: dict) -> list:
    group_ids = template.get("group_ids")
    if isinstance(group_ids, list):
        return group_ids
    groups = template.get("groups")
    if isinstance(groups, list):
        return [g.get("id") if isinstance(g, dict) else g for g in groups if (g.get("id") if isinstance(g, dict) else g) is not None]
    return []


def _limit(value) -> int:
    try:
        return max(int(value), 0) if value not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


def _bytes(gb) -> int:
    try:
        value = float(gb or 0)
    except (TypeError, ValueError):
        value = 0
    return int(value * 1024 ** 3) if value > 0 else 0


def _expire(days) -> int:
    try:
        value = int(days or 0)
    except (TypeError, ValueError):
        value = 0
    return int(time.time()) + value * 86400 if value > 0 else 0


async def _ensure_hwid(body: dict, device_limit, username: str):
    limit = _limit(device_limit)
    if limit <= 0:
        return True, None
    cap = await _detect_capabilities()
    if not cap.get("hwid"):
        return False, "پنل Rebecca نصب‌شده قابلیت HWID را در API خود اعلام نمی‌کند؛ برای جلوگیری از فروش اشتباه، سقف دستگاه اعمال‌نشده باقی گذاشته نشد."
    body["hwid_limit"] = limit
    return True, None


async def _verify_hwid(data: Any, username: str, requested: int):
    if requested <= 0:
        return True, None
    returned = data.get("hwid_limit") if isinstance(data, dict) else None
    if returned is None:
        ok, user, _ = await get_user(username)
        if ok and isinstance(user, dict):
            returned = user.get("hwid_limit")
    try:
        returned = int(returned) if returned is not None else None
    except (TypeError, ValueError):
        returned = None
    if returned != requested:
        return False, f"محدودیت HWID در Rebecca تأیید نشد. درخواستی: {requested} | ثبت‌شده: {returned if returned is not None else 'نامشخص'}"
    return True, None


async def create_user_from_template(template_id: int, username: str, device_limit=None):
    ok, template, msg = await get_template(template_id)
    if not ok:
        return False, None, msg
    body = {
        "username": username,
        "proxies": _proxies_for_template(template),
        "inbounds": template.get("inbounds") or {},
        "expire": int(time.time()) + int(template.get("expire_duration") or 0) if template.get("expire_duration") else 0,
        "data_limit": int(template.get("data_limit") or 0),
        "data_limit_reset_strategy": "no_reset",
        "status": "active",
    }
    groups = _group_ids_for_template(template)
    if groups:
        body["group_ids"] = groups
    requested = _limit(device_limit)
    good, err = await _ensure_hwid(body, requested, username)
    if not good:
        return False, None, err
    ok, data, msg = await _request("POST", "/api/user", json_body=body)
    if not ok:
        return ok, data, msg
    good, err = await _verify_hwid(data, username, requested)
    return (False, data, err) if not good else (ok, data, msg)


async def create_user_custom(template_id: int, username: str, volume_gb, days, device_limit=None):
    ok, template, msg = await get_template(template_id)
    if not ok:
        return False, None, msg
    body = {
        "username": username,
        "proxies": _proxies_for_template(template),
        "inbounds": template.get("inbounds") or {},
        "expire": _expire(days),
        "data_limit": _bytes(volume_gb),
        "data_limit_reset_strategy": "no_reset",
        "status": "active",
    }
    groups = _group_ids_for_template(template)
    if groups:
        body["group_ids"] = groups
    requested = _limit(device_limit)
    good, err = await _ensure_hwid(body, requested, username)
    if not good:
        return False, None, err
    ok, data, msg = await _request("POST", "/api/user", json_body=body)
    if not ok:
        return ok, data, msg
    good, err = await _verify_hwid(data, username, requested)
    return (False, data, err) if not good else (ok, data, msg)


async def renew_user(username: str, template_id: int, device_limit=None):
    ok, template, msg = await get_template(template_id)
    if not ok:
        return False, None, msg
    body = {
        "data_limit": int(template.get("data_limit") or 0),
        "expire": int(time.time()) + int(template.get("expire_duration") or 0) if template.get("expire_duration") else 0,
        "status": "active",
    }
    requested = _limit(device_limit)
    good, err = await _ensure_hwid(body, requested, username)
    if not good:
        return False, None, err
    ok, data, msg = await _request("PUT", f"/api/user/{username}", json_body=body)
    if not ok:
        return ok, data, msg
    good, err = await _verify_hwid(data, username, requested)
    return (False, data, err) if not good else (ok, data, msg)


async def renew_user_custom(username: str, volume_gb, days, device_limit=None):
    body = {"data_limit": _bytes(volume_gb), "expire": _expire(days), "status": "active"}
    requested = _limit(device_limit)
    good, err = await _ensure_hwid(body, requested, username)
    if not good:
        return False, None, err
    ok, data, msg = await _request("PUT", f"/api/user/{username}", json_body=body)
    if not ok:
        return ok, data, msg
    good, err = await _verify_hwid(data, username, requested)
    return (False, data, err) if not good else (ok, data, msg)


async def get_user(username: str):
    return await _request("GET", f"/api/user/{username}")


async def disable_user(username: str):
    return await _request("PUT", f"/api/user/{username}", json_body={"status": "disabled"})


async def enable_user(username: str):
    return await _request("PUT", f"/api/user/{username}", json_body={"status": "active"})


async def revoke_sub(username: str):
    return await _request("POST", f"/api/user/{username}/revoke_sub")


async def delete_user(username: str):
    return await _request("DELETE", f"/api/user/{username}")


def extract_link_and_username(payload: dict):
    if not isinstance(payload, dict):
        return None, None
    link = payload.get("subscription_url") or payload.get("sub_url") or payload.get("subscriptionUrl") or payload.get("subscription") or payload.get("sub")
    if not link:
        links = payload.get("links")
        if isinstance(links, list):
            for item in links:
                if isinstance(item, str) and item.startswith(("http://", "https://", "/")):
                    link = item
                    break
    if isinstance(link, str) and link.startswith("/"):
        link = REBECCA_BASE_URL + link
    return link, payload.get("username")


async def warmup_cache():
    try:
        await get_templates()
        await _detect_capabilities()
    except Exception:
        pass
