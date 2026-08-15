"""
handlers/marzban_admin.py
اتصال پنل مرزبان (Marzban Reseller VaaS) به ربات.

⚠️ نکته‌ی مهم درباره‌ی طراحی: این ماژول هیچ رفتار قبلی ربات را تغییر یا
حذف نمی‌کند مگر جایی که صراحتاً خواسته شده.

📌 قانون فعلی ارسال VIP بر اساس روش پرداخت:
- **کیف پول** و **پرداخت آنلاین (یونیک‌پی)**: اگر مرزبان فعال باشد و دسته‌بندی
  آن پلن به یک بسته‌ی مرزبان نگاشت شده باشد، سرویس بلافاصله و کاملاً خودکار
  ساخته و برای مشتری ارسال می‌شود — بدون هیچ دکمه‌ای برای انتخاب دستی/خودکار.
  اگر مرزبان غیرفعال باشد یا نگاشتی برای آن دسته‌بندی نباشد، دقیقاً مثل قبل
  به ادمین اطلاع داده می‌شود تا خودش دستی ارسال کند.
- **کارت‌به‌کارت**: تغییری نکرده — بعد از تأیید رسید توسط ادمین، همان دو دکمه‌ی
  «ارسال دستی» و «ارسال خودکار از پنل مرزبان» نشان داده می‌شود و انتخاب با
  ادمین است.

همین قانون عیناً برای «بساز سرویس خودت» هم پیاده شده (با یک نگاشت پیش‌فرض
واحد برای کل این بخش، چون حجم/مدت هر سفارش متغیر است، نه بر اساس دسته‌بندی).

⚠️  کاملاً و عمداً خارج از این ماژول نگه داشته شده: هیچ نگاشتی، هیچ
دکمه‌ی خودکاری و هیچ تماس API‌ای برای  وجود ندارد. ارسال کانفیگ 
همیشه ۱۰۰٪ دستی می‌ماند، دقیقاً مثل قبل، فارغ از روش پرداخت.

درباره‌ی اینکه «کدام دسته‌بندی از کدام ترافیک (اقتصادی/CDN) تغذیه شود»:
این تصمیم در سطح planSlug گرفته می‌شود — یعنی وقتی در پنل مرزبان یک بسته
تعریف می‌کنید، همان‌جا مشخص می‌کنید آن بسته از کدام استخر ترافیک بخورد.
اینجا (بخش «نگاشت دسته‌بندی‌ها») فقط تعیین می‌کنیم که هر دسته‌بندی ربات
(مثلاً «پرسرعت») باید کدام planSlug مرزبان را صدا بزند؛ چون آن planSlug از
قبل در خود پنل مرزبان به ترافیک درست وصل شده، همین یک نگاشت برای کنترل
کامل کافی است.
"""

import asyncio
import html
import json
import logging
import random
import re
from datetime import datetime, timedelta
from io import BytesIO

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey

import database as db
import crypto
import marzban
import vpn_panel
import panels
import fsm_storage
import bot_info
from config import MARZBAN_ENABLED, ADMIN_ID
from states import AdminStates
from keyboards import (
    admin_marzban_menu,
    marzban_back_keyboard,
    marzban_map_category_pick_keyboard,
    marzban_map_vip_category_pick_keyboard,
    marzban_map_vip_plans_keyboard,
    marzban_plan_pick_keyboard,
    config_delivery_keyboard,
    admin_panel_menu,
    main_reply_keyboard,
)
from utils import is_duplicate_action, now_tehran_naive, parse_int_in_range

def _admin_perm(*args, **kwargs):
    from handlers.admin import _admin_perm as _impl
    return _impl(*args, **kwargs)

def _is_admin(*args, **kwargs):
    from handlers.admin import _is_admin as _impl
    return _impl(*args, **kwargs)

def _log_fulfilled_order(*args, **kwargs):
    from handlers.admin import _log_fulfilled_order as _impl
    return _impl(*args, **kwargs)

router = Router(name="marzban_admin")


def _generate_service_username() -> str:
    """نام کاربری یکتا برای سرویس جدید در پنل VPN می‌سازد: پیشوند دلخواه ادمین (از بخش اطلاعات ربات) + یک کد عددی رندوم. قبل از استفاده، عدم تکراری بودنش در جدول configs بررسی می‌شود تا هرگز دو سرویس یک نام یکسان نگیرند."""
    raw_prefix = (bot_info.get("config_name_prefix") or "tg").strip()
    prefix = re.sub(r"[^A-Za-z0-9_]+", "", raw_prefix) or "tg"
    for _ in range(50):
        candidate = f"{prefix}_{random.randint(100000, 999999)}"
        if not db.is_service_id_taken(candidate):
            return candidate
    # این حالت عملاً ناممکن است و فقط تضمین یک فالبک مطمئن است
    return f"{prefix}_{int(datetime.now().timestamp())}"


def _format_volume_gb_label(volume_gb) -> str:
    if volume_gb is None:
        return "نامشخص"
    try:
        v = float(volume_gb)
    except (TypeError, ValueError):
        return str(volume_gb)
    if v <= 0:
        return "نامحدود"
    if v < 1:
        mb = round(v * 1024)
        return f"{mb} مگابایت"
    return f"{int(v) if v.is_integer() else v:g} گیگابایت"


def _actual_volume_gb_from_panel_response(data, fallback=None):
    """حجم واقعی سرویس ساخته‌شده را از پاسخ خود پنل استخراج می‌کند.

    Marzban مقدار data_limit را برحسب بایت در پاسخ ساخت کاربر برمی‌گرداند.
    بنابراین برای متن تحویلی مشتری، اولویت با مقدار واقعی ثبت‌شده در پنل است،
    نه صرفاً حجمی که از پلن ربات ارسال شده است. اگر پنل مقدار را برنگرداند،
    از fallback (حجم پلن/سفارش) استفاده می‌کنیم.
    """
    if isinstance(data, dict):
        raw = data.get("data_limit")
        if raw is not None:
            try:
                raw = int(raw)
                if raw <= 0:
                    return 0
                return raw / (1024 ** 3)
            except (TypeError, ValueError):
                pass

    return fallback
logger = logging.getLogger(__name__)

try:
    import qrcode
except ImportError:
    qrcode = None  # اگر نصب نباشد، لینک به‌جای عکس QR به‌صورت متنی فرستاده می‌شود.


def _make_qr_bytes(link: str) -> bytes:
    img = qrcode.make(link)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _pretty(data, limit: int = 1500) -> str:
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        text = str(data)
    if len(text) > limit:
        text = text[:limit] + "\n... (بریده‌شد)"
    return html.escape(text)


def _admin_fsm(bot) -> FSMContext | None:
    """FSMContext مربوط به چت خود ادمین، برای زمانی که می‌خواهیم از یک مسیر
    غیرتعاملی (مثل بعد از پرداخت کیف‌پول/آنلاین که در چت مشتری اتفاق می‌افتد)
    همان مکانیزم «لطفاً لینک رو دستی بفرست» را روی چت ادمین صدا بزنیم.
    اگر هنوز storage ثبت نشده باشد (مثلاً در تست) None برمی‌گرداند."""
    if fsm_storage.storage is None:
        return None
    return FSMContext(
        storage=fsm_storage.storage,
        key=StorageKey(bot_id=bot.id, chat_id=ADMIN_ID, user_id=ADMIN_ID),
    )


