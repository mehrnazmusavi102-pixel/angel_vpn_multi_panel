"""Unified multi-instance VPN panel layer: Marzban, PasarGuard, Rebecca.

Rebecca is Service-based on current builds: plan mappings store a Rebecca
Service ID instead of a Template ID. Marzban/PasarGuard keep their existing
Template catalog and creation behavior.
"""
import marzban_panel, pasargad_panel, rebecca_panel

PANEL_TYPE_LABELS = {"marzban": "مرزبان", "pasargad": "پاسارگارد", "rebecca": "Rebecca"}
PANEL_TYPES = list(PANEL_TYPE_LABELS)


def panel_label(p):
    return f"{PANEL_TYPE_LABELS.get(p.get('panel_type'), p.get('panel_type', '?'))} — {p.get('name') or '#'+str(p.get('id'))}"


def _client(p):
    return {"marzban": marzban_panel, "pasargad": pasargad_panel, "rebecca": rebecca_panel}.get(p.get("panel_type"))


async def test_connection(p):
    c = _client(p)
    return await c.test_connection(p) if c else (False, None, "نوع پنل نامعتبر.")


async def get_catalog(p):
    """Return mapping choices.

    Marzban/PasarGuard => existing Template catalog.
    Rebecca => current /api/v2/services catalog.
    """
    c = _client(p)
    if not c:
        return [], "نوع پنل نامعتبر."

    if p.get("panel_type") == "rebecca":
        ok, data, msg = await c.get_services(p, force_refresh=True)
        if not ok:
            return [], msg
        items = data if isinstance(data, list) else []
        out = []
        for i, service in enumerate(items):
            if not isinstance(service, dict) or service.get("id") is None:
                continue
            ref = str(service["id"])
            name = service.get("name") or f"Service {ref}"
            hosts = int(service.get("host_count") or 0)
            users = int(service.get("user_count") or 0)
            label = f"🦋 {name} | {hosts} Host | {users} User | ID: {ref}"
            out.append({"idx": i, "ref": ref, "name": name, "label": label[:64]})
        if not out:
            return [], "هیچ Service قابل استفاده‌ای در Rebecca پیدا نشد. ابتدا در Rebecca یک Service دارای حداقل یک Host فعال بساز."
        return out, "موفق"

    ok, data, msg = await c.get_templates(p, force_refresh=True)
    if not ok:
        return [], msg
    items = data if isinstance(data, list) else []
    out = []
    for i, x in enumerate(items):
        if not isinstance(x, dict) or x.get("id") is None:
            continue
        ref = str(x["id"])
        name = x.get("name") or x.get("remark") or f"Template {ref}"
        label = f"📦 {name} (id: {ref})"
        out.append({"idx": i, "ref": ref, "name": name, "label": label[:60]})
    return (out, "موفق") if out else ([], f"هیچ تمپلیتی در {panel_label(p)} پیدا نشد.")


async def create_service(p, username, remote_ref, volume_gb=None, days=None, device_limit=None):
    c = _client(p)
    if not c:
        return False, None, None, None, "نوع پنل نامعتبر."

    ref = remote_ref if p.get("panel_type") == "rebecca" else int(remote_ref)
    if volume_gb is not None or days is not None:
        ok, data, msg = await c.create_user_custom(p, ref, username, volume_gb, days, device_limit=device_limit)
    else:
        ok, data, msg = await c.create_user_from_template(p, ref, username, device_limit=device_limit)
    if not ok:
        return False, None, None, data, msg
    link, uid = c.extract_link_and_username(p, data)
    return True, link, uid or username, data, msg


async def renew_service(p, service_id, remote_ref=None, volume_gb=None, days=None, device_limit=None):
    c = _client(p)
    if not c:
        return False, None, service_id, None, "نوع پنل نامعتبر."

    if p.get("panel_type") == "rebecca":
        ok, data, msg = await c.renew_user_custom(p, service_id, volume_gb, days, device_limit=device_limit)
    elif remote_ref is not None:
        ok, data, msg = await c.renew_user(p, service_id, int(remote_ref), device_limit=device_limit)
    else:
        ok, data, msg = await c.renew_user_custom(p, service_id, volume_gb, days, device_limit=device_limit)

    if not ok:
        return False, None, service_id, data, msg
    link, uid = c.extract_link_and_username(p, data)
    return True, link, uid or service_id, data, msg


async def disable_service(p, s):
    c = _client(p); ok, d, m = await c.disable_user(p, s); return ok, m


async def enable_service(p, s):
    c = _client(p); ok, d, m = await c.enable_user(p, s); return ok, m


async def regenerate_sub_link(p, s):
    c = _client(p); ok, d, m = await c.revoke_sub(p, s)
    if not ok:
        return False, None, s, d, m
    link, uid = c.extract_link_and_username(p, d)
    return True, link, uid or s, d, m


async def delete_service(p, s):
    c = _client(p); ok, d, m = await c.delete_user(p, s); return ok, m


async def get_service_snapshot(p, s):
    c = _client(p); return await c.get_user(p, s)