# ---------------------------------------------------------------------------
# 🤖 ارسال کاملاً خودکار بعد از پرداخت کیف‌پول/آنلاین (بدون دخالت ادمین)
# فقط VIP و «بساز سرویس خودت» —  هرگز از این مسیر عبور نمی‌کند.
# توجه: این تابع از طریق vpn_panel کار می‌کند، پس روی هرکدام از پنل مرزبان یا پاسارگارد
# (هرکدام که به‌عنوان پنل متصل انتخاب شده باشد) کار می‌کند.
# اگر هیچ پنلی متصل/فعال نباشد یا نگاشتی نباشد، False برمی‌گرداند تا مسیر همیشگی
# (اطلاع دستی به ادمین) دنبال شود و هیچ سفارشی گم نشود.
# ---------------------------------------------------------------------------
async def auto_fulfill_vip_via_marzban(bot, uid, plan_key: str, order_id: int | None) -> bool:
    if not vpn_panel.routing_panel():
        return False
    mapping = db.get_marzban_plan_map_for_plan_key(plan_key)
    if not mapping:
        return False

    plan = db.get_effective_plan(plan_key)
    user = db.get_user(uid)
    if not plan or not user:
        return False

    panel_label = vpn_panel.PANEL_LABELS.get(vpn_panel.routing_panel(), vpn_panel.routing_panel())
    username = _generate_service_username()
    # 🆕 فیکس: قبلاً اینجا create_user_from_template صدا زده می‌شد که حجم/مدت را از روی خود تمپلیت مرزبان می‌خواند — یعنی اگر تمپلیت با حجم/مدت پلن هماهنگ نبود، مشتری حجم/مدت اشتباه می‌گرفت. الان از create_user_custom استفاده می‌شود که حجم/مدت را دقیقاً از روی خود پلن (plan['volume_gb']/plan['days']) می‌گیرد؛ تمپلیت فقط برای تنظیمات پروتکل/استخر ترافیک به کار می‌رود.
    # 🆕 فیکس HWID Limit: سقف کاربر همزمان خود پلن (plan['user_limit']) همراه با ساخت سرویس به پنل فرستاده می‌شود.
    ok, data, msg = await vpn_panel.create_user_custom(
        int(mapping["plan_slug"]), username, plan.get("volume_gb"), plan.get("days"),
        device_limit=plan.get("user_limit"),
    )
    if not ok:
        await bot.send_message(
            ADMIN_ID,
            f"⚠️ خرید VIP (کیف‌پول/پرداخت آنلاین) قرار بود خودکار از پنل {panel_label} ارسال شود ولی "
            f"ساخت سرویس در پنل ناموفق بود:\n{msg}\n"
            f"🔑 planSlug ارسال‌شده: {mapping['plan_slug']}\n\n"
            "لطفاً از دکمه‌ی ارسال دستی زیر همین سفارش استفاده کن. "
            "اگه خطا NOT_FOUND بود، احتمالاً باید این نگاشت رو دوباره از منوی پنل متصل تنظیم کنی (توجه: نگاشت بسته به پنل فعلی بستگی دارد — اگر پنل متصل را عوض کردید، باید دوباره از روی همان پنل نگاشت کنید).",
        )
        return False

    link, slug = vpn_panel.extract_link_and_username(data)
    actual_volume_gb = _actual_volume_gb_from_panel_response(data, plan.get("volume_gb"))
    snapshot = {"name": plan.get("name"), "volume_gb": actual_volume_gb, "days": plan.get("days"), "user_limit": plan.get("user_limit")}
    ctx = {"uid": uid, "plan_key": plan_key, "order_id": order_id, "order_kind": "plan",
           "slug": slug, "snapshot": snapshot}

    if not link:
        await bot.send_message(
            ADMIN_ID, f"📨 پاسخ پنل {panel_label} (ارسال خودکار بعد از پرداخت):\n<pre>{_pretty(data)}</pre>",
            parse_mode="HTML",
        )
        admin_state = _admin_fsm(bot)
        if admin_state:
            await admin_state.update_data(marzban_pending_ctx=ctx)
            await admin_state.set_state(AdminStates.waiting_marzban_manual_link)
        await bot.send_message(
            ADMIN_ID,
            "⚠️ سرویس در پنل مرزبان ساخته شد (خودکار، بعد از پرداخت) ولی لینک ساب به‌صورت "
            "خودکار پیدا نشد.\nلطفاً لینک رو از پاسخ بالا کپی و همینجا برام بفرست:",
        )
        return True

    # 🆕 فیکس سرعت: دامپ خام پاسخ پنل (فقط برای رفع اشکال ادمین) با ارسال واقعی کانفیگ برای مشتری
    # کاملاً مستقل است؛ به‌جای پشت‌سرهم، همزمان اجرا می‌شوند تا مشتری منتظر این پیام‌ای
    # فقط-ادمینی نماند.
    await asyncio.gather(
        asyncio.ensure_future(bot.send_message(
            ADMIN_ID, f"📨 پاسخ پنل {panel_label} (ارسال خودکار بعد از پرداخت):\n<pre>{_pretty(data)}</pre>",
            parse_mode="HTML",
        )),
        asyncio.ensure_future(_deliver_marzban_link(bot, ctx, link)),
    )
    return True


async def auto_fulfill_custom_via_marzban(bot, user: dict, order_id: int, volume, days, custom_name) -> bool:
    """معادل تابع بالا، ولی برای سفارش‌های «بساز سرویس خودت». چون این سفارش‌ها
    دسته‌بندی ثابت ندارند (حجم/مدت دلخواه مشتری‌ست)، یک نگاشت پیش‌فرض واحد
    (scope='custom_build') استفاده می‌شود که از منوی مرزبان/اتصال پنل قابل تنظیم است،
    و از طریق vpn_panel روی هرکدام از پنل‌های مرزبان/پاسارگارد (هرکدام پنل متصل) اجرا می‌شود."""
    if not vpn_panel.routing_panel():
        return False
    mapping = db.get_marzban_plan_map("custom_build", 0)
    if not mapping:
        return False

    panel_label = vpn_panel.PANEL_LABELS.get(vpn_panel.routing_panel(), vpn_panel.routing_panel())
    username = _generate_service_username()
    # 🆕 فیکس: حجم/مدت دقیقاً همانی است که مشتری سفارش داده (volume/days)، نه از روی تمپلیت نگاشت‌شده.
    ok, data, msg = await vpn_panel.create_user_custom(int(mapping["plan_slug"]), username, volume, days)
    if not ok:
        await bot.send_message(
            ADMIN_ID,
            f"⚠️ سفارش «بساز سرویس خودت» (کیف‌پول/پرداخت آنلاین) قرار بود خودکار از پنل {panel_label} ارسال "
            f"شود ولی ساخت سرویس ناموفق بود:\n{msg}\n"
            f"🔑 planSlug ارسال‌شده: {mapping['plan_slug']}\n\n"
            "لطفاً از دکمه‌ی ارسال دستی این سفارش استفاده کن. "
            "اگه خطا NOT_FOUND بود، از «🧩 نگاشت پیش‌فرض بساز سرویس خودت» دوباره یه بسته‌ی معتبر از روی همان پنل فعلی انتخاب کن.",
        )
        return False

    link, slug = vpn_panel.extract_link_and_username(data)
    snapshot = {"name": custom_name or "سرویس سفارشی", "volume_gb": volume, "days": days}
    ctx = {"uid": user["telegram_id"], "plan_key": None, "order_id": order_id, "order_kind": "custom",
           "slug": slug, "snapshot": snapshot}

    if not link:
        await bot.send_message(
            ADMIN_ID, f"📨 پاسخ پنل {panel_label} (ارسال خودکار بعد از پرداخت):\n<pre>{_pretty(data)}</pre>",
            parse_mode="HTML",
        )
        admin_state = _admin_fsm(bot)
        if admin_state:
            await admin_state.update_data(marzban_pending_ctx=ctx)
            await admin_state.set_state(AdminStates.waiting_marzban_manual_link)
        await bot.send_message(
            ADMIN_ID,
            "⚠️ سرویس در پنل مرزبان ساخته شد (خودکار، بعد از پرداخت) ولی لینک ساب به‌صورت "
            "خودکار پیدا نشد.\nلطفاً لینک رو از پاسخ بالا کپی و همینجا برام بفرست:",
        )
        return True

    # 🆕 فیکس سرعت: دامپ خام پاسخ پنل (فقط برای رفع اشکال ادمین) با ارسال واقعی کانفیگ برای مشتری
    # کاملاً مستقل است؛ به‌جای پشت‌سرهم، همزمان اجرا می‌شوند تا مشتری منتظر این پیام‌ای
    # فقط-ادمینی نماند.
    await asyncio.gather(
        asyncio.ensure_future(bot.send_message(
            ADMIN_ID, f"📨 پاسخ پنل {panel_label} (ارسال خودکار بعد از پرداخت):\n<pre>{_pretty(data)}</pre>",
            parse_mode="HTML",
        )),
        asyncio.ensure_future(_deliver_marzban_link(bot, ctx, link)),
    )
    return True


async def _fetch_plan_choices() -> tuple[list[dict], str]:
    """لیست تمپلیت‌های کاربر (User Template) پنل مرزبان را می‌گیرد و به همان
    شکل choices قبلی (idx/slug/name/label) تبدیل می‌کند؛ اینجا slug همان
    شناسه‌ی عددی تمپلیت (template_id) به‌صورت رشته است."""
    ok, data, msg = await vpn_panel.get_templates()
    if not ok:
        return [], msg
    items = data if isinstance(data, list) else []
    choices = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        template_id = it.get("id")
        if template_id is None:
            continue
        name = it.get("name") or f"template-{template_id}"
        label = f"📦 {name} (ID: {template_id})"
        if len(label) > 60:
            label = label[:57] + "..."
        choices.append({"idx": i, "slug": str(template_id), "name": name, "label": label})
    if not choices:
        return [], "هیچ تمپلیتی در پنل مرزبان پیدا نشد. ابتدا از پنل مرزبان یک «User Template» بسازید."
    return choices, "موفق"


# ---------------------------------------------------------------------------
# 📋 صفحه‌ی اصلی
# ---------------------------------------------------------------------------
def _marzban_hub_warn() -> str:
    """هشدار وضعیت اتصال — بر اساس پنل واقعاً فعال (مرزبان یا پاسارگارد)، نه
    فقط مرزبان؛ چون این هاب برای هرکدام از دو پنل که فعال باشد کار می‌کند."""
    panel = vpn_panel.routing_panel()
    if panel:
        label = vpn_panel.PANEL_LABELS.get(panel, panel)
        return f"\n\n✅ پنل متصل فعلی: {label}"
    return "\n\n⚠️ هیچ پنل VPNی وصل نیست. اول از «🔗 اتصال پنل مرزبان» یا «🛡️ اتصال پنل پاسارگارد» یکی رو وصل کن."


@router.callback_query(F.data == "admin_marzban")
async def open_marzban_menu(callback: types.CallbackQuery):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    warn = _marzban_hub_warn()
    await callback.message.edit_text(f"📦 نگاشت پلن‌ها به پنل متصل{warn}", reply_markup=admin_marzban_menu())
    await callback.answer()


@router.message(F.text == "📦 نگاشت پلن‌ها به پنل متصل")
async def menu_admin_marzban(message: types.Message):
    if not _admin_perm(message.from_user.id, "vpn_panel"):
        return
    warn = _marzban_hub_warn()
    await message.answer(f"📦 نگاشت پلن‌ها به پنل متصل{warn}", reply_markup=admin_marzban_menu())


@router.callback_query(F.data == "marzban_test")
async def marzban_test(callback: types.CallbackQuery):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    await callback.answer("⏳ در حال تست اتصال...")
    ok, data, msg = await vpn_panel.test_connection()
    if not ok:
        await callback.message.answer(f"❌ اتصال ناموفق: {msg}", reply_markup=marzban_back_keyboard())
        return
    await callback.message.answer(
        f"✅ اتصال برقرار است.\n<pre>{_pretty(data)}</pre>",
        parse_mode="HTML", reply_markup=marzban_back_keyboard(),
    )


@router.callback_query(F.data == "marzban_traffic")
async def marzban_traffic(callback: types.CallbackQuery):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    await callback.answer("⏳ در حال دریافت اطلاعات ترافیک...")
    ok, data, msg = await vpn_panel.get_system_stats()
    if not ok:
        await callback.message.answer(f"❌ خطا: {msg}", reply_markup=marzban_back_keyboard())
        return
    await callback.message.answer(
        f"🚦 ترافیک/مصرف برند:\n<pre>{_pretty(data)}</pre>",
        parse_mode="HTML", reply_markup=marzban_back_keyboard(),
    )


@router.callback_query(F.data == "marzban_plans")
async def marzban_plans(callback: types.CallbackQuery):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    await callback.answer("⏳ در حال دریافت بسته‌ها...")
    choices, msg = await _fetch_plan_choices()
    if not choices:
        await callback.message.answer(f"❌ {msg}", reply_markup=marzban_back_keyboard())
        return
    panel_label = vpn_panel.PANEL_LABELS.get(vpn_panel.routing_panel(), vpn_panel.routing_panel())
    lines = [f"• {c['name']}\n  slug: <code>{html.escape(c['slug'])}</code>" for c in choices]
    text = f"📦 بسته‌های فعال برند در پنل {panel_label}:\n\n" + "\n\n".join(lines)
    text += (
        "\n\n💡 این‌که هر بسته از کدام ترافیک (اقتصادی/CDN و ...) تغذیه می‌شود در خود "
        "پنل مرزبان هنگام ساخت بسته مشخص شده؛ اینجا فقط برای انتخاب/نگاشت نمایش داده می‌شود."
    )
    await callback.message.answer(text[:4000], parse_mode="HTML", reply_markup=marzban_back_keyboard())


# ---------------------------------------------------------------------------
# 🗂 نگاشت دسته‌بندی VIP → planSlug مرزبان
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "marzban_map_vip")
async def marzban_map_vip(callback: types.CallbackQuery):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    cats = db.get_vip_categories()
    if not cats:
        await callback.answer("هنوز هیچ دسته‌بندی VIP‌ای ساخته نشده.", show_alert=True)
        return
    await callback.message.edit_text(
        "🗂 یک دسته‌بندی VIP رو انتخاب کن تا پلن‌های داخلش رو ببینی و برای هرکدوم "
        "جداگانه بسته‌ی متناظرش در مرزبان رو مشخص کنی (چون هر پلن حجم/مدت خودش رو داره):",
        reply_markup=marzban_map_vip_category_pick_keyboard(cats),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("marzbanmapvipcat_"))
async def marzban_map_vip_cat_pick(callback: types.CallbackQuery):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    cat_id = int(callback.data.replace("marzbanmapvipcat_", ""))
    plans = db.get_vip_plans(cat_id)
    if not plans:
        await callback.answer("این دسته‌بندی هنوز هیچ پلنی نداره.", show_alert=True)
        return
    await callback.message.edit_text(
        "یک پلن رو انتخاب کن تا بسته‌ی متناظرش در مرزبان رو مشخص کنی "
        "(هر پلن با حجم/مدت خودش باید به بسته‌ی درست وصل بشه):",
        reply_markup=marzban_map_vip_plans_keyboard(cat_id, plans),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("marzbanmapvipplan_"))
async def marzban_map_vip_plan_pick(callback: types.CallbackQuery, state: FSMContext):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    raw = callback.data.replace("marzbanmapvipplan_", "")
    cat_id_str, _, plan_id_str = raw.partition("_")
    if not cat_id_str.isdigit() or not plan_id_str.isdigit():
        await callback.answer("❌ خطای داخلی.", show_alert=True)
        return

    await callback.answer("⏳ در حال دریافت بسته‌های مرزبان...")
    choices, msg = await _fetch_plan_choices()
    if not choices:
        await callback.message.answer(f"❌ {msg}", reply_markup=marzban_back_keyboard())
        return

    # از scope اختصاصی "vip_plan" با scope_id = id دقیق همین پلن استفاده می‌کنیم
    # (نه category_id) تا هر پلن، مستقل از بقیه‌ی پلن‌های همون دسته، بسته‌ی
    # متناظر خودش رو داشته باشه.
    await state.update_data(marzban_map_scope="vip_plan", marzban_map_scope_id=int(plan_id_str),
                             marzban_map_choices=choices)
    await callback.message.answer(
        "یک بسته‌ی مرزبان رو انتخاب کن تا به این پلن مشخص وصل بشه:",
        reply_markup=marzban_plan_pick_keyboard(choices, "marzbanmapplan"),
    )


async def _open_custom_build_mapping(chat_id: int, bot, state: FSMContext):
    choices, msg = await _fetch_plan_choices()
    if not choices:
        await bot.send_message(chat_id, f"❌ {msg}", reply_markup=marzban_back_keyboard())
        return
    await state.update_data(marzban_map_choices=choices)
    await bot.send_message(
        chat_id,
        "🧩 بسته‌ی پیش‌فرض سفارش‌های «سرویس خودت رو بساز» را از پنل متصل انتخاب کن:",
        reply_markup=marzban_plan_pick_keyboard(choices, "marzbanmapcustombuild"),
    )


@router.callback_query(F.data == "marzban_map_custom_build")
async def marzban_map_custom_build(callback: types.CallbackQuery, state: FSMContext):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await callback.answer("⏳ در حال دریافت بسته‌های پنل متصل...")
    await _open_custom_build_mapping(callback.message.chat.id, callback.bot, state)


@router.message(F.text == "🗂 نگاشت سرویس خودت رو بساز")
async def marzban_map_custom_build_message(message: types.Message, state: FSMContext):
    if not _admin_perm(message.from_user.id, "vpn_panel"):
        return
    await message.answer("⏳ در حال دریافت بسته‌های پنل متصل...")
    await _open_custom_build_mapping(message.chat.id, message.bot, state)


@router.callback_query(F.data.startswith("marzbanmapcustombuild_"))
async def marzban_map_custom_build_set(callback: types.CallbackQuery, state: FSMContext):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    idx = int(callback.data.replace("marzbanmapcustombuild_", ""))
    data = await state.get_data()
    choices = data.get("marzban_map_choices") or []
    chosen = next((c for c in choices if c["idx"] == idx), None)
    if not chosen:
        await callback.answer("❌ این انتخاب منقضی شده؛ دوباره از منو وارد شو.", show_alert=True)
        return

    db.set_marzban_plan_map("custom_build", 0, chosen["slug"], chosen["name"])
    await callback.message.edit_text(
        f"✅ ذخیره شد: از این پس سفارش‌های «بساز سرویس خودت» (کیف‌پول/آنلاین) با بسته‌ی "
        f"«{chosen['name']}» (slug: {chosen['slug']}) در پنل مرزبان ساخته می‌شوند.",
        reply_markup=admin_marzban_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "marzban_map_free_test")
async def marzban_map_free_test(callback: types.CallbackQuery, state: FSMContext):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    await callback.answer("⏳ در حال دریافت بسته‌های مرزبان...")
    choices, msg = await _fetch_plan_choices()
    if not choices:
        await callback.message.answer(f"❌ {msg}", reply_markup=marzban_back_keyboard())
        return
    await state.update_data(marzban_map_choices=choices)
    await callback.message.answer(
        "🧪 این بسته برای همه‌ی سفارش‌های «تست رایگان» (۱ گیگ/۷ روزه) استفاده خواهد شد. "
        "یه بسته‌ی کوچیک و مناسب برای تست انتخاب کن:",
        reply_markup=marzban_plan_pick_keyboard(choices, "marzbanmapfreetest"),
    )


@router.callback_query(F.data.startswith("marzbanmapfreetest_"))
async def marzban_map_free_test_set(callback: types.CallbackQuery, state: FSMContext):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    idx = int(callback.data.replace("marzbanmapfreetest_", ""))
    data = await state.get_data()
    choices = data.get("marzban_map_choices") or []
    chosen = next((c for c in choices if c["idx"] == idx), None)
    if not chosen:
        await callback.answer("❌ این انتخاب منقضی شده؛ دوباره از منو وارد شو.", show_alert=True)
        return

    db.set_marzban_plan_map("free_test", 0, chosen["slug"], chosen["name"])
    await callback.message.edit_text(
        f"✅ ذخیره شد: از این پس سفارش‌های «تست رایگان» با بسته‌ی "
        f"«{chosen['name']}» (slug: {chosen['slug']}) در پنل مرزبان ساخته می‌شوند.",
        reply_markup=admin_marzban_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("marzbanmapcat_"))
async def marzban_map_cat_pick(callback: types.CallbackQuery, state: FSMContext):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    raw = callback.data.replace("marzbanmapcat_", "", 1)
    scope, _, cat_id_str = raw.rpartition("_")
    if not scope or not cat_id_str.isdigit():
        await callback.answer("❌ خطای داخلی.", show_alert=True)
        return
    cat_id = int(cat_id_str)

    await callback.answer("⏳ در حال دریافت بسته‌های مرزبان...")
    choices, msg = await _fetch_plan_choices()
    if not choices:
        await callback.message.answer(f"❌ {msg}", reply_markup=marzban_back_keyboard())
        return

    await state.update_data(marzban_map_scope=scope, marzban_map_scope_id=cat_id, marzban_map_choices=choices)
    await callback.message.answer(
        "یک بسته‌ی مرزبان رو انتخاب کن تا به این دسته‌بندی وصل بشه:",
        reply_markup=marzban_plan_pick_keyboard(choices, "marzbanmapplan"),
    )


@router.callback_query(F.data.startswith("marzbanmapplan_"))
async def marzban_map_plan_set(callback: types.CallbackQuery, state: FSMContext):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    idx = int(callback.data.replace("marzbanmapplan_", ""))
    data = await state.get_data()
    choices = data.get("marzban_map_choices") or []
    scope = data.get("marzban_map_scope")
    scope_id = data.get("marzban_map_scope_id")
    chosen = next((c for c in choices if c["idx"] == idx), None)
    if not chosen or not scope or scope_id is None:
        await callback.answer("❌ این انتخاب منقضی شده؛ دوباره از منو وارد شو.", show_alert=True)
        return

    db.set_marzban_plan_map(scope, int(scope_id), chosen["slug"], chosen["name"])
    await callback.message.edit_text(
        f"✅ ذخیره شد: از این پس این دسته‌بندی از بسته‌ی «{chosen['name']}» (slug: {chosen['slug']}) "
        "در پنل مرزبان استفاده می‌کند.",
        reply_markup=admin_marzban_menu(),
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# 📤 ارسال خودکار بعد از تأیید رسید (فقط VIP — بر اساس دسته‌بندی نگاشت‌شده.
#  عمداً پشتیبانی نمی‌شود؛ همیشه ۱۰۰٪ دستی می‌ماند)
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("marzbansend|"))
async def marzban_send_service(callback: types.CallbackQuery, state: FSMContext):
    """ارسال خودکار سفارش بر اساس نگاشت چندپنلی Bomb-style.

    نام callback قدیمی برای سازگاری حفظ شده، اما دیگر به routing_panel() یا
    marzban_plan_map وابسته نیست و از panel_plan_map استفاده می‌کند.
    """
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    if is_duplicate_action(f"mappedsend_{callback.data}"):
        await callback.answer("⚠️ این عملیات چند لحظه پیش انجام شد.", show_alert=True)
        return

    try:
        _, uid, plan_key, order_id_str = callback.data.split("|", 3)
        order_id = int(order_id_str) if order_id_str and order_id_str != "0" else None
    except Exception:
        await callback.answer("❌ اطلاعات سفارش نامعتبر است.", show_alert=True)
        return

    plan = db.get_effective_plan(plan_key)
    user = db.get_user(uid)
    mapping = db.get_panel_map_for_plan_key(plan_key)
    if not plan or not user:
        await callback.answer("❌ کاربر یا پلن یافت نشد.", show_alert=True)
        return
    if not mapping or not mapping.get("enabled"):
        await callback.answer("❌ برای این پلن پنل/بسته‌ای در نگاشت چندپنلی پیدا نشد.", show_alert=True)
        return

    panel = db.get_vpn_panel(mapping.get("panel_id"))
    if not panel or not panel.get("enabled"):
        await callback.answer("❌ پنل نگاشت‌شده فعال نیست یا حذف شده است.", show_alert=True)
        return

    await callback.answer(f"⏳ در حال ساخت سرویس از {panels.panel_label(panel)}...")

    if order_id:
        current = db.get_order(order_id)
        if current and current.get("status") == "fulfilled":
            await callback.message.answer("⚠️ این سفارش قبلاً تحویل شده است.")
            return
        if current and current.get("status") == "processing":
            await callback.message.answer("⏳ این سفارش در حال پردازش است؛ دوباره ارسال نکنید.")
            return
        if not db.claim_order_for_processing(order_id):
            fresh = db.get_order(order_id)
            if fresh and fresh.get("status") == "fulfilled":
                await callback.message.answer("⚠️ این سفارش قبلاً تحویل شده است.")
            else:
                await callback.message.answer("⏳ این سفارش توسط عملیات دیگری در حال پردازش است.")
            return

    username = _generate_service_username()
    ok, link, remote_id, data, msg = await panels.create_service(
        panel, username, mapping["remote_ref"],
        volume_gb=plan.get("volume_gb"),
        days=plan.get("days"),
        device_limit=plan.get("user_limit"),
    )

    if not ok:
        if order_id:
            db.set_order_status(order_id, "paid")
        await callback.message.answer(
            f"❌ ساخت خودکار از {panels.panel_label(panel)} ناموفق بود:\n{msg}\n\n"
            f"🔑 مرجع نگاشت: <code>{html.escape(str(mapping.get('remote_ref')))}</code>",
            parse_mode="HTML",
        )
        return

    if not link:
        if order_id:
            db.set_order_status(order_id, "paid")
        await callback.message.answer(
            f"⚠️ سرویس در {panels.panel_label(panel)} ساخته شد، اما لینک از پاسخ پنل استخراج نشد.\n"
            f"<pre>{_pretty(data)}</pre>", parse_mode="HTML"
        )
        return

    ctx = {
        "uid": uid, "plan_key": plan_key, "order_id": order_id,
        "order_kind": "plan", "panel": panel, "service_id": remote_id,
        "snapshot": {
            "name": plan.get("name"), "volume_gb": plan.get("volume_gb"),
            "days": plan.get("days"), "user_limit": plan.get("user_limit"),
        },
    }
    await _deliver_mapped_panel_link(callback.bot, ctx, link)


@router.callback_query(F.data.startswith("marzbancustomauto_"))
async def marzban_custom_auto_retry(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    try:
        order_id = int(callback.data.replace("marzbancustomauto_", ""))
    except ValueError:
        await callback.answer("❌ شناسه سفارش نامعتبر است.", show_alert=True)
        return
    order = db.get_custom_order(order_id)
    if not order or order.get("status") != "paid":
        await callback.answer("⚠️ این سفارش دیگر در صف ساخت نیست.", show_alert=True)
        return
    user = db.get_user_by_id(order["user_id"])
    if not user:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return
    await callback.answer("⏳ در حال تلاش برای ساخت خودکار از پنل...")
    try:
        handled = await auto_fulfill_custom_via_marzban(
            callback.bot, user, order_id, order.get("volume_gb"), order.get("days"), order.get("custom_name")
        )
    except Exception:
        logger.exception("manual auto retry failed for custom order %s", order_id)
        handled = False
    if handled:
        await callback.message.edit_text("✅ سفارش سفارشی به‌صورت خودکار از پنل ساخته/ارسال شد.")
    else:
        await callback.message.answer("⚠️ ساخت خودکار ناموفق بود؛ از گزینه‌ی ارسال دستی استفاده کنید.")


@router.callback_query(F.data.startswith("marzbancustom_"))
async def marzban_custom_start(callback: types.CallbackQuery, state: FSMContext):
    # 🐛 فیکس: همین باگ که در marzban_send_service بالا توضیح داده شد — دکمه‌ی «🚀 ساخت خودکار از پنل متصل» کنار دکمه‌ی دستی «📤
    # شروع ارسال کانفیگ — دستی» (sendcustomorder_) نمایش داده می‌شود و هر دو برای همان ادمین‌های فرعی
    # مسئول پیگیری سفارشات (مجوز requests) فرستاده می‌شود. قبلاً این دکمه مجوز "vpn_panel" می‌خواست
    # (جداگانه از مجوز دکمه‌ی دستی کنارش)، برای همین برای ادمین فرعیای که فقط مجوز پیگیری
    # سفارشات داشت، این دکمه کاملاً بی‌پاسخ می‌ماند (بدون هیچ پیام/پاسخی به کاربر)؛ از نظر کاربر
    # دقیقاً همان «این دکمه کار نمی‌کند» بود. حالا مثل دکمه‌ی دستی، فقط _is_admin چک می‌شود.
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    order_id = int(callback.data.replace("marzbancustom_", ""))
    order = db.get_custom_order(order_id)
    if order is None:
        await callback.answer("❌ سفارش یافت نشد.", show_alert=True)
        return

    await callback.answer("⏳ در حال دریافت بسته‌های مرزبان...")
    choices, msg = await _fetch_plan_choices()
    if not choices:
        await callback.message.answer(f"❌ {msg}", reply_markup=marzban_back_keyboard())
        return

    await state.update_data(marzban_custom_order_id=order_id, marzban_map_choices=choices)
    await callback.message.answer(
        f"سفارش «بساز سرویس خودت» #{order_id} — {order['volume_gb']} گیگ / {order['days']} روز\n"
        "نزدیک‌ترین بسته‌ی مرزبان رو انتخاب کن:",
        reply_markup=marzban_plan_pick_keyboard(choices, "marzbancustompick"),
    )


@router.callback_query(F.data.startswith("marzbancustompick_"))
async def marzban_custom_pick(callback: types.CallbackQuery, state: FSMContext):
    # 🐛 فیکس: مطابق marzban_custom_start بالا — فقط _is_admin چک می‌شود.
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    idx = int(callback.data.replace("marzbancustompick_", ""))
    data_state = await state.get_data()
    choices = data_state.get("marzban_map_choices") or []
    order_id = data_state.get("marzban_custom_order_id")
    chosen = next((c for c in choices if c["idx"] == idx), None)
    order = db.get_custom_order(order_id) if order_id else None
    if not chosen or not order:
        await callback.answer("❌ این انتخاب منقضی شده؛ دوباره تلاش کن.", show_alert=True)
        return
    user = db.get_user_by_id(order["user_id"])
    if not user:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return

    await callback.answer("⏳ در حال ساخت سرویس در پنل مرزبان...")
    username = _generate_service_username()
    # 🆕 فیکس: حجم/مدت دقیقاً از روی خود سفارش (order['volume_gb']/order['days']) گرفته می‌شود؛ تمپلیت انتخاب‌شده فقط برای تعیین پروتکل/استخر استفاده می‌شود.
    ok, data, msg = await vpn_panel.create_user_custom(int(chosen["slug"]), username, order["volume_gb"], order["days"])
    if not ok:
        await callback.message.answer(
            f"❌ ساخت سرویس در پنل مرزبان ناموفق بود: {msg}\n"
            f"🔑 planSlug ارسال‌شده: <code>{html.escape(chosen['slug'])}</code>",
            parse_mode="HTML",
        )
        return

    link, slug = vpn_panel.extract_link_and_username(data)
    actual_volume_gb = _actual_volume_gb_from_panel_response(data, order["volume_gb"])
    snapshot = {"name": order.get("custom_name") or "سرویس سفارشی",
                "volume_gb": actual_volume_gb, "days": order["days"]}
    ctx = {"uid": user["telegram_id"], "plan_key": None, "order_id": order_id, "order_kind": "custom",
           "slug": slug, "snapshot": snapshot}

    if not link:
        await callback.message.answer(f"📨 پاسخ پنل مرزبان:\n<pre>{_pretty(data)}</pre>", parse_mode="HTML")
        await state.update_data(marzban_pending_ctx=ctx)
        await state.set_state(AdminStates.waiting_marzban_manual_link)
        await callback.message.answer(
            "⚠️ سرویس در پنل مرزبان ساخته شد ولی نتونستم لینک ساب رو خودکار پیدا کنم.\n"
            "لطفاً لینک ساب رو از پاسخ بالا کپی و همینجا ارسال کن:"
        )
        return

    # 🆕 فیکس سرعت: دامپ خام پاسخ پنل را همزمان با ارسال واقعی کانفیگ برای مشتری اجرا می‌کنیم
    # تا مشتری منتظر پیام فقط-ادمینی نماند.
    await asyncio.gather(
        asyncio.ensure_future(callback.message.answer(f"📨 پاسخ پنل مرزبان:\n<pre>{_pretty(data)}</pre>", parse_mode="HTML")),
        asyncio.ensure_future(_deliver_marzban_link(callback.bot, ctx, link)),
    )


@router.message(AdminStates.waiting_marzban_manual_link)
async def marzban_manual_link_received(message: types.Message, state: FSMContext):
    link = (message.text or "").strip()
    if not link.lower().startswith(("http://", "https://")):
        await message.answer("❌ این یک لینک معتبر نیست؛ لطفاً لینک ساب رو با http یا https ارسال کن:")
        return
    data = await state.get_data()
    ctx = data.get("marzban_pending_ctx")
    if not ctx:
        await message.answer("❌ مشکلی پیش آمد؛ لطفاً از ابتدا دکمه‌ی ارسال خودکار رو دوباره بزن.")
        await state.clear()
        return
    await _deliver_marzban_link(message.bot, ctx, link)
    await state.clear()


async def _deliver_marzban_link(bot, ctx: dict, link: str):
    """سرویس ساخته‌شده از طریق مرزبان را در دیتابیس ذخیره و برای کاربر ارسال می‌کند
    (دقیقاً همان قالب/تجربه‌ی ارسال دستی، فقط بدون نیاز به آپلود دستی عکس/لینک).
    توجه: به‌جای گرفتن یک پیام از چت ادمین، مستقیماً bot می‌گیرد و پیام‌های
    وضعیت را با send_message به ADMIN_ID می‌فرستد — چون این تابع هم از داخل
    یک callback تعاملی ادمین صدا زده می‌شود و هم از مسیر کاملاً خودکار بعد از
    پرداخت کیف‌پول/آنلاین (که اصلاً در چت ادمین اتفاق نمی‌افتد)."""
    uid = ctx["uid"]
    plan_key = ctx.get("plan_key")
    order_id = ctx.get("order_id")
    order_kind = ctx.get("order_kind")
    slug = ctx.get("slug")
    snap = ctx.get("snapshot") or {}

    user = db.get_user(uid)
    if user is None:
        await bot.send_message(ADMIN_ID, "❌ کاربر یافت نشد؛ سرویس در پنل مرزبان ساخته شد ولی ارسال نشد.")
        return

    name = snap.get("name") or "کاربر"
    volume_gb = snap.get("volume_gb")
    days = snap.get("days")
    volume_text = _format_volume_gb_label(volume_gb) if volume_gb is not None else "نامشخص"
    days_text = f"{days} روز" if days else "نامحدود"
    user_limit = snap.get("user_limit")
    user_limit_text = str(user_limit) if user_limit else "نامحدود"
    expiry_date = (now_tehran_naive() + timedelta(days=days)).strftime("%Y-%m-%d") if days else None

    caption = (
        "✅ سرویس با موفقیت ایجاد شد\n\n"
        f"👤 نام کاربری سرویس : {name}\n"
        "🇺🇳 لوکیشن: مولتی لوکیشن+تانل\n"
        f"⏳ مدت زمان: {days_text}\n"
        f"🗜 حجم سرویس: {volume_text}\n"
        f"👤 تعداد کاربر:{user_limit_text}\n\n"
        "لینک اتصال:\n"
        f"{link}\n\n"
        "🧑‍🦯 شما میتوانید شیوه اتصال را با فشردن دکمه زیر دریافت کنید."
    )

    encrypted = crypto.encrypt_config(link)
    plan_name = f"{name} | {volume_text} | {days_text}"
    config_type = db.plan_type(plan_key) if plan_key else "vip"
    if config_type == "test":
        config_type = "vip"

    config_id = db.add_config(
        user["id"], plan_name, encrypted, expiry=expiry_date,
        config_type=config_type, service_id=slug, source=vpn_panel.routing_panel() or "marzban",
    )

    if order_kind == "plan" and order_id:
        db.set_order_status(order_id, "fulfilled")
    elif order_kind == "custom" and order_id:
        db.set_custom_order_status(order_id, "fulfilled")

    async def _send_to_customer():
        try:
            await bot.send_message(int(uid), "📦 سرویس شما آماده شد ⬇️", reply_markup=main_reply_keyboard())
            db.set_keyboard_hidden(int(uid), False)
        except Exception:
            pass
        if qrcode:
            photo = types.BufferedInputFile(_make_qr_bytes(link), filename="qr.png")
            sent = await bot.send_photo(
                int(uid), photo, caption=caption, reply_markup=config_delivery_keyboard(bot_info.get('connection_guide_url'))
            )
            return sent.photo[-1].file_id if sent.photo else None
        await bot.send_message(
            int(uid), caption, reply_markup=config_delivery_keyboard(bot_info.get('connection_guide_url'))
        )
        return None

    # 🆕 فیکس سرعت: قبلاً ارسال کانفیگ به مشتری، پیام تأیید به ادمین، و ثبت لاگ سفارش در کانال
    # «اعتماد» (که خودش شامل یک get_chat + یک send_message جداست) کاملاً پشت‌سرهم ارسال می‌شدند؛ همین زنجیره‌ی رفت‌وبرگشت‌های متوالی، اصلی‌ترین عامل کند بودن
    # (۱۰-۱۵ ثانیه) کل فرآیند «ساخت و ارسال سرویس» بود، نه فقط ارتباط با پنل. چون این پیام‌ها کاملاً
    # مستقل از هم‌اند، حالا همزمان (concurrent) اجرا می‌شوند تا زمانشان روی هم جمع نشود.
    send_result, log_result = await asyncio.gather(
        _send_to_customer(),
        _log_fulfilled_order(
            bot, user,
            plan_order_id=order_id if order_kind == "plan" else None,
            custom_order_id=order_id if order_kind == "custom" else None,
            service_id=slug, service_name=name,
            package_text=f"{volume_text} | {days_text}", expiry_text=expiry_date or "نامحدود",
        ),
        return_exceptions=True,
    )

    if isinstance(send_result, Exception):
        logger.exception("ارسال کانفیگ به کاربر ناموفق بود", exc_info=send_result)
        await bot.send_message(ADMIN_ID, f"⚠️ سرویس ساخته و ذخیره شد ولی ارسال پیام به کاربر ناموفق بود: {send_result}")
    else:
        if send_result:
            db.set_config_qr(config_id, send_result)
        await bot.send_message(ADMIN_ID, "✅ سرویس به‌صورت خودکار ساخته و برای کاربر ارسال شد.")

    if isinstance(log_result, Exception):
        logger.exception("ثبت لاگ سفارش در کانال اعتماد ناموفق بود", exc_info=log_result)


# ---------------------------------------------------------------------------
# 🔁 مدیریت سرویس‌های ساخته‌شده از طریق مرزبان (از صفحه‌ی جزئیات سرویس)
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("marzbanrenew_"))
async def marzban_renew_start(callback: types.CallbackQuery, state: FSMContext):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    cfg_id = int(callback.data.replace("marzbanrenew_", ""))
    cfg = db.get_config_by_id(cfg_id)
    if not cfg or cfg.get("source") not in ("marzban", "pasargad") or not cfg.get("service_id"):
        await callback.answer("❌ این سرویس از پنل VPN ساخته نشده.", show_alert=True)
        return

    # 🆕 فیکس: دیگه از لیست ثابت تمپلیت‌ها انتخاب نمی‌شود (چون تمدید اصلاً پروتکل/inbounds را
    # تغییر نمی‌دهد)؛ دقیقاً حجم و تعداد روز دلخواهی از ادمین پرسیده می‌شود.
    await callback.answer()
    await state.update_data(marzban_renew_cfg_id=cfg_id)
    await state.set_state(AdminStates.waiting_marzban_renew_volume)
    await callback.message.answer(
        "حجم جدید سرویس رو به گیگابایت وارد کن (برای نامحدود عدد 0 رو بفرست):"
    )


@router.message(AdminStates.waiting_marzban_renew_volume)
async def marzban_renew_volume_received(message: types.Message, state: FSMContext):
    volume_gb = parse_int_in_range((message.text or "").strip(), 0, 100000)
    if volume_gb is None:
        await message.answer("❌ یک عدد معتبر برای حجم (گیگابایت) وارد کن:")
        return
    await state.update_data(marzban_renew_volume_gb=volume_gb)
    await state.set_state(AdminStates.waiting_marzban_renew_days)
    await message.answer("تعداد روز اعتبار جدید رو وارد کن (برای همیشگی عدد 0 رو بفرست):")


@router.message(AdminStates.waiting_marzban_renew_days)
async def marzban_renew_days_received(message: types.Message, state: FSMContext):
    days = parse_int_in_range((message.text or "").strip(), 0, 100000)
    if days is None:
        await message.answer("❌ یک عدد معتبر برای تعداد روز وارد کن:")
        return

    data_state = await state.get_data()
    cfg_id = data_state.get("marzban_renew_cfg_id")
    volume_gb = data_state.get("marzban_renew_volume_gb")
    cfg = db.get_config_by_id(cfg_id) if cfg_id else None
    if not cfg:
        await message.answer("❌ این سرویس دیگر یافت نشد؛ دوباره از ابتدا تلاش کن.")
        await state.clear()
        return

    await message.answer("⏳ در حال تمدید...")
    ok, data, msg = await vpn_panel.renew_user_custom(cfg["service_id"], volume_gb, days)
    if not ok:
        await message.answer(
            f"❌ تمدید ناموفق بود: {msg}\n"
            f"🔑 service slug: <code>{html.escape(str(cfg['service_id']))}</code>",
            parse_mode="HTML",
        )
        await state.clear()
        return

    await message.answer(f"📨 پاسخ پنل مرزبان:\n<pre>{_pretty(data)}</pre>", parse_mode="HTML")
    link, slug = vpn_panel.extract_link_and_username(data)
    new_slug = slug or cfg["service_id"]
    expiry_date = (now_tehran_naive() + timedelta(days=days)).strftime("%Y-%m-%d") if days else None
    if link:
        encrypted = crypto.encrypt_config(link)
        db.update_config(cfg_id, cfg["plan"], encrypted, expiry=expiry_date, service_id=new_slug)
        await message.answer(
            "✅ سرویس در پنل مرزبان تمدید شد و لینک جدید ذخیره شد.\n"
            "(اگر لینک ساب عوض شده، حتماً به کاربر هم اطلاع بده.)"
        )
    else:
        db.update_config(cfg_id, cfg["plan"], cfg["config"], expiry=expiry_date, service_id=new_slug)
        await message.answer(
            "✅ سرویس در پنل مرزبان با حجم/مدت جدید تمدید شد. لینک ساب تغییری نکرده، برای همین نیازی به اطلاع دوباره به کاربر نیست."
        )
    await state.clear()


@router.callback_query(F.data.startswith("marzbandisable_"))
async def marzban_disable(callback: types.CallbackQuery):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    cfg_id = int(callback.data.replace("marzbandisable_", ""))
    cfg = db.get_config_by_id(cfg_id)
    if not cfg or not cfg.get("service_id"):
        await callback.answer("❌ این سرویس از پنل مرزبان ساخته نشده.", show_alert=True)
        return
    ok, data, msg = await vpn_panel.disable_user(cfg["service_id"])
    if ok:
        db.set_config_disabled(cfg_id, True)
    await callback.answer("✅ در پنل مرزبان غیرفعال شد." if ok else f"❌ {msg}", show_alert=True)


@router.callback_query(F.data.startswith("marzbanenable_"))
async def marzban_enable(callback: types.CallbackQuery):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    cfg_id = int(callback.data.replace("marzbanenable_", ""))
    cfg = db.get_config_by_id(cfg_id)
    if not cfg or not cfg.get("service_id"):
        await callback.answer("❌ این سرویس از پنل مرزبان ساخته نشده.", show_alert=True)
        return
    ok, data, msg = await vpn_panel.enable_user(cfg["service_id"])
    if ok:
        db.set_config_disabled(cfg_id, False)
    await callback.answer("✅ در پنل مرزبان فعال شد." if ok else f"❌ {msg}", show_alert=True)


@router.callback_query(F.data.startswith("svcrevokesub_"))
async def marzban_revoke_sub(callback: types.CallbackQuery):
    """🆕 برای ادمین: لینک ساب فعلی سرویس را باطل می‌کند و یک لینک کاملاً جدید از پنل می‌سازد (برای وقتی لینک قبلی لو رفته یا نیاز به تعویض دارد)."""
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    cfg_id = int(callback.data.replace("svcrevokesub_", ""))
    cfg = db.get_config_by_id(cfg_id)
    if not cfg or not cfg.get("service_id"):
        await callback.answer("❌ این سرویس از پنل VPN ساخته نشده.", show_alert=True)
        return
    await callback.answer("⏳ در حال ساخت لینک ساب جدید...")
    ok, data, msg = await vpn_panel.revoke_sub(cfg["service_id"])
    if not ok:
        await callback.message.answer(f"❌ ساخت لینک ساب جدید ناموفق بود: {msg}")
        return
    link, _slug = vpn_panel.extract_link_and_username(data)
    if not link:
        await callback.message.answer("⚠️ لینک ساب جدید در پاسخ پنل پیدا نشد.")
        return
    db.update_config_link(cfg_id, crypto.encrypt_config(link))
    await callback.message.answer(
        "✅ لینک ساب جدید ساخته و ذخیره شد. لینک قبلی دیگر کار نمی‌کند؛ اگر لازم است به کاربر هم اطلاع بده."
    )

# ============================================================================
# 🆕 Compatibility layer: automatic fulfillment now uses Bomb-style mappings.
# These definitions intentionally override the legacy single-panel functions
# above so existing imports in plans.py/admin.py keep working without any
# «active panel» concept.
# ============================================================================
async def _deliver_mapped_panel_link(bot, ctx: dict, link: str):
    uid=ctx['uid']; panel=ctx['panel']; user=db.get_user(uid)
    if not user:
        await bot.send_message(ADMIN_ID,'❌ کاربر یافت نشد؛ سرویس روی پنل ساخته شد ولی تحویل نشد.'); return
    snap=ctx.get('snapshot') or {}; name=snap.get('name') or 'کاربر'; volume=snap.get('volume_gb'); days=snap.get('days'); limit=snap.get('user_limit')
    try: vol=float(volume) if volume is not None else None
    except: vol=None
    if vol is None: vol_text='نامشخص'
    elif vol<=0: vol_text='نامحدود'
    elif vol<1: vol_text=f'{round(vol*1024)} مگابایت'
    else: vol_text=f'{int(vol) if vol.is_integer() else vol:g} گیگابایت'
    days_text=f'{days} روز' if days else 'نامحدود'; limit_text=str(limit) if limit else 'نامحدود'
    expiry=(now_tehran_naive()+timedelta(days=int(days))).strftime('%Y-%m-%d') if days else None
    caption=(f'✅ سرویس با موفقیت ایجاد شد\n\n👤 نام کاربری سرویس : {name}\n'
             f'🖥 پنل: {panels.panel_label(panel)}\n⏳ مدت زمان: {days_text}\n🗜 حجم سرویس: {vol_text}\n'
             f'👤 تعداد کاربر: {limit_text}\n\nلینک اتصال:\n{link}\n\n'
             '🧑‍🦯 شما میتوانید شیوه اتصال را با فشردن دکمه زیر دریافت کنید.')
    cfg_type=db.plan_type(ctx.get('plan_key')) if ctx.get('plan_key') else 'vip'
    cfg_id=db.add_config(user['id'],f'{name} | {vol_text} | {days_text}',crypto.encrypt_config(link),expiry=expiry,
                         config_type=cfg_type,service_id=ctx.get('service_id'),source=panel.get('panel_type','manual'),panel_id=panel.get('id'))
    if ctx.get('order_kind')=='plan' and ctx.get('order_id'): db.set_order_status(ctx['order_id'],'fulfilled')
    if ctx.get('order_kind')=='custom' and ctx.get('order_id'): db.set_custom_order_status(ctx['order_id'],'fulfilled')
    sent_file=None
    try:
        await bot.send_message(int(uid),'📦 سرویس شما آماده شد ⬇️',reply_markup=main_reply_keyboard()); db.set_keyboard_hidden(int(uid),False)
        if qrcode:
            sent=await bot.send_photo(int(uid),types.BufferedInputFile(_make_qr_bytes(link),filename='qr.png'),caption=caption,reply_markup=config_delivery_keyboard(bot_info.get('connection_guide_url')))
            if sent.photo: sent_file=sent.photo[-1].file_id
        else:
            await bot.send_message(int(uid),caption,reply_markup=config_delivery_keyboard(bot_info.get('connection_guide_url')))
        if sent_file: db.set_config_qr(cfg_id,sent_file)
        await bot.send_message(ADMIN_ID,f'✅ سرویس از {panels.panel_label(panel)} ساخته و برای کاربر ارسال شد.')
    except Exception as e:
        await bot.send_message(ADMIN_ID,f'⚠️ سرویس ساخته و ذخیره شد ولی ارسال پیام به کاربر ناموفق بود: {e}')

async def auto_fulfill_vip_via_marzban(bot, uid, plan_key: str, order_id: int | None) -> bool:
    mapping=db.get_panel_map_for_plan_key(plan_key)
    if not mapping or not mapping.get('enabled'): return False
    panel=db.get_vpn_panel(mapping['panel_id'])
    plan=db.get_effective_plan(plan_key); user=db.get_user(uid)
    if not panel or not panel.get('enabled') or not plan or not user:return False
    if order_id:
        cur=db.get_order(order_id)
        if cur and cur.get('status') in ('fulfilled','processing'): return True if cur.get('status')=='fulfilled' else False
        if not db.claim_order_for_processing(order_id):
            fresh=db.get_order(order_id); return bool(fresh and fresh.get('status')=='fulfilled')
    username=_generate_service_username()
    ok,link,remote_id,data,msg=await panels.create_service(panel,username,mapping['remote_ref'],volume_gb=plan.get('volume_gb'),days=plan.get('days'),device_limit=plan.get('user_limit'))
    if not ok:
        if order_id: db.set_order_status(order_id,'paid')
        await bot.send_message(ADMIN_ID,f'⚠️ ساخت خودکار از {panels.panel_label(panel)} ناموفق بود:\n{msg}\n🔑 مرجع: {mapping["remote_ref"]}')
        return False
    if not link:
        await bot.send_message(ADMIN_ID,f'⚠️ سرویس در {panels.panel_label(panel)} ساخته شد ولی لینک از پاسخ پنل استخراج نشد.\n<pre>{_pretty(data)}</pre>',parse_mode='HTML')
        return False
    ctx={'uid':uid,'plan_key':plan_key,'order_id':order_id,'order_kind':'plan','panel':panel,'service_id':remote_id,'snapshot':{'name':plan.get('name'),'volume_gb':plan.get('volume_gb'),'days':plan.get('days'),'user_limit':plan.get('user_limit')}}
    await _deliver_mapped_panel_link(bot,ctx,link); return True

async def auto_fulfill_custom_via_marzban(bot, user: dict, order_id: int, volume, days, custom_name) -> bool:
    cur=db.get_custom_order(order_id)
    if cur and cur.get('status')=='fulfilled': return True
    if cur and cur.get('status')=='processing': return False
    if not db.claim_custom_order_for_processing(order_id):
        fresh=db.get_custom_order(order_id); return bool(fresh and fresh.get('status')=='fulfilled')
    mapping=db.get_panel_plan_map_with_panel('custom_build',0)
    if not mapping or not mapping.get('enabled'):
        db.set_custom_order_status(order_id,'paid'); return False
    panel=db.get_vpn_panel(mapping['panel_id'])
    if not panel or not panel.get('enabled'):
        db.set_custom_order_status(order_id,'paid'); return False
    username=_generate_service_username(); ok,link,remote_id,data,msg=await panels.create_service(panel,username,mapping['remote_ref'],volume_gb=volume,days=days)
    if not ok:
        db.set_custom_order_status(order_id,'paid'); await bot.send_message(ADMIN_ID,f'⚠️ ساخت سفارشی از {panels.panel_label(panel)} ناموفق بود:\n{msg}'); return False
    if not link:
        db.set_custom_order_status(order_id,'paid'); await bot.send_message(ADMIN_ID,f'⚠️ سرویس ساخته شد ولی لینک استخراج نشد:\n<pre>{_pretty(data)}</pre>',parse_mode='HTML'); return False
    ctx={'uid':user['telegram_id'],'plan_key':None,'order_id':order_id,'order_kind':'custom','panel':panel,'service_id':remote_id,'snapshot':{'name':custom_name or 'سرویس سفارشی','volume_gb':volume,'days':days}}
    await _deliver_mapped_panel_link(bot,ctx,link); return True
