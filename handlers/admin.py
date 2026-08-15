"""
handlers/admin.py
پنل کامل ادمین: آمار، لیست کاربران، جستجوی حرفه‌ای، شارژ کیف پول، ارسال
کانفیگ، تأیید/رد پرداخت کارت‌به‌کارت خرید سرویس، مدیریت تخفیف (ساخت گام‌به‌گام)،
مدیریت دعوت‌ها، پیام همگانی، بکاپ.

تمام handlerهای این فایل فقط برای ADMIN_ID فعال هستند.
"""

from datetime import datetime, timedelta
import asyncio
import html
import json
import logging
import os
import re

from aiogram import Router, F, types, BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile

import database as db
import crypto
import alerts
from subscription import extract_meta, days_remaining, format_bytes, usage_bar, fetch_subscription_info, format_expire
from utils import parse_int_in_range, is_duplicate_action, now_tehran_naive, STICKER_SECTION_LABELS, STICKER_FILES, STICKERS_DIR, invalidate_section_sticker_cache, send_notification_sticker, clean_numeric_id, TELEGRAM_TEXT_LIMIT, truncate_for_telegram, is_message_too_long_error
from keyboards import main_reply_keyboard
from states import AdminStates, UserStates
import bot_info
import payments
import marzban
import pasargad
import vpn_panel
from config import (
    ADMIN_ID,
    DATABASE_PATH,
    AGENCY_VIP_DISCOUNT_PERCENT,
    REFERRAL_MIN_VOLUME_GB,
    FREE_TEST_PLAN_KEY,
    MARZBAN_ENABLED,
    PASARGAD_ENABLED,
)
from keyboards import (
    admin_panel_menu,
    admin_reply_keyboard,
    admin_back_button,
    admin_discount_menu,
    admin_user_actions_keyboard,
    admin_purchase_notify_keyboard,
    admin_userlist_menu,
    config_delivery_keyboard,
    admin_services_list_keyboard,
    admin_service_detail_keyboard,
    admin_order_queue_keyboard,
    admin_clear_orders_confirm_keyboard,
    admin_request_queue_menu,
    admin_pending_receipts_keyboard,
    admin_clear_receipts_confirm_keyboard,
    admin_purge_confirm_keyboard,
    admin_userlist_page_keyboard,
    admin_accounting_keyboard,
    discount_type_keyboard,
    discount_plans_select_keyboard,
    discount_plans_edit_keyboard,
    discount_detail_keyboard,
    discount_delete_confirm_keyboard,
    admin_agency_menu,
    admin_agent_row_keyboard,
    admin_agent_actions_keyboard,
    admin_vip_categories_keyboard,
    admin_vip_category_detail_keyboard,
    admin_vip_plan_detail_keyboard,
    admin_pm_cancel_keyboard,
    admin_referrers_page_keyboard,
    admin_referred_detail_keyboard,
    admin_guides_menu,
    admin_guide_detail_keyboard,
    admin_guide_delete_confirm_keyboard,
    admin_guide_cancel_keyboard,
    admin_error_logs_keyboard,
    admin_error_log_detail_keyboard,
    admin_error_logs_clear_confirm_keyboard,
    admin_stickers_menu,
    admin_sticker_detail_keyboard,
    admin_sticker_cancel_keyboard,
    admin_botinfo_menu,
    admin_botinfo_field_keyboard,
    admin_botinfo_channels_menu,
    admin_custom_build_settings_keyboard,
    admin_pasargad_menu,
    admin_rebecca_menu,
    admin_manage_admins_keyboard,
    admin_permissions_keyboard,
    admin_custom_order_card_approval_keyboard, admin_custom_order_notify_keyboard,
)

plan_type = db.plan_type  # نسخه‌ی DB-aware (دسته‌بندی‌های VIP را هم می‌شناسد)

from text_catalog import TEXT_CATEGORIES, TEXTS, CATEGORY_BY_KEY, text as user_text, refresh as refresh_user_text

router = Router(name="admin")
logger = logging.getLogger(__name__)


def _auto_fulfill_custom_via_marzban(*args, **kwargs):
    # Lazy import عمداً برای جلوگیری از circular import بین admin و marzban_admin.
    from handlers.marzban_admin import auto_fulfill_custom_via_marzban
    return auto_fulfill_custom_via_marzban(*args, **kwargs)


def _permission_for_message_text(text: str | None) -> str | None:
    mapping = {
        "📊 آمار": "stats",
        "📥 صف درخواست‌ها": "requests",
        "👥 لیست کاربران": "users",
        "🔍 جستجوی کاربر": "users",
        "📢 پیام همگانی": "broadcast",
        "🎟 مدیریت تخفیف": "discounts",
        "🤝 نمایندگی (تخفیف VIP)": "agency",
        "🗂 دسته‌بندی‌های VIP": "plans",
        "📦 نگاشت پلن‌ها به پنل‌های متصل": "vpn_panel",
        "🛡️ اتصال پنل پاسارگارد": "vpn_panel",
        "🦋 اتصال پنل Rebecca": "vpn_panel",
        "🤝 مدیریت دعوت‌ها": "referrals",
        "📚 مدیریت راهنما": "guides",
        "🦖 لاگ خطاها": "logs",
        "ℹ️ اطلاعات ربات": "botinfo",
        "🎬 استیکرهای منو": "stickers",
        "💾 بکاپ": "backup",
        "🎁 تنظیم تست رایگان": "settings",
        "📝 مدیریت متن‌های کاربر": "texts",
        "🔴 خاموش کردن سفارشات": "orders_toggle",
        "🟢 روشن کردن سفارشات": "orders_toggle",
    }
    return mapping.get(text or "")


def _permission_for_callback(data: str | None) -> str | None:
    d = data or ""
    if d in {"admin_back"}:
        return None
    if d.startswith(("approve_", "reject_", "approvepay|", "rejectpay|", "approvecustom_", "rejectcustom_", "clearreceipts")) or d == "admin_pending_receipts":
        return "receipts"
    groups = [
        (("admin_stats",), "stats"),
        (("admin_request_queue", "admin_order_queue", "dismissorder_", "clearorders", "marzbansend|"), "requests"),
        (("admin_userlist", "userpage_", "useropen_", "accounting_", "admin_search", "useractions_", "pm_", "toggleblock_", "svcs_", "svcdetail_", "svcdelete_", "svcrestore_", "svcpurge", "svcedit_"), "users"),
        (("admin_broadcast",), "broadcast"),
        (("admin_discount", "discdetail_", "discdelete", "discedit_", "discplan", "new_discount", "disctype_"), "discounts"),
        (("admin_agency", "new_agent", "deleteagent_", "agentopen_", "editagentpercent_"), "agency"),
        (("admin_vip_categories", "newvip", "vip"), "plans"),
        (("admin_marzban", "admin_pasargad", "admin_rebecca", "marz", "pasargad", "rebecca", "svcrevokesub_"), "vpn_panel"),
        (("admin_botinfo", "botinfo", "channel"), "botinfo"),
        (("admin_stickers", "sticker"), "stickers"),
        (("admin_referrals", "refpage_", "refdetail_"), "referrals"),
        (("admin_guides", "guide"), "guides"),
        (("admin_texts", "textedit_", "textdelete_"), "texts"),
        (("errlog",), "logs"),
        (("admin_backup",), "backup"),
        (("admin_orders_off", "admin_orders_on"), "orders_toggle"),
        (("free_test", "admin_free_test_settings"), "settings"),
    ]
    for prefixes, perm in groups:
        if any(d == x or d.startswith(x) for x in prefixes):
            return perm
    return None


class AdminPermissionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if not user or user.id == ADMIN_ID:
            return await handler(event, data)
        if not db.is_sub_admin(str(user.id)):
            return await handler(event, data)
        # مدیریت خود ادمین‌های فرعی فقط برای ادمین اصلی است.
        cb_data = getattr(event, "data", None)
        if cb_data and (cb_data == "admin_manage_admins" or cb_data.startswith("subadm")):
            await event.answer("⛔ فقط ادمین اصلی دسترسی دارد.", show_alert=True)
            return
        perm = _permission_for_callback(cb_data) if cb_data is not None else _permission_for_message_text(getattr(event, "text", None))
        # اگر دکمه‌ای داخل یک بخش مجاز ساخته شده باشد، همان مجوز بخش مادر کافی است.
        if user.id != ADMIN_ID and perm == "receipts" and db.sub_admin_has_permission(str(user.id), "requests"):
            return await handler(event, data)
        if perm and not db.sub_admin_has_permission(str(user.id), perm):
            if cb_data is not None:
                await event.answer("⛔ شما به این قابلیت دسترسی ندارید.", show_alert=True)
            else:
                await event.answer("⛔ شما به این قابلیت دسترسی ندارید.")
            return
        return await handler(event, data)


router.message.middleware(AdminPermissionMiddleware())
router.callback_query.middleware(AdminPermissionMiddleware())


def _is_main_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def _is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID or db.is_sub_admin(str(user_id))

def _admin_perm(user_id: int, permission: str) -> bool:
    return user_id == ADMIN_ID or db.sub_admin_has_permission(str(user_id), permission)

def _current_admin_permissions(user_id: int) -> set[str] | None:
    if user_id == ADMIN_ID:
        return None
    adm = db.get_sub_admin(str(user_id)) or {}
    return set(adm.get("permissions") or [])

def _admin_panel_kb_for(user_id: int):
    return admin_panel_menu(db.is_orders_enabled(), permissions=_current_admin_permissions(user_id), is_main_admin=(user_id == ADMIN_ID))

def _admin_reply_kb_for(user_id: int, orders_enabled: bool | None = None):
    return admin_reply_keyboard(orders_enabled, permissions=_current_admin_permissions(user_id), is_main_admin=(user_id == ADMIN_ID))


async def _notify_main_admin_action(bot, actor, action: str, target: str = "", details: str = ""):
    name = getattr(actor, "full_name", None) or getattr(actor, "first_name", None) or str(getattr(actor, "id", ""))
    actor_id = str(getattr(actor, "id", ""))
    try:
        db.log_admin_action(actor_id, name, action, target, details)
    except Exception:
        logger.exception("admin activity log failed")
    if actor_id != str(ADMIN_ID):
        try:
            await bot.send_message(ADMIN_ID, f"📝 گزارش عملکرد ادمین فرعی\n\n👤 {name}\n🆔 {actor_id}\n✅ عملیات: {action}\n🎯 مورد: {target or '-'}\nℹ️ جزئیات: {details or '-'}")
        except Exception:
            logger.exception("notify main admin failed")

async def _deny_no_perm(obj, text="⛔ شما به این بخش دسترسی ندارید."):
    if hasattr(obj, "answer"):
        try:
            await obj.answer(text, show_alert=True)
        except TypeError:
            await obj.answer(text)


async def _reply_with_user_actions(target, text: str, uid, is_blocked: bool, *, edit: bool = False):
    """پیام همراه با کیبورد اقدامات کاربر (admin_user_actions_keyboard) را می‌فرستد یا ویرایش می‌کند.
    اگر تلگرام به‌خاطر تنظیمات حریم‌خصوصی محدودتر همان کاربر خاص، دکمه‌ی «رفتن به پیوی کاربر»
    (لینک tg://user) را رد کند (خطای BUTTON_USER_PRIVACY_RESTRICTED)، به‌جای کرش کردن کل پیام،
    همان پیام را بدون این دکمه‌ی خاص دوباره می‌فرستد؛ برای بقیه‌ی کاربران دکمه همچنان نمایش داده می‌شود."""
    try:
        kb = admin_user_actions_keyboard(uid, is_blocked)
        if edit:
            await target.edit_text(text, reply_markup=kb)
        else:
            await target.answer(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "BUTTON_USER_PRIVACY_RESTRICTED" in str(e):
            kb = admin_user_actions_keyboard(uid, is_blocked, show_pm_link=False)
            if edit:
                await target.edit_text(text, reply_markup=kb)
            else:
                await target.answer(text, reply_markup=kb)
        else:
            raise


_RECEIPTS_QUEUE_MARKER = "🧾 رسیدهای در انتظار تایید"


async def _finish_receipt_message(message: types.Message, note: str, queue_refresh=None):
    """پیام رسید (چه پیام متنی معمولی از ربات کلاسیک، چه پیام عکس+کپشن از
    Mini App که دکمه‌ها مستقیم روی خودِ عکس هستند) را با یک خط نتیجه
    (تأیید/رد) نهایی می‌کند و دکمه‌ها را حذف می‌کند.
    توجه: روی پیام‌های عکس‌دار، edit_text خطا می‌دهد (تلگرام برای عکس‌ها
    caption دارد نه text)؛ برای همین باید edit_caption صدا زده شود.

    queue_refresh: یک تابع async بدون آرگومان. اگر همین دکمه‌ی تأیید/رد از
    داخل پیام «صف درخواست‌ها → رسیدهای در انتظار تایید» زده شده باشد (نه از
    پیام تک‌رسیدیِ اصلی)، به‌جای خالی‌کردن دکمه‌های کل لیست، همان لیست
    رفرش می‌شود تا آیتم‌های دیگرِ هنوز-در-انتظار از بین نروند."""
    text_or_caption = message.caption if message.photo else message.text
    if queue_refresh is not None and text_or_caption and text_or_caption.startswith(_RECEIPTS_QUEUE_MARKER):
        await queue_refresh()
        return
    empty_kb = types.InlineKeyboardMarkup(inline_keyboard=[])
    if message.photo:
        await message.edit_caption(caption=(message.caption or "") + note, reply_markup=empty_kb)
    else:
        await message.edit_text((message.text or "") + note, reply_markup=empty_kb)




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

def _gb_from_bytes(num_bytes) -> float | None:
    if not num_bytes:
        return None
    try:
        return round(int(num_bytes) / (1024 ** 3), 1)
    except (TypeError, ValueError):
        return None


@router.message(Command("admin"))
async def admin_entry(message: types.Message):
    if not _is_admin(message.from_user.id):
        return  # کاربر عادی هیچ پاسخی نمی‌گیرد (نه حتی پیام خطا) - امنیتی
    await message.answer("👨‍💻 پنل مدیریت:", reply_markup=_admin_panel_kb_for(message.from_user.id))


@router.callback_query(F.data == "admin_back")
async def admin_back(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("👨‍💻 پنل مدیریت:", reply_markup=_admin_panel_kb_for(callback.from_user.id))
    await callback.answer()


@router.callback_query(F.data == "admin_manage_admins")
async def admin_manage_admins(callback: types.CallbackQuery):
    if not _is_main_admin(callback.from_user.id):
        await callback.answer("⛔ فقط ادمین اصلی دسترسی دارد.", show_alert=True)
        return
    # رفع باگ: اگر متن/دکمه دقیقاً همان چیزی باشد که الان روی پیام هست (مثلاً دوبار زدن روی دکمه)، تلگرام خطای "message is not modified"
    # می‌دهد که قبلاً گرفته نمی‌شد و به هندلر سراسری خطا می‌رفت و به کاربر "خطایی پیش آمد" نشان داده می‌شد.
    try:
        await callback.message.edit_text("👮 مدیریت ادمین‌های فرعی\n\nادمین را انتخاب کنید یا ادمین جدید اضافه کنید:", reply_markup=admin_manage_admins_keyboard(db.get_all_sub_admins()))
    except TelegramBadRequest:
        pass
    await callback.answer()

# 🐛 فیکس: دکمه‌ی «مدیریت ادمین‌ها» قبلاً فقط داخل پنل اینلاین بود، طبق درخواست کاربر الان به منوی پایین صفحه (reply keyboard) هم منتقل شد.
@router.message(F.text == "👮 مدیریت ادمین‌ها")
async def admin_manage_admins_from_menu(message: types.Message, state: FSMContext):
    if not _is_main_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("👮 مدیریت ادمین‌های فرعی\n\nادمین را انتخاب کنید یا ادمین جدید اضافه کنید:", reply_markup=admin_manage_admins_keyboard(db.get_all_sub_admins()))


@router.callback_query(F.data == "subadm_add")
async def subadm_add(callback: types.CallbackQuery, state: FSMContext):
    if not _is_main_admin(callback.from_user.id):
        await callback.answer("⛔ فقط ادمین اصلی دسترسی دارد.", show_alert=True); return
    await state.set_state(AdminStates.waiting_sub_admin_id)
    try:
        await callback.message.edit_text(
            "آیدی عددی تلگرام ادمین فرعی را بفرستید. اگر خواستید نام هم ثبت شود این‌طور بفرستید:\n"
            "نام | آیدی\n"
            "مثال: علی پشتیبان | 123456789",
            reply_markup=admin_back_button(),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()

@router.message(AdminStates.waiting_sub_admin_id)
async def subadm_add_id(message: types.Message, state: FSMContext):
    if not _is_main_admin(message.from_user.id): return
    raw = clean_numeric_id(message.text or "")
    if "|" in raw:
        name, tid = [x.strip() for x in raw.split("|", 1)]
        tid = clean_numeric_id(tid)
    else:
        tid, name = raw, raw
    if not tid.isdigit():
        await message.answer("❌ فرمت درست نیست. فقط آیدی عددی یا «نام | آیدی» بفرستید.", reply_markup=admin_back_button()); return
    try:
        db.add_sub_admin(tid, name=name or tid, permissions=[])
    except Exception:
        logger.exception("خطا در ثبت ادمین فرعی")
        await message.answer("⚠️ ثبت آیدی با خطا مواجه شد. لطفاً دوباره تلاش کنید.", reply_markup=admin_back_button())
        return
    await _notify_main_admin_action(message.bot, message.from_user, "افزودن ادمین فرعی", tid, name or tid)
    await state.clear()
    await message.answer("✅ ادمین فرعی اضافه شد. حالا قابلیت‌هایش را با تیک انتخاب کنید:", reply_markup=admin_permissions_keyboard(tid, []))

@router.callback_query(F.data.startswith("subadm_") & ~F.data.startswith("subadmperm_") & ~F.data.startswith("subadmdel_"))
async def subadm_open(callback: types.CallbackQuery):
    if not _is_main_admin(callback.from_user.id):
        await callback.answer("⛔ فقط ادمین اصلی دسترسی دارد.", show_alert=True); return
    tid = callback.data.split("_",1)[1]
    adm = db.get_sub_admin(tid)
    if not adm:
        await callback.answer("❌ ادمین یافت نشد.", show_alert=True); return
    try:
        await callback.message.edit_text(f"👤 ادمین فرعی: {tid}\n\nقابلیت‌ها را تیک بزنید/بردارید:", reply_markup=admin_permissions_keyboard(tid, adm.get("permissions") or []))
    except TelegramBadRequest:
        pass
    await callback.answer()

@router.callback_query(F.data.startswith("subadmperm_"))
async def subadm_toggle_perm(callback: types.CallbackQuery):
    if not _is_main_admin(callback.from_user.id):
        await callback.answer("⛔ فقط ادمین اصلی دسترسی دارد.", show_alert=True); return
    rest = callback.data.removeprefix("subadmperm_")
    # رفع باگ: برخی کلیدهای قابلیت خودشان زیرخط دارند (مثل "vpn_panel"، "orders_toggle")، با rsplit("_", 1) قبلی
    # آیدی و نام قابلیت اشتباه جدا می‌شدند (مثلاً آیدی "...vpn" و قابلیت "panel") و ادمین پیدا نمی‌شد. الان با ':' (که در هیچ‌کدام وجود ندارد) جدا می‌شوند.
    if ":" in rest:
        tid, perm = rest.split(":", 1)
    else:
        # سازگاری با دکمه‌های قدیمی (اگر callback زیرخطدار قبلی هنوز جایی فعال باشد)
        tid, perm = rest.rsplit("_", 1)
    adm = db.get_sub_admin(tid)
    if not adm: await callback.answer("❌ یافت نشد.", show_alert=True); return
    perms = set(adm.get("permissions") or [])
    if perm in perms: perms.remove(perm)
    else: perms.add(perm)
    db.update_sub_admin_permissions(tid, sorted(perms))
    await _notify_main_admin_action(callback.bot, callback.from_user, "تغییر دسترسی ادمین فرعی", tid, perm)
    try:
        await callback.message.edit_reply_markup(reply_markup=admin_permissions_keyboard(tid, sorted(perms)))
    except TelegramBadRequest:
        pass
    # فیکس: قبلاً فقط کیبورد تأیید خود ادمین اصلی به‌روز می‌شد، ولی منوی پایین صفحه‌ی خود ادمین فرعی
    # (که از آخرین /start او ساخته شده بود) همچنان قدیمی می‌ماند تا دوباره /start بزند. حالا
    # همینجا منوی پایین صفحه‌اش را با دسترسی‌های تازه دوباره برایش می‌فرستیم تا فوراً به‌روز شود.
    try:
        await callback.bot.send_message(
            int(tid),
            "🔄 دسترسی‌های شما توسط ادمین اصلی به‌روزرسانی شد. منوی پایین صفحه شما براساس دسترسی‌های جدید به‌روز شد.",
            reply_markup=admin_reply_keyboard(permissions=set(perms), is_main_admin=False),
        )
    except Exception:
        logger.exception("اطلاع ادمین فرعی از تغییر دسترسی ناموفق بود")
    await callback.answer("✅ بروزرسانی شد")

@router.callback_query(F.data.startswith("subadmdel_"))
async def subadm_delete(callback: types.CallbackQuery):
    if not _is_main_admin(callback.from_user.id):
        await callback.answer("⛔ فقط ادمین اصلی دسترسی دارد.", show_alert=True); return
    tid = callback.data.removeprefix("subadmdel_")
    db.delete_sub_admin(tid)
    await _notify_main_admin_action(callback.bot, callback.from_user, "حذف ادمین فرعی", tid, "")
    try:
        await callback.message.edit_text("✅ ادمین حذف شد.", reply_markup=admin_manage_admins_keyboard(db.get_all_sub_admins()))
    except TelegramBadRequest:
        pass
    await callback.answer()

@router.message(F.text == "📥 صف درخواست‌ها")
async def menu_admin_request_queue(message: types.Message):
    if not _is_admin(message.from_user.id):
        return
    order_count = len(db.get_pending_orders(limit=200)) + len(db.get_pending_custom_orders(limit=200))
    receipt_count = len(db.get_pending_receipts(limit=200)) + len(db.get_pending_custom_order_receipts(limit=200))
    await message.answer(
        "📥 صف درخواست‌ها\n\nچه چیزی رو می‌خوای بررسی کنی؟ 👇",
        reply_markup=admin_request_queue_menu(order_count, receipt_count),
    )


@router.message(F.text == "🤝 نمایندگی (تخفیف VIP)")
async def menu_admin_agency(message: types.Message):
    if not _is_admin(message.from_user.id):
        return
    agents = db.get_all_agents()
    text = (
        "🤝 هنوز هیچ نماینده‌ای ثبت نشده.\n\nبرای افزودن، دکمه‌ی زیر را بزنید 👇"
        if not agents else
        "🤝 نمایندگان فعلی (تخفیف خودکار روی VIP)\n\nروی هرکدام بزنید تا مثل بخش «کاربران» مدیریتش کنید 👇"
    )
    await message.answer(text, reply_markup=admin_agency_menu(agents))


@router.message(F.text == "🗂 دسته‌بندی‌های VIP")
async def menu_admin_vip_categories(message: types.Message):
    if not _is_admin(message.from_user.id):
        return
    await message.answer(
        "🗂 دسته‌بندی‌های VIP\n\n"
        "این دسته‌ها همان چیزی هستند که کاربر موقع «خرید اشتراک → سرور VIP» می‌بیند.\n"
        "برای مدیریت پلن‌های داخل هر دسته، روی آن بزنید 👇",
        reply_markup=admin_vip_categories_keyboard(),
    )




@router.message(F.text == "🔴 خاموش کردن سفارشات")
async def menu_admin_orders_off(message: types.Message):
    if not _is_admin(message.from_user.id):
        return
    db.set_orders_enabled(False)
    users = db.get_all_users()
    sent, failed = 0, 0
    status_msg = await message.answer(f"⏳ در حال اطلاع‌رسانی به {len(users)} کاربر...")
    for u in users:
        try:
            await message.bot.send_message(
                int(u["telegram_id"]),
                user_text("orders_closed", "🔴 ربات به دلیل حجم سفارشات بالا موقتاً بسته می‌باشد.") + "\n\nروشن شدن دوباره‌ی آن اطلاع‌رسانی خواهد شد.",
            )
            sent += 1
        except Exception:
            failed += 1
    await status_msg.edit_text(f"🔴 بخش سفارشات خاموش شد. اطلاع‌رسانی به {sent} نفر موفق، {failed} نفر ناموفق.")
    await message.answer("👨‍💻 پنل مدیریت:", reply_markup=_admin_reply_kb_for(message.from_user.id, False))


@router.message(F.text == "🟢 روشن کردن سفارشات")
async def menu_admin_orders_on(message: types.Message):
    if not _is_admin(message.from_user.id):
        return
    db.set_orders_enabled(True)
    users = db.get_all_users()
    sent, failed = 0, 0
    status_msg = await message.answer(f"⏳ در حال اطلاع‌رسانی به {len(users)} کاربر...")
    for u in users:
        try:
            await message.bot.send_message(
                int(u["telegram_id"]),
                user_text("orders_opened", "🟢 ربات مجدداً فعال شد!") + "\n\nبا زدن /start می‌توانید دوباره سفارش ثبت کنید.",
            )
            sent += 1
        except Exception:
            failed += 1
    await status_msg.edit_text(f"🟢 بخش سفارشات روشن شد. اطلاع‌رسانی به {sent} نفر موفق، {failed} نفر ناموفق.")
    await message.answer("👨‍💻 پنل مدیریت:", reply_markup=_admin_reply_kb_for(message.from_user.id, True))


def _error_logs_text(logs: list, total: int) -> str:
    if not logs:
        return "🦖 لاگ خطاها\n\n✅ تا این لحظه هیچ خطای ثبت نشده."
    return f"🦖 لاگ خطاها — {total} خطای ثبت‌شده\n\nروی هرکدام بزنید تا جزئیاتش رو ببینید 👇"


async def _open_error_logs(target, edit: bool = False):
    logs = db.get_error_logs(limit=15)
    total = db.count_error_logs()
    text = _error_logs_text(logs, total)
    kb = admin_error_logs_keyboard(logs)
    if edit:
        await target.message.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@router.message(F.text == "🦖 لاگ خطاها")
async def menu_admin_error_logs(message: types.Message):
    if not _is_admin(message.from_user.id):
        return
    await _open_error_logs(message, edit=False)


@router.callback_query(F.data == "errlogrefresh")
async def admin_error_logs_refresh(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await _open_error_logs(callback, edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("errlogdetail_"))
async def admin_error_log_detail(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    log_id = int(callback.data.split("_", 1)[1])
    log = db.get_error_log(log_id)
    if log is None:
        await callback.answer("❌ یافت نشد.", show_alert=True)
        return
    # 🆕 فیکس: قبلاً پیام خطا جداگانه از یک طول ثابت (message تا ۲۰۰۰ کاراکتر + traceback تا ۳۵۰۰ کاراکتر) ساخته می‌شد؛ اگر خطایی با پیام (message) طولانی ثبت می‌شد (مثلاً همین خطای «MESSAGE_TOO_LONG»)، مجموع این دو می‌توانست از سقف تلگرام (۴۰۹۶) رد شود و خود صفحه‌ی «لاگ خطاها» هم با همان خطا مواجه می‌شد و ادمین اصلاً نمی‌توانست جزئیات را ببیند. حالا طول قابل‌نمایش traceback پویا بر اساس طول واقعی بقیه محاسبه می‌شود تا مجموع همیشه زیر سقف تلگرام بماند، و بازهم یک try/except محافظتی اضافه شده تا اگر بازهم محاسبه جایی کم بیافتاد، پیام با یک نسخه‌ی کاملاً مختصر‌شده بازهم فرستاده شود تا این بخش از پنل ادمین هرگز با ارور متوقف نشود.
    error_type_display = html.escape(str(log["error_type"]))
    occurred_at_display = html.escape(str(log.get("occurred_at") or ""))
    message_display = html.escape(str(log.get("message") or "")[:300])
    header = (
        f"⚠️ {error_type_display}\n"
        f"🕐 {occurred_at_display}\n\n"
        f"📝 {message_display}\n\n"
    )
    wrapper_len = len("<pre></pre>")
    max_tb_len = max(TELEGRAM_TEXT_LIMIT - len(header) - wrapper_len - 20, 200)
    tb = html.escape(str(log.get("traceback") or "")[:max_tb_len])
    text = f"{header}<pre>{tb}</pre>"
    try:
        await callback.message.edit_text(text, reply_markup=admin_error_log_detail_keyboard())
    except TelegramBadRequest as e:
        if is_message_too_long_error(e):
            fallback_text = truncate_for_telegram(
                f"⚠️ {error_type_display}\n🕐 {occurred_at_display}\n\n📝 {message_display}"
            )
            await callback.message.edit_text(fallback_text, reply_markup=admin_error_log_detail_keyboard())
        else:
            raise
    await callback.answer()


@router.callback_query(F.data == "errlogclear")
async def admin_error_logs_clear_ask(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await callback.message.edit_text(
        "🗑 مطمئنید می‌خواهید همه‌ی لاگ ها پاک شوند؟", reply_markup=admin_error_logs_clear_confirm_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "errlogclearconfirm")
async def admin_error_logs_clear_do(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    db.clear_error_logs()
    await _open_error_logs(callback, edit=True)
    await callback.answer("✅ پاک شد.")


# ---------------------------------------------------------------------------
# 📊 آمار
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    text = (
        f"📊 آمار ربات\n\n"
        f"💰 فروش امروز: {db.sales_since(1):,} تومان\n"
        f"💰 فروش هفته: {db.sales_since(7):,} تومان\n"
        f"💰 فروش ماه: {db.sales_since(30):,} تومان\n"
        f"💰 کل فروش: {db.total_sales():,} تومان\n\n"
        f"👥 تعداد کاربران: {db.count_users()}\n"
        f"🟢 کاربران فعال (۳۰ روز اخیر): {db.count_active_users(30)}"
    )
    await callback.message.edit_text(text, reply_markup=admin_back_button())
    await callback.answer()


# ---------------------------------------------------------------------------
# 👥 لیست کاربران
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "admin_userlist")
async def admin_user_list(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    text = (
        f"👥 مدیریت کاربران\n\n"
        f"👥 کل کاربران ثبت‌نامی: {db.count_users()}\n"
        f"🟢 مشتریانی که خرید داشته‌اند: {db.count_customers()}\n\n"
        f"یکی از گزینه‌های زیر را انتخاب کنید 👇"
    )
    await callback.message.edit_text(text, reply_markup=admin_userlist_menu())
    await callback.answer()


async def _render_userlist_page(callback: types.CallbackQuery, list_kind: str, page: int):
    per_page = 10
    if list_kind == "active":
        users = db.get_customers_page(page, per_page)
        total = db.count_customers()
        title = "🟢 مشتریان فعال (خریدکرده)"
    else:
        users = db.get_all_users_page(page, per_page)
        total = db.count_users()
        title = "👥 کل کاربران"

    has_next = (page + 1) * per_page < total
    if not users and page == 0:
        text = f"{title}\n\nهنوز هیچ کاربری در این لیست نیست."
    else:
        start = page * per_page + 1
        text = f"{title} — {total} نفر (مرتب‌شده بر اساس بیشترین خرید)\nنمایش {start} تا {start + len(users) - 1}:\n\n"
        text += "برای مدیریت هرکدام روی نامش بزن 👇"

    await callback.message.edit_text(
        text, reply_markup=admin_userlist_page_keyboard(users, page, has_next, list_kind)
    )
    await callback.answer()


@router.callback_query(F.data == "admin_userlist_active")
async def admin_userlist_active(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await _render_userlist_page(callback, "active", 0)


@router.callback_query(F.data == "admin_userlist_all")
async def admin_userlist_all(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await _render_userlist_page(callback, "all", 0)


@router.callback_query(F.data.startswith("userpage_"))
async def admin_userlist_page_nav(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    _, list_kind, page_str = callback.data.split("_")
    await _render_userlist_page(callback, list_kind, int(page_str))


@router.callback_query(F.data.startswith("useropen_"))
async def admin_user_open(callback: types.CallbackQuery):
    """با زدن روی هرکدام از کاربران در لیست، مستقیم وارد صفحه‌ی مدیریت همان کاربر می‌شویم
    (همان صفحه‌ای که از طریق «🔍 جستجوی حرفه‌ای» هم بازش می‌شد)."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    uid = callback.data.replace("useropen_", "")
    user = db.get_user(uid)
    if user is None:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return

    stats = db.get_referral_stats(user["id"])
    text = (
        f"👤 {user['name']}\n"
        f"🆔 {user['telegram_id']}\n\n"
        f"👛 کیف پول آزاد: {user['wallet']:,} تومان\n"
        f"🔒 کیف پول مسدود: {user['locked_wallet']:,} تومان\n"
        f"🛒 کل خرید: {user['total_purchase']:,} تومان\n"
        f"📅 عضویت: {user['joined']}\n\n"
        f"🔗 کد دعوت: {user['invite_code']}\n"
        f"👥 دعوت: {stats['invited_count']} | موفق: {stats['successful_invites']}"
    )
    await _reply_with_user_actions(
        callback.message, text, user["telegram_id"], db.is_user_blocked(user["telegram_id"]), edit=True
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# 📒 حسابداری کاربر — تراکنش‌ها (کیف پول، خرید، شارژ، منشأ پول) با صفحه‌بندی
# ---------------------------------------------------------------------------
def _tx_type_label(tx_type: str) -> str:
    return {
        "charge": "💳 شارژ (تأیید کارت‌به‌کارت/دستی توسط ادمین)",
        "purchase": "🛒 خرید سرویس (کسر از کیف پول)",
        "referral_locked": "🔒 پاداش دعوت (در انتظار آزادسازی)",
        "referral_release": "🔓 آزادسازی پاداش دعوت",
        "referral_pending": "🔒 پاداش دعوت (در انتظار)",
    }.get(tx_type, f"📄 {tx_type}")


@router.callback_query(F.data.startswith("accounting_"))
async def admin_user_accounting(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    _, uid, page_str = callback.data.split("_")
    page = int(page_str)
    user = db.get_user(uid)
    if user is None:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return

    per_page = 10
    txs = db.get_transactions_page(user["id"], page, per_page)
    has_next = len(txs) == per_page

    text = (
        f"📒 حسابداری کاربر {user['name']} (🆔 {user['telegram_id']})\n\n"
        f"👛 کیف پول آزاد: {user['wallet']:,} تومان\n"
        f"🔒 کیف پول مسدود (پاداش دعوت در انتظار): {user['locked_wallet']:,} تومان\n"
        f"🛒 مجموع خرید: {user['total_purchase']:,} تومان\n\n"
        f"📋 تراکنش‌ها (صفحه {page + 1}):\n\n"
    )
    if not txs:
        text += "— تراکنشی در این صفحه نیست —"
    else:
        for tx in txs:
            sign = "+" if tx["amount"] >= 0 and tx["type"] in ("charge", "referral_release") else "-"
            text += (
                f"{_tx_type_label(tx['type'])}\n"
                f"{sign}{abs(tx['amount']):,} تومان | {tx['status']}\n"
                f"📝 {tx.get('description') or '-'}\n"
                f"🕐 {tx['created_at']}\n\n"
            )

    await callback.message.edit_text(text, reply_markup=admin_accounting_keyboard(uid, page, has_next))
    await callback.answer()


# ---------------------------------------------------------------------------
# 🔍 جستجوی حرفه‌ای
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "admin_search")
async def admin_search_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await callback.message.edit_text(
        "🔍 آیدی عددی یا کد دعوت کاربر را ارسال کنید:", reply_markup=admin_back_button()
    )
    await state.set_state(AdminStates.waiting_search_user)
    await callback.answer()


@router.message(AdminStates.waiting_search_user)
async def admin_search_result(message: types.Message, state: FSMContext):
    query = clean_numeric_id(message.text)

    user = db.get_user(query) if query.isdigit() else db.get_user_by_invite_code(query)
    if user is None:
        await message.answer("❌ کاربری با این مشخصات یافت نشد.", reply_markup=admin_back_button())
        return

    stats = db.get_referral_stats(user["id"])
    text = (
        f"👤 {user['name']}\n"
        f"🆔 {user['telegram_id']}\n\n"
        f"👛 کیف پول آزاد: {user['wallet']:,} تومان\n"
        f"🔒 کیف پول مسدود: {user['locked_wallet']:,} تومان\n"
        f"🛒 کل خرید: {user['total_purchase']:,} تومان\n"
        f"📅 عضویت: {user['joined']}\n\n"
        f"🔗 کد دعوت: {user['invite_code']}\n"
        f"👥 دعوت: {stats['invited_count']} | موفق: {stats['successful_invites']}"
    )
    await _reply_with_user_actions(
        message, text, user["telegram_id"], db.is_user_blocked(user["telegram_id"]), edit=False
    )
    await state.clear()


# ---------------------------------------------------------------------------
# 💳 شارژ کیف پول (تأیید/رد رسید + شارژ دستی)
# دسترسی از طریق «🔍 جستجوی حرفه‌ای» ← دکمه «💰 شارژ دستی» (برای بهینه شدن فضای منو)
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("approve_"))
async def approve_charge(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    if is_duplicate_action(f"approvecharge_{callback.data}") or not db.claim_admin_action(f"approvecharge_{callback.data}"):
        await callback.answer("⚠️ این رسید قبلاً پردازش شده.", show_alert=True)
        return

    _, uid, amount_str, receipt_id_str = callback.data.split("_")
    amount = int(amount_str)
    receipt_id = int(receipt_id_str)

    user = db.get_user(uid)
    if user is None:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return

    db.add_to_wallet(user["id"], amount, "شارژ کیف پول (تأیید رسید)")
    try:
        db.resolve_pending_receipt_by_id(receipt_id)
    except Exception:
        pass
    await _finish_receipt_message(
        callback.message, "\n\n✅ تأیید و شارژ شد.", queue_refresh=lambda: _render_pending_receipts(callback)
    )
    try:
        await send_notification_sticker(callback.bot, int(uid), "notif_wallet_charge")
        await callback.bot.send_message(int(uid), user_text("notif_wallet_charge_approved", amount=amount))
    except Exception:
        pass
    await _notify_main_admin_action(callback.bot, callback.from_user, "تأیید رسید شارژ کیف پول", uid, f"مبلغ {amount:,} تومان")
    await callback.answer("✅ شارژ شد.")


@router.callback_query(F.data.startswith("reject_"))
async def reject_charge(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    if is_duplicate_action(f"rejectcharge_{callback.data}") or not db.claim_admin_action(f"rejectcharge_{callback.data}"):
        await callback.answer("⚠️ این رسید قبلاً پردازش شده.", show_alert=True)
        return

    _, uid, receipt_id_str = callback.data.split("_")
    receipt_id = int(receipt_id_str)
    try:
        db.resolve_pending_receipt_by_id(receipt_id)
    except Exception:
        pass
    await _finish_receipt_message(
        callback.message, "\n\n❌ رد شد.", queue_refresh=lambda: _render_pending_receipts(callback)
    )
    try:
        await send_notification_sticker(callback.bot, int(uid), "notif_receipt_rejected")
        await callback.bot.send_message(int(uid), user_text("notif_receipt_rejected_short"))
    except Exception:
        pass
    await _notify_main_admin_action(callback.bot, callback.from_user, "رد رسید", uid if 'uid' in locals() else str(order_id) if 'order_id' in locals() else "", "")
    await callback.answer("❌ رد شد.")


@router.callback_query(F.data.startswith("custom_"))
async def custom_charge_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    uid = callback.data.replace("custom_", "")
    await state.update_data(charge_target=uid)
    await state.set_state(AdminStates.waiting_custom_amount)
    await callback.message.answer(f"💵 مبلغ شارژ برای کاربر {uid} را به تومان ارسال کنید:")
    await callback.answer()


@router.message(AdminStates.waiting_custom_amount)
async def custom_charge_apply(message: types.Message, state: FSMContext):
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await message.answer("❌ فقط عدد ارسال کنید.")
        return

    data = await state.get_data()
    uid = data.get("charge_target")
    amount = int(clean_numeric_id(message.text))

    user = db.get_user(uid)
    if user is None:
        await message.answer("❌ کاربر یافت نشد.")
        await state.clear()
        return

    db.add_to_wallet(user["id"], amount, "شارژ دستی توسط ادمین")
    try:
        db.resolve_pending_receipt("charge", uid)
    except Exception:
        pass
    await message.answer(f"✅ {amount:,} تومان به کیف پول کاربر {uid} اضافه شد.")
    try:
        await send_notification_sticker(message.bot, int(uid), "notif_wallet_charge")
        await message.bot.send_message(int(uid), user_text("notif_wallet_charged", amount=amount))
    except Exception:
        pass
    await state.clear()


# ---------------------------------------------------------------------------
# 💳 تأیید/رد رسید خرید کارت‌به‌کارت (سرویس)
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("approvepay|"))
async def approve_purchase(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    if is_duplicate_action(f"approvepay_{callback.data}") or not db.claim_admin_action(f"approvepay_{callback.data}"):
        await callback.answer("⚠️ این رسید قبلاً پردازش شده.", show_alert=True)
        return

    _, uid, plan_key, price_str, receipt_id_str = callback.data.split("|")
    price = int(price_str)
    receipt_id = int(receipt_id_str)
    plan = db.get_effective_plan(plan_key)
    user = db.get_user(uid)
    if user is None or plan is None:
        await callback.answer("❌ کاربر یا پلن یافت نشد.", show_alert=True)
        return

    if plan_key == FREE_TEST_PLAN_KEY and db.has_used_free_test(user["id"]):
        await callback.answer(
            "⚠️ این کاربر قبلاً از «تست رایگان» استفاده کرده؛ هر کاربر فقط یک‌بار می‌تواند این پلن را بگیرد.",
            show_alert=True,
        )
        return

    # 🐛 فیکس: کد تخفیف کارت‌به‌کارت حالا فقط همین‌جا (تأیید ادمین) مصرف
    # می‌شود، نه هنگام ارسال رسید؛ اگر ادمین رد کند سهم کد تخفیف مصرف نمی‌شود.
    pending = None
    try:
        pending = db.get_pending_receipt_by_id(receipt_id)
    except Exception:
        pending = None

    db.record_purchase(user["id"], price, f"خرید {plan['name']} (کارت به کارت)")

    if pending and pending.get("discount_code"):
        try:
            db.use_discount(pending["discount_code"], user["id"])
        except Exception:
            logging.getLogger(__name__).exception("خطا در مصرف کد تخفیف کارت‌به‌کارت")

    if plan.get("volume_gb", 0) >= REFERRAL_MIN_VOLUME_GB:
        try:
            db.complete_referral(user["id"])
        except ValueError:
            pass

    order_id = db.create_order(user["id"], plan_key, plan["name"], plan_type(plan_key), price)
    try:
        db.resolve_pending_receipt_by_id(receipt_id)
    except Exception:
        pass

    await _finish_receipt_message(
        callback.message, "\n\n✅ تأیید شد و خرید ثبت شد.", queue_refresh=lambda: _render_pending_receipts(callback)
    )
    try:
        _discount_note = f"\n🎟 کد تخفیف {pending['discount_code']} برای این خرید مصرف شد." if pending and pending.get("discount_code") else ""
        _confirm_text = user_text("notif_purchase_approved", plan_name=plan["name"], discount_note=_discount_note)
        if pending and pending.get("discount_code"):
            _confirm_text += f"\n🎟 کد تخفیف {pending['discount_code']} برای این خرید مصرف شد."
        await send_notification_sticker(callback.bot, int(uid), "notif_purchase_approved")
        await callback.bot.send_message(int(uid), _confirm_text)
    except Exception:
        pass

    # تأیید رسید VIP نباید خودش روش تحویل را انتخاب کند؛ ادمین باید بین
    # ارسال دستی و ساخت خودکار از پنل یکی را انتخاب کند.
    await callback.message.answer(
        "📤 رسید VIP تأیید شد. روش ارسال کانفیگ را انتخاب کن:",
        reply_markup=admin_purchase_notify_keyboard(uid, plan_key, order_id),
    )
    # بعد از تأیید، ادمین خودش روش تحویل را انتخاب می‌کند؛ هیچ ساخت خودکاری
    # در این مرحله اجرا نمی‌شود.
    await _notify_main_admin_action(callback.bot, callback.from_user, "تأیید خرید/سفارش", uid if 'uid' in locals() else str(order_id) if 'order_id' in locals() else "", "ثبت شد")
    await callback.answer("✅ تأیید شد.")


@router.callback_query(F.data.startswith("rejectpay|"))
async def reject_purchase(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    if is_duplicate_action(f"rejectpay_{callback.data}") or not db.claim_admin_action(f"rejectpay_{callback.data}"):
        await callback.answer("⚠️ این رسید قبلاً پردازش شده.", show_alert=True)
        return

    _, uid, receipt_id_str = callback.data.split("|")
    receipt_id = int(receipt_id_str)
    try:
        db.resolve_pending_receipt_by_id(receipt_id)
    except Exception:
        pass
    await _finish_receipt_message(
        callback.message, "\n\n❌ رد شد.", queue_refresh=lambda: _render_pending_receipts(callback)
    )
    try:
        await send_notification_sticker(callback.bot, int(uid), "notif_receipt_rejected")
        await callback.bot.send_message(int(uid), user_text("notif_receipt_rejected"))
    except Exception:
        pass
    await _notify_main_admin_action(callback.bot, callback.from_user, "رد رسید", uid if 'uid' in locals() else str(order_id) if 'order_id' in locals() else "", "")
    await callback.answer("❌ رد شد.")


# ---------------------------------------------------------------------------
# 🛠 تأیید/رد رسید کارت‌به‌کارت برای «بساز سرویس خودت» / «تمدید سرویس»
# ---------------------------------------------------------------------------




async def _log_fulfilled_order(
    bot, user: dict, *, plan_order_id=None, custom_order_id=None,
    target_config_id=None, service_id=None, service_name: str = "-",
    package_text: str = "-", expiry_text: str = "-",
):
    """پیام لاگ استاندارد سفارش را برای «کانال اعتماد» می‌سازد و ارسال می‌کند."""
    label = "🛒 خرید جدید"
    amount_text = "-"

    if plan_order_id:
        order = db.get_order(plan_order_id)
        if order:
            amount_text = f"{order['price']:,} تومان" if order["price"] else "رایگان"
            if order.get("order_type") == "test":
                label = "🎁 تست رایگان"
            elif target_config_id:
                label = "🔁 تمدید سرویس"
            else:
                label = "🛒 خرید جدید"
    elif custom_order_id:
        order = db.get_custom_order(custom_order_id)
        if order:
            amount_text = f"{order['price']:,} تومان" if order["price"] else "رایگان"
            label = "🔁 تمدید سرویس" if order.get("order_type") == "renew" else "🛠 سرویس سفارشی جدید"
    elif target_config_id:
        label = "🔁 تمدید سرویس"

    username = await alerts.fetch_username(bot, user["telegram_id"])
    await alerts.log_order_to_channel(
        bot,
        order_label=label,
        user=user,
        username=username,
        service_id=service_id,
        service_name=service_name,
        package_text=package_text,
        amount_text=amount_text,
        expiry_text=expiry_text,
    )


# ---------------------------------------------------------------------------
# 🛠 تأیید/رد سفارش «بساز سرویس خودت» با کارت‌به‌کارت
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("approvecustom_"))
async def approve_custom_order(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    try:
        order_id = int(callback.data.replace("approvecustom_", ""))
    except ValueError:
        await callback.answer("❌ شناسه سفارش نامعتبر است.", show_alert=True)
        return
    order = db.get_custom_order(order_id)
    if not order:
        await callback.answer("❌ سفارش یافت نشد.", show_alert=True)
        return
    if order.get("status") != "pending":
        await callback.answer("⚠️ این سفارش قبلاً پردازش شده است.", show_alert=True)
        return
    if not db.claim_admin_action(f"approve_custom:{order_id}"):
        await callback.answer("⚠️ این سفارش هم‌زمان توسط ادمین دیگری پردازش شد.", show_alert=True)
        return
    user = db.get_user_by_id(order["user_id"])
    if not user:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return
    db.record_purchase(user["id"], order["price"], "خرید سرویس سفارشی (کارت به کارت)")
    db.set_custom_order_status(order_id, "paid")
    if order.get("volume_gb", 0) >= REFERRAL_MIN_VOLUME_GB:
        try:
            db.complete_referral(user["id"])
        except ValueError:
            pass
    handled = False
    try:
        handled = await _auto_fulfill_custom_via_marzban(callback.bot, user, order_id, order["volume_gb"], order["days"], order.get("custom_name"))
    except Exception:
        logger.exception("auto fulfillment failed for custom card order %s", order_id)
    try:
        await callback.bot.send_message(int(user["telegram_id"]), t("custom_payment_approved", "✅ پرداخت شما تأیید شد!\nسرویس شما به‌زودی ساخته و ارسال می‌شود."))
    except Exception:
        pass
    if handled:
        await callback.message.edit_text("✅ سفارش سفارشی تأیید و به‌صورت خودکار ساخته و ارسال شد.")
    else:
        await callback.message.edit_text("✅ پرداخت تأیید شد و سفارش در صف ارسال قرار گرفت.", reply_markup=admin_custom_order_notify_keyboard(order_id))
    await callback.answer("✅ تأیید شد.")


@router.callback_query(F.data.startswith("rejectcustom_"))
async def reject_custom_order(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    try:
        order_id = int(callback.data.replace("rejectcustom_", ""))
    except ValueError:
        await callback.answer("❌ شناسه سفارش نامعتبر است.", show_alert=True)
        return
    order = db.get_custom_order(order_id)
    if not order:
        await callback.answer("❌ سفارش یافت نشد.", show_alert=True)
        return
    db.set_custom_order_status(order_id, "rejected")
    user = db.get_user_by_id(order["user_id"])
    if user:
        try:
            await callback.bot.send_message(int(user["telegram_id"]), t("notif_receipt_rejected", "❌ متأسفانه رسید پرداخت شما تأیید نشد. با پشتیبانی تماس بگیرید."))
        except Exception:
            pass
    await callback.message.edit_text("❌ سفارش سفارشی رد شد.")
    await callback.answer("❌ رد شد.")


@router.callback_query(F.data.startswith("sendcustomorder_"))
async def send_custom_order_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    try:
        order_id = int(callback.data.replace("sendcustomorder_", ""))
    except ValueError:
        await callback.answer("❌ شناسه سفارش نامعتبر است.", show_alert=True)
        return
    order = db.get_custom_order(order_id)
    if not order:
        await callback.answer("❌ سفارش یافت نشد.", show_alert=True)
        return
    user = db.get_user_by_id(order["user_id"])
    if not user:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return
    await _start_send_flow(callback, state, str(user["telegram_id"]), None, order_id, None, hint=" — سرویس سفارشی")


# ---------------------------------------------------------------------------
# 📤 ارسال کانفیگ — عکس کیوآرکد + لینک ساب (نام/حجم/مدت به‌صورت خودکار
# از روی خود لینک تشخیص داده می‌شود؛ اگر تشخیص خودکار جواب نداد، به‌صورت
# دستی از ادمین پرسیده می‌شود)
# ---------------------------------------------------------------------------
async def _start_send_flow(callback: types.CallbackQuery, state: FSMContext, uid: str,
                            target_config_id: int | None, order_id: int | None,
                            plan_order_id: int | None = None, hint: str = ""):
    await state.update_data(
        send_target_uid=uid,
        send_target_config_id=target_config_id,
        send_order_id=order_id,
        send_plan_order_id=plan_order_id,
        qr_file_id=None,
    )
    await state.set_state(AdminStates.waiting_send_qr_photo)
    await callback.message.answer(
        f"📤 ارسال کانفیگ برای کاربر {uid}{hint}\n\n📸 اول عکس کیوآرکد سرویس رو ارسال کن:"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sendvip_"))
async def send_config_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    raw = callback.data.replace("sendvip_", "")
    uid, _, plan_order_id = raw.partition("|")
    await _start_send_flow(
        callback, state, uid, target_config_id=None, order_id=None,
        plan_order_id=int(plan_order_id) if plan_order_id else None,
    )




@router.message(AdminStates.waiting_send_qr_photo, F.photo)
async def send_config_qr_received(message: types.Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(qr_file_id=file_id)
    await state.set_state(AdminStates.waiting_send_qr_link)
    await message.answer("🔗 حالا لینک ساب (Subscription) این سرویس رو ارسال کن:")


@router.message(AdminStates.waiting_send_qr_photo)
async def send_config_qr_wrong_format(message: types.Message):
    await message.answer("📸 لطفاً عکس کیوآرکد سرویس رو ارسال کن (نه متن).")


@router.message(AdminStates.waiting_send_qr_link)
async def send_config_link_received(message: types.Message, state: FSMContext):
    sub_link = (message.text or "").strip()
    if not sub_link.lower().startswith(("http://", "https://")):
        await message.answer("❌ این یک لینک معتبر نیست؛ لطفاً لینک ساب رو با http یا https ارسال کن:")
        return

    data = await state.get_data()
    order_id = data.get("send_order_id")
    order = db.get_custom_order(order_id) if order_id else None

    await message.answer("⏳ در حال تشخیص خودکار اطلاعات از روی لینک...")
    meta = await extract_meta(sub_link)
    userinfo = (meta or {}).get("userinfo") or {}
    fetched_name = (meta or {}).get("name")

    if order:
        # اطلاعات حجم/مدت از خود سفارش (که کاربر برایش پول پرداخت کرده) قابل‌اعتمادتر است
        volume_gb = order["volume_gb"]
        days = order["days"]
        name = fetched_name or order.get("custom_name") or "کاربر"
        await state.update_data(send_volume_gb=volume_gb, send_days=days, send_name=name, send_sub_link=sub_link)
        await _finalize_send(message, state)
        return

    volume_gb = _gb_from_bytes(userinfo.get("total"))
    days = days_remaining(userinfo.get("expire"))

    if volume_gb is not None and days is not None and fetched_name:
        await state.update_data(send_volume_gb=volume_gb, send_days=days, send_name=fetched_name, send_sub_link=sub_link)
        await _finalize_send(message, state)
        return

    # تشخیص خودکار کامل نبود؛ از ادمین می‌خواهیم دستی وارد کند
    await state.update_data(
        send_sub_link=sub_link,
        send_volume_gb=volume_gb,
        send_days=days,
        send_name=fetched_name,
    )
    await state.set_state(AdminStates.waiting_send_qr_manual)
    known = []
    if fetched_name:
        known.append(f"نام: {fetched_name}")
    if volume_gb is not None:
        known.append(f"حجم: {volume_gb} گیگ")
    if days is not None:
        known.append(f"مدت: {days} روز")
    known_text = ("\n✅ همین مقدار از روی لینک تشخیص داده شد: " + " | ".join(known)) if known else ""
    await message.answer(
        "⚠️ تشخیص خودکار کامل از روی این لینک ممکن نشد (احتمالاً این پنل هدر استاندارد ساب رو برنمی‌گردونه)."
        + known_text
        + "\n\nلطفاً این ۳ مورد رو هرکدام در یک خط، به همین ترتیب بفرست:\n"
        "نام کاربری سرویس\nحجم به گیگ (فقط عدد)\nمدت به روز (فقط عدد)\n\nمثال:\naminvpn1\n50\n30"
    )


@router.message(AdminStates.waiting_send_qr_manual)
async def send_config_manual_input(message: types.Message, state: FSMContext):
    lines = [l.strip() for l in (message.text or "").splitlines() if l.strip()]
    if len(lines) < 3:
        await message.answer("❌ باید دقیقاً ۳ خط بفرستی: نام / حجم (گیگ) / مدت (روز). دوباره امتحان کن:")
        return

    name = lines[0]
    volume_gb = parse_int_in_range(lines[1], 1, 100000)
    days = parse_int_in_range(lines[2], 1, 100000)
    if volume_gb is None or days is None:
        await message.answer("❌ خط دوم و سوم باید فقط عدد باشند (حجم و مدت). دوباره امتحان کن:")
        return

    await state.update_data(send_name=name, send_volume_gb=volume_gb, send_days=days)
    await _finalize_send(message, state)


async def _finalize_send(message: types.Message, state: FSMContext):
    data = await state.get_data()
    uid = data.get("send_target_uid")
    qr_file_id = data.get("qr_file_id")
    sub_link = data.get("send_sub_link")
    target_config_id = data.get("send_target_config_id")
    order_id = data.get("send_order_id")
    plan_order_id = data.get("send_plan_order_id")
    name = data.get("send_name") or "کاربر"

    # 🐛 فیکس: قبلاً متن تحویلی همیشه ثابت "نامحدود" نشان می‌داد و سقف کاربر (HWID Limit) واقعی پلن را نادیده می‌گرفت. حالا اگر این ارسال از روی یک سفارش پلن VIP باشد سقف کاربر همان پلن خوانده و نمایش داده می‌شود (0 = نامحدود).
    user_limit = None
    if plan_order_id:
        plan_order = db.get_order(plan_order_id)
        if plan_order and plan_order.get("plan_key"):
            order_plan = db.get_effective_plan(plan_order["plan_key"])
            if order_plan:
                user_limit = order_plan.get("user_limit")
    volume_gb = data.get("send_volume_gb")
    days = data.get("send_days")

    user = db.get_user(uid)
    if user is None or qr_file_id is None or sub_link is None:
        await message.answer("❌ مشکلی پیش آمد؛ لطفاً از ابتدا (📸 عکس کیوآرکد) دوباره امتحان کن.")
        await state.clear()
        return

    volume_text = _format_volume_gb_label(volume_gb)
    days_text = f"{days} روز" if days is not None else "نامحدود"
    user_limit_text = str(user_limit) if user_limit else "نامحدود"

    caption = (
        "✅ سرویس با موفقیت ایجاد شد\n\n"
        f"👤 نام کاربری سرویس : {name}\n"
        "🇺🇳 لوکیشن: مولتی لوکیشن+تانل\n"
        f"⏳ مدت زمان: {days_text}\n"
        f"🗜 حجم سرویس: {volume_text}\n"
        f"👤 تعداد کاربر:{user_limit_text}\n\n"
        "لینک اتصال:\n"
        f"{sub_link}\n\n"
        "🧑‍💻 شما میتوانید شیوه اتصال را با فشردن دکمه زیر دریافت کنید."
    )

    expiry_date = None
    if days is not None:
        expiry_date = (now_tehran_naive() + timedelta(days=days)).strftime("%Y-%m-%d")

    encrypted = crypto.encrypt_config(sub_link)
    plan_name = f"{name} | {volume_text} | {days_text}"

    if target_config_id:
        db.update_config(target_config_id, plan_name, encrypted, expiry=expiry_date, qr_file_id=qr_file_id)
    else:
        db.add_config(user["id"], plan_name, encrypted, expiry=expiry_date, config_type="vip", qr_file_id=qr_file_id)

    try:
        await send_notification_sticker(message.bot, int(uid), "notif_service_delivery")
        try:
            await message.bot.send_message(int(uid), user_text("notif_service_delivery", "📦 سرویس شما آماده شد ⬇️"), reply_markup=main_reply_keyboard())
            db.set_keyboard_hidden(int(uid), False)
        except Exception:
            pass
        await message.bot.send_photo(
            int(uid),
            qr_file_id,
            caption=caption,
            reply_markup=config_delivery_keyboard(bot_info.get('connection_guide_url')),
        )
        if order_id:
            db.set_custom_order_status(order_id, "fulfilled")
        if plan_order_id:
            db.set_order_status(plan_order_id, "fulfilled")
        await message.answer("✅ کانفیگ برای کاربر ارسال شد.")
        await _notify_main_admin_action(message.bot, message.from_user, "ارسال کانفیگ", uid, plan_name)
    except Exception as e:
        await message.answer(f"⚠️ سرویس ذخیره شد ولی ارسال پیام به کاربر ناموفق بود: {e}")

    await _log_fulfilled_order(
        message.bot, user, plan_order_id=plan_order_id, custom_order_id=order_id,
        target_config_id=target_config_id, service_name=name,
        package_text=f"{volume_text} | {days_text}", expiry_text=expiry_date or "نامحدود",
    )

    await state.clear()


# ---------------------------------------------------------------------------
# 📦 ارسال کانفیگ  (WireGuard) — بدون کیوآرکد/mirroring/تمدید:
# ادمین ابتدا شناسه سرویس و لینک ساب را وارد می‌کند، سپس هر تعداد فایل
# .conf که بخواهد آپلود می‌کند (هرکدام می‌تواند کپشن/لوکیشن جدا داشته باشد)
# و در پایان با زدن دکمه «✅ پایان ارسال فایل‌ها» همه‌ی فایل‌ها یک‌جا برای
# کاربر ارسال می‌شوند - دقیقاً مطابق فرمت فایلی که در کانال نمونه دیده می‌شود.
# ---------------------------------------------------------------------------












# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 📝 مدیریت جامع متن‌های کاربر و اعلان‌ها
# همه‌ی متن‌های قابل ویرایش در text_catalog.py تعریف شده‌اند.
TEXT_KEYS = TEXTS


def _chunk2(items):
    rows = []
    for i in range(0, len(items), 2):
        rows.append(items[i:i + 2])
    return rows


def _text_manager_keyboard(category: str | None = None):
    """مدیریت متن‌ها؛ در هر ردیف تا ۲ دکمه (🐛 فیکس: قبلاً ۳ دکمه در هر ردیف بود که روی صفحه‌کلید شلوغ و فشرده بود)."""
    buttons = []
    if category is None:
        categories = list(TEXT_CATEGORIES.keys())
        for i in range(0, len(categories), 2):
            row = []
            for cat in categories[i:i + 2]:
                count = len(TEXT_CATEGORIES[cat])
                row.append(types.InlineKeyboardButton(
                    text=f"📝 {cat.split(' ', 1)[-1]} ({count})",
                    callback_data=f"textcat_{categories.index(cat)}",
                    style="primary",
                ))
            buttons.append(row)
        buttons.append([types.InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="primary")])
        return types.InlineKeyboardMarkup(inline_keyboard=buttons)

    items = TEXT_CATEGORIES.get(category, [])
    for i in range(0, len(items), 2):
        row = []
        for key, default in items[i:i + 2]:
            value = user_text(key, default).replace("\n", " ")[:22]
            row.append(types.InlineKeyboardButton(
                text=f"✏️ {key[:14]} | {value}",
                callback_data=f"textedit_{key}",
                style="primary",
            ))
        buttons.append(row)
    buttons.append([types.InlineKeyboardButton(text="🔙 بازگشت به دسته‌ها", callback_data="admin_texts", style="primary")])
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "admin_texts")
async def admin_texts(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True); return
    await state.clear()
    await callback.message.edit_text(
        "📝 مدیریت جامع متن‌های کاربر و اعلان‌ها\n\nیک بخش را انتخاب کنید:",
        reply_markup=_text_manager_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("textcat_"))
async def admin_text_category(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True); return
    try:
        index = int(callback.data.replace("textcat_", "", 1))
        category = list(TEXT_CATEGORIES.keys())[index]
    except Exception:
        await callback.answer("❌ بخش متن پیدا نشد.", show_alert=True); return
    await state.clear()
    note = "\n\n🔒 متن انقضای فاکتور کارت‌به‌کارت سیستمی است و از اینجا قابل تغییر نیست." if "فاکتور کارت‌به‌کارت" in category else ""
    await callback.message.edit_text(
        f"📝 {category}\n\nمتن موردنظر را برای ویرایش انتخاب کنید:{note}",
        reply_markup=_text_manager_keyboard(category),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("textedit_"))
async def admin_text_edit_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True); return
    key = callback.data.replace("textedit_", "", 1)
    if key not in TEXT_KEYS:
        await callback.answer("❌ متن پیدا نشد.", show_alert=True); return
    await state.update_data(text_override_key=key)
    await state.set_state(AdminStates.waiting_text_override_value)
    current = user_text(key, TEXT_KEYS[key])
    placeholder_note = "\n\nمتغیرهای قابل استفاده: " + ", ".join("{" + x + "}" for x in _template_vars(TEXT_KEYS[key])) if _template_vars(TEXT_KEYS[key]) else ""
    lock_note = "\n\n🔒 توجه: جمله‌ی زمان انقضای فاکتور در کد سیستمی تولید می‌شود و جزو این متن نیست." if key.startswith("invoice_") else ""
    await callback.message.edit_text(
        f"✏️ ویرایش متن: {key}\n\nمتن فعلی:\n{current}{placeholder_note}{lock_note}\n\nمتن جدید را ارسال کنید:",
        reply_markup=admin_back_button(),
    )
    await callback.answer()


def _template_vars(template: str) -> list[str]:
    import string
    vars_found = []
    for _, field, _, _ in string.Formatter().parse(template):
        if field and field not in vars_found:
            vars_found.append(field.split("!", 1)[0].split(":", 1)[0])
    return vars_found


@router.message(AdminStates.waiting_text_override_value)
async def admin_text_edit_save(message: types.Message, state: FSMContext):
    data = await state.get_data(); key = data.get("text_override_key")
    if key not in TEXT_KEYS:
        await state.clear(); await message.answer("❌ عملیات منقضی شد."); return
    value = (message.text or "")
    if not value.strip():
        await message.answer("❌ متن نمی‌تواند خالی باشد:"); return
    # محدودیت واقعی Telegram برای متن پیام 4096 واحد UTF-16 است. متن را عمداً روی 3800 قفل نمی‌کنیم؛
    # Premium/Custom Emoji نباید باعث رد شدن یا بی‌دلیل کوتاه شدن متن ادمین شود.
    from utils import _telegram_utf16_len
    if _telegram_utf16_len(value) > 4096:
        await message.answer(f"❌ متن از سقف ۴۰۹۶ واحد UTF-16 تلگرام بیشتر است ({_telegram_utf16_len(value)}). متن کوتاه‌تری ارسال کنید:")
        return
    # جلوگیری از خراب‌شدن فاکتور با حذف متغیرهای سیستمی
    required = set(_template_vars(TEXT_KEYS[key]))
    if required:
        supplied = set(_template_vars(value))
        missing = required - supplied
        if missing:
            await message.answer("❌ این متغیرهای ضروری حذف شده‌اند: " + ", ".join("{" + x + "}" for x in sorted(missing)))
            return
    entities = []
    for entity in (message.entities or []):
        try:
            entities.append(entity.model_dump(exclude_none=True))
        except Exception:
            try:
                entities.append(entity.dict(exclude_none=True))
            except Exception:
                pass
    db.set_text_override(key, value, entities=entities)
    refresh_user_text(key)
    await state.clear()
    category = CATEGORY_BY_KEY.get(key)
    await message.answer("✅ متن ذخیره شد.", reply_markup=_text_manager_keyboard(category))


# 📥 صف سفارشات — لیست خریدهای تأییدشده‌ای که هنوز کانفیگ‌شان ارسال نشده،
# چه خرید پلن معمولی (VIP) و چه سفارش سفارشی/تمدید.
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "admin_orders_off")
async def admin_orders_off(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    db.set_orders_enabled(False)
    users = db.get_all_users()
    sent, failed = 0, 0
    status_msg = await callback.message.answer(f"⏳ در حال اطلاع‌رسانی به {len(users)} کاربر...")
    for u in users:
        try:
            await callback.bot.send_message(
                int(u["telegram_id"]),
                user_text("orders_closed", "🔴 ربات به دلیل حجم سفارشات بالا موقتاً بسته می‌باشد.") + "\n\nروشن شدن دوباره‌ی آن اطلاع‌رسانی خواهد شد.",
            )
            sent += 1
        except Exception:
            failed += 1
    await status_msg.edit_text(f"🔴 بخش سفارشات خاموش شد. اطلاع‌رسانی به {sent} نفر موفق، {failed} نفر ناموفق.")
    await callback.message.edit_text("👨‍💻 پنل مدیریت:", reply_markup=_admin_panel_kb_for(callback.from_user.id))
    await callback.answer()


@router.callback_query(F.data == "admin_orders_on")
async def admin_orders_on(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    db.set_orders_enabled(True)
    users = db.get_all_users()
    sent, failed = 0, 0
    status_msg = await callback.message.answer(f"⏳ در حال اطلاع‌رسانی به {len(users)} کاربر...")
    for u in users:
        try:
            await callback.bot.send_message(
                int(u["telegram_id"]),
                user_text("orders_opened", "🟢 ربات مجدداً فعال شد!") + "\n\nبا زدن /start می‌توانید دوباره سفارش ثبت کنید.",
            )
            sent += 1
        except Exception:
            failed += 1
    await status_msg.edit_text(f"🟢 بخش سفارشات روشن شد. اطلاع‌رسانی به {sent} نفر موفق، {failed} نفر ناموفق.")
    await callback.message.edit_text("👨‍💻 پنل مدیریت:", reply_markup=_admin_panel_kb_for(callback.from_user.id))
    await callback.answer()


async def _render_order_queue(callback: types.CallbackQuery):
    pending = db.get_pending_orders(limit=25)
    pending_custom = db.get_pending_custom_orders(limit=25)
    for o in pending + pending_custom:
        u = db.get_user_by_id(o["user_id"])
        o["telegram_id"] = u["telegram_id"] if u else ""
    total = len(pending) + len(pending_custom)
    text = ("📦 سفارش‌های در انتظار\n\n✅ در حال حاضر هیچ سفارش در انتظار ارسالی وجود ندارد." if not total else f"📦 سفارش‌های در انتظار — {total} مورد در انتظار ارسال\n\nروی هرکدوم بزن تا مسیر ارسالش شروع بشه 👇")
    try:
        markup = admin_order_queue_keyboard(pending, pending_custom)
    except TypeError:
        markup = admin_order_queue_keyboard(pending)
    await callback.message.edit_text(text, reply_markup=markup)

@router.callback_query(F.data == "admin_request_queue")
async def admin_request_queue(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    order_count = len(db.get_pending_orders(limit=200)) + len(db.get_pending_custom_orders(limit=200))
    receipt_count = len(db.get_pending_receipts(limit=200)) + len(db.get_pending_custom_order_receipts(limit=200))
    await callback.message.edit_text(
        "📥 صف درخواست‌ها\n\nچه چیزی رو می‌خوای بررسی کنی؟ 👇",
        reply_markup=admin_request_queue_menu(order_count, receipt_count),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_order_queue")
async def admin_order_queue(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await _render_order_queue(callback)
    await callback.answer()


# ---------------------------------------------------------------------------
# 🧾 رسیدهای در انتظار تایید — همه‌ی رسیدهای شارژ کیف پول و خرید کارت‌به‌کارت
# (پلن ثابت + بساز سرویس خودت) که هنوز ادمین تایید/رد نکرده، در یک لیست.
# تایید/رد از همینجا دقیقاً همان مسیر همیشگی (پیام فوروارد‌شده در چت ادمین)
# را صدا می‌زند، فقط یک راه میان‌بر برای دیدن همه‌چیز یک‌جاست.
# ---------------------------------------------------------------------------
async def _render_pending_receipts(callback: types.CallbackQuery):
    receipts = db.get_pending_receipts(limit=25)
    custom_receipts = db.get_pending_custom_order_receipts(limit=25)
    for co in custom_receipts:
        u = db.get_user_by_id(co["user_id"])
        co["telegram_id"] = u["telegram_id"] if u else ""
    total = len(receipts) + len(custom_receipts)
    text = ("🧾 رسیدهای در انتظار تایید\n\n✅ در حال حاضر هیچ رسید در انتظار تاییدی وجود ندارد." if not total else f"🧾 رسیدهای در انتظار تایید — {total} مورد\n\nروی ✅ برای تایید یا ❌ برای رد بزن 👇")
    markup = admin_pending_receipts_keyboard(receipts, custom_receipts)
    await callback.message.edit_text(text, reply_markup=markup)
# ---------------------------------------------------------------------------
# 🧾 باز کردن رسید از «صف درخواست‌ها»
# دکمه‌های لیست فقط شناسه رسید را دارند و با کلیک، پیام بررسی همان رسید
# با همان دکمه‌های تأیید/رد اصلی نمایش داده می‌شود.
# ---------------------------------------------------------------------------
def _receipt_field(row: dict, *names, default=None):
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return default


def _receipt_is_purchase(row: dict) -> bool:
    plan_key = _receipt_field(row, "plan_key", "plan", "plan_slug")
    kind = str(_receipt_field(row, "kind", "receipt_type", "type", "source", default="")).lower()
    return bool(plan_key) or any(x in kind for x in ("purchase", "plan", "service", "buy", "card"))


@router.callback_query(F.data.startswith("receipt_"))
async def open_pending_receipt(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    try:
        receipt_id = int(callback.data.replace("receipt_", "", 1))
    except ValueError:
        await callback.answer("❌ شناسه رسید نامعتبر است.", show_alert=True)
        return
    try:
        receipt = db.get_pending_receipt_by_id(receipt_id)
    except Exception:
        receipt = None
    if not receipt:
        await callback.answer("⚠️ این رسید دیگر در انتظار بررسی نیست.", show_alert=True)
        await _render_pending_receipts(callback)
        return

    uid = str(_receipt_field(receipt, "telegram_id", "uid", "user_telegram_id", "user_id", default=""))
    amount = int(_receipt_field(receipt, "amount", "price", "paid_amount", default=0) or 0)
    plan_key = _receipt_field(receipt, "plan_key", "plan", "plan_slug")
    plan = db.get_effective_plan(plan_key) if plan_key else None
    discount = _receipt_field(receipt, "discount_code", default=None)
    note = f"\n🎟 کد تخفیف: {discount}" if discount else ""

    if _receipt_is_purchase(receipt):
        if not plan_key or not plan:
            await callback.message.answer(
                f"🧾 رسید خرید #{receipt_id}\n\n👤 کاربر: {uid}\n💰 مبلغ: {amount:,} تومان\n\n⚠️ پلن این رسید در تنظیمات فعلی پیدا نشد؛ ابتدا پلن را بررسی کنید."
            )
        else:
            text = (
                f"🧾 رسید خرید #{receipt_id}\n\n👤 کاربر: {uid}\n"
                f"📦 پلن: {plan.get('name', plan_key)}\n"
                f"💰 مبلغ: {amount or plan.get('price', 0):,} تومان{note}\n\n"
                "از دکمه‌های زیر برای تأیید یا رد رسید استفاده کنید."
            )
            await callback.message.answer(
                text,
                reply_markup=admin_purchase_card_approval_keyboard(
                    uid, plan_key, amount or int(plan.get("price", 0) or 0), receipt_id
                ),
            )
    else:
        await callback.message.answer(
            f"🧾 رسید شارژ کیف پول #{receipt_id}\n\n👤 کاربر: {uid}\n💰 مبلغ: {amount:,} تومان\n\n"
            "از دکمه‌های زیر برای تأیید یا رد رسید استفاده کنید.",
            reply_markup=admin_charge_approval_keyboard(uid, amount, receipt_id),
        )
    await callback.answer("🧾 رسید آماده بررسی شد.")


@router.callback_query(F.data.startswith("customreceipt_"))
async def open_pending_custom_receipt(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    try:
        order_id = int(callback.data.replace("customreceipt_", "", 1))
    except ValueError:
        await callback.answer("❌ شناسه سفارش نامعتبر است.", show_alert=True)
        return
    order = db.get_custom_order(order_id)
    if not order or order.get("status") != "pending":
        await callback.answer("⚠️ این رسید دیگر در انتظار بررسی نیست.", show_alert=True)
        await _render_pending_receipts(callback)
        return
    user = db.get_user_by_id(order["user_id"])
    uid = user["telegram_id"] if user else order.get("telegram_id", "?")
    kind = "تمدید سرویس" if order.get("order_type") == "renew" else "سرویس خودت رو بساز"
    text = (
        f"🧾 رسید {kind} #{order_id}\n\n👤 کاربر: {uid}\n"
        f"💾 حجم: {order.get('volume_gb', '?')} گیگ\n"
        f"⏳ مدت: {order.get('days', '?')} روز\n"
        f"💰 مبلغ: {int(order.get('price', 0) or 0):,} تومان\n"
        f"📝 نام سرویس: {order.get('custom_name') or 'بدون نام'}\n\n"
        "از دکمه‌های زیر برای تأیید یا رد رسید استفاده کنید."
    )
    await callback.message.answer(text, reply_markup=admin_custom_order_card_approval_keyboard(order_id))
    await callback.answer("🧾 رسید آماده بررسی شد.")



@router.callback_query(F.data == "admin_pending_receipts")
async def admin_pending_receipts(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await _render_pending_receipts(callback)
    await callback.answer()


@router.callback_query(F.data == "clearreceipts_confirm")
async def clear_receipts_confirm(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await callback.message.edit_text(
        "⚠️ مطمئنی می‌خوای همه‌ی رسیدهای این لیست رو بررسی‌شده علامت بزنی؟\n"
        "(توجه: این کار فقط لیست رو خالی می‌کنه؛ اگر هنوز به کاربری تایید/رد اعلام نکردی، "
        "پیام اصلی رسیدش همچنان توی چتت هست و باید از همونجا اقدام کنی.)",
        reply_markup=admin_clear_receipts_confirm_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "clearreceipts_do")
async def clear_receipts_do(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    db.dismiss_all_pending_receipts()
    await _render_pending_receipts(callback)
    await callback.answer("🧹 لیست خالی شد.")


@router.callback_query(F.data.startswith("dismissorder_"))
async def dismiss_order(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    order_id = int(callback.data.replace("dismissorder_", ""))
    db.set_order_status(order_id, "dismissed")
    await _render_order_queue(callback)
    await callback.answer("🗑 از صف پاک شد.")




@router.callback_query(F.data == "clearorders_confirm")
async def clear_orders_confirm(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await callback.message.edit_text(
        "⚠️ مطمئنی می‌خوای همه‌ی سفارش‌های این صف رو پاک کنی؟\n"
        "(این کار فقط سفارش‌ها رو از صف حذف می‌کنه؛ اگه کانفیگ کسی رو نفرستادی، دیگه اینجا یادآوریش نمی‌مونه.)",
        reply_markup=admin_clear_orders_confirm_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "clearorders_do")
async def clear_orders_do(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    pending = db.get_pending_orders(limit=1000)
    for o in pending:
        db.set_order_status(o["id"], "dismissed")
    await _render_order_queue(callback)
    await callback.answer(f"🧹 {len(pending)} سفارش پاک شد.")


# ---------------------------------------------------------------------------
# 📦 مشاهده و مدیریت سرویس‌های یک کاربر (لیست/حذف نرم/ادیت لینک ساب و کیوآرکد/
# مدیریت فایل‌های ) — همه از طریق «🔍 جستجوی حرفه‌ای»
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("useractions_"))
async def admin_user_actions_back(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    uid = callback.data.replace("useractions_", "")
    await _reply_with_user_actions(
        callback.message, f"👤 مدیریت کاربر {uid}", uid, db.is_user_blocked(uid), edit=True
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# ✉️ پیام خصوصی ادمین به یک کاربر خاص (متن/عکس/فیلم/فوروارد)
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("pm_"))
async def admin_pm_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    uid = callback.data.replace("pm_", "")
    await state.set_state(AdminStates.waiting_pm_message)
    await state.update_data(pm_target_uid=uid)
    await callback.message.edit_text(
        f"✉️ پیامی که می‌خواهید به کاربر {uid} ارسال شود را بفرستید (متن، عکس، فیلم یا فوروارد هم پذیرفه است):",
        reply_markup=admin_pm_cancel_keyboard(uid),
    )
    await callback.answer()


@router.message(AdminStates.waiting_pm_message)
async def admin_pm_send(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    data = await state.get_data()
    uid = data.get("pm_target_uid")
    if not uid:
        await state.clear()
        return
    try:
        await send_notification_sticker(message.bot, int(uid), "notif_personal_message")
        await message.bot.copy_message(int(uid), message.chat.id, message.message_id)
        await _reply_with_user_actions(message, "✅ پیام خصوصی برای کاربر ارسال شد.", uid, db.is_user_blocked(uid), edit=False)
    except Exception:
        await _reply_with_user_actions(message, "❌ ارسال پیام ناموفق بود (ممکن است کاربر ربات را مسدود کرده باشد).", uid, db.is_user_blocked(uid), edit=False)
    await state.clear()


# ---------------------------------------------------------------------------
# 🚫 مسدود/رفع مسدودیت کاربر — کاربر‌های مسدود نمی‌توانند از ربات استفاده کنند
# (بررسی در middleware/start.py)
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("toggleblock_"))
async def admin_toggle_block(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    uid = callback.data.replace("toggleblock_", "")
    currently_blocked = db.is_user_blocked(uid)
    db.set_user_blocked(uid, not currently_blocked)

    user = db.get_user(uid)
    if user is None:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return
    stats = db.get_referral_stats(user["id"])
    text = (
        f"👤 {user['name']}\n"
        f"🆔 {user['telegram_id']}\n\n"
        f"💛 کیف پول آزاد: {user['wallet']:,} تومان\n"
        f"🔒 کیف پول مسدود: {user['locked_wallet']:,} تومان\n"
        f"🛍 کل خرید: {user['total_purchase']:,} تومان\n"
        f"📅 عضویت: {user['joined']}\n\n"
        f"🔗 کد دعوت: {user['invite_code']}\n"
        f"👥 دعوت: {stats['invited_count']} | موفق: {stats['successful_invites']}\n\n"
        + ("🚫 وضعیت: مسدود" if not currently_blocked else "✅ وضعیت: رفع مسدودیت شد")
    )
    await _reply_with_user_actions(callback.message, text, user["telegram_id"], not currently_blocked, edit=True)
    await callback.answer("🚫 کاربر مسدود شد." if not currently_blocked else "✅ مسدودیت برداشته شد.")


@router.callback_query(F.data.startswith("svcs_"))
async def admin_view_user_services(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    uid = callback.data.replace("svcs_", "")
    user = db.get_user(uid)
    if user is None:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return

    configs = db.get_configs(user["id"], include_deleted=True)
    if not configs:
        await callback.message.edit_text(
            f"📦 کاربر {uid} هنوز هیچ سرویسی نداره.",
            reply_markup=admin_back_button(),
        )
    else:
        await callback.message.edit_text(
            f"📦 سرویس‌های کاربر {uid}\n\n❌ یعنی توسط خودِ کاربر حذف شده (ولی برای شما همچنان قابل‌مشاهده‌ست).\n\nروی هرکدوم بزن برای جزئیات و مدیریت 👇",
            reply_markup=admin_services_list_keyboard(configs, uid),
        )
    await callback.answer()


def _remaining_days_from_date_str(date_str) -> int | None:
    """تعداد روز باقی‌مانده تا انقضا را از یک رشته تاریخ به فرمت YYYY-MM-DD محاسبه می‌کند."""
    if not date_str:
        return None
    try:
        exp_date = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
        delta = exp_date - now_tehran_naive()
        return delta.days
    except Exception:
        return None


async def _service_detail_text(cfg: dict) -> str:
    """
    توجه: قبلاً این متن با parse_mode="Markdown" (نسخه‌ی قدیمی مارک‌داون
    تلگرام) فرستاده می‌شد و cfg['plan'] بدون هیچ escape‌ای مستقیم داخل متن
    قرار می‌گرفت. نسخه‌ی قدیمی Markdown تلگرام امکان escape کردن کاراکترهای
    خاص رو نداره؛ پس اگر نام پلن یک زیرخط (_) تک و جفت‌نشده داشت (مثل
    "Businesss_vpn - 1090174")، پارسر اون رو شروع ایتالیک در نظر می‌گرفت و
    چون بسته نمی‌شد، کل درخواست ویرایش پیام با خطای "can't parse entities"
    رد می‌شد و صفحه‌ی جزئیات سرویس اصلاً نمایش داده نمی‌شد.
    راه‌حل: استفاده از HTML به‌جای Markdown، چون HTML یک تابع escape رسمی و
    قابل‌اعتماد داره (html.escape) و این مشکل اصلاً پیش نمیاد.
    """
    icon = "🚀" if cfg.get("type", "vip") == "vip" else "📦"
    status = "❌ حذف‌شده (توسط کاربر یا ادمین)" if cfg.get("deleted") else "✅ فعال"
    try:
        sub_preview = crypto.decrypt_config(cfg["config"])
    except Exception:
        sub_preview = "⚠️ خطا در رمزگشایی"

    plan_safe = html.escape(str(cfg.get("plan", "")))
    created_safe = html.escape(str(cfg.get("created_at", "")))
    sub_preview_safe = html.escape(sub_preview)

    text = (
        f"{icon} {plan_safe}\n\n"
        f"📌 وضعیت: {status}\n"
        f"📆 تاریخ ایجاد: {created_safe}\n"
    )
    if cfg.get("service_id"):
        text += f"🆔 شناسه سرویس: {html.escape(str(cfg['service_id']))}\n"

    usage = None
    sub_url = sub_preview if sub_preview.lower().startswith(("http://", "https://")) else None
    if cfg.get("type", "vip") == "vip" and sub_url:
        try:
            usage = await fetch_subscription_info(sub_url)
        except Exception:
            usage = None

    if usage:
        total = usage.get("total")
        used = (usage.get("upload") or 0) + (usage.get("download") or 0)
        remaining_bytes = (total - used) if total else None

        text += "\n📊 وضعیت مصرف (لحظه‌ای):\n"
        if total:
            text += f"   • حجم کل: {html.escape(format_bytes(total))}\n"
        text += f"   • مصرف‌شده: {html.escape(format_bytes(used))}\n"
        if remaining_bytes is not None:
            text += f"   • باقی‌مانده: {html.escape(format_bytes(remaining_bytes))}\n"
        if total:
            percent = min(100, round(used / total * 100))
            text += f"\n{usage_bar(percent)} {percent}٪ مصرف شده\n"
        text += f"\n⏰ تاریخ انقضا: {html.escape(format_expire(usage.get('expire')))}\n"
        remaining_days = days_remaining(usage.get("expire"))
        if remaining_days is not None:
            text += "⛔️ منقضی شده\n" if remaining_days <= 0 else f"⌛️ زمان باقی‌مانده: {remaining_days} روز\n"
    elif cfg.get("expiry"):
        text += f"⏰ انقضا: {html.escape(str(cfg['expiry']))}\n"
        remaining_days = _remaining_days_from_date_str(cfg.get("expiry"))
        if remaining_days is not None:
            if remaining_days <= 0:
                text += "⌛️ زمان باقی‌مانده: ⛔️ منقضی شده\n"
            else:
                text += f"⌛️ زمان باقی‌مانده: {remaining_days} روز\n"

    text += f"\n🔗 لینک ساب فعلی:\n<code>{sub_preview_safe}</code>\n"
    text += f"\n🎫 کیوآرکد: {'ثبت شده ✅' if cfg.get('qr_file_id') else 'ثبت نشده ❌'}"
    return text


async def _render_service_detail(callback: types.CallbackQuery, cfg_id: int):
    cfg = db.get_config_by_id(cfg_id)
    if cfg is None:
        await callback.answer("❌ سرویس یافت نشد.", show_alert=True)
        return
    owner = db.get_user_by_id(cfg["user_id"])
    uid = owner["telegram_id"] if owner else ""

    text = await _service_detail_text(cfg)
    kb = admin_service_detail_keyboard(cfg, uid)
    # -----------------------------------------------------------------
    # علت اصلی خطای "خطایی پیش آمد..." روی این صفحه: نام پلن (cfg['plan'])
    # می‌تونه هرچیزی باشه (مثلاً چیزی شبیه "Businesss_vpn - 1090174") و اگر
    # داخلش یک زیرخط (_) تکی و جفت‌نشده باشه، پارسر Markdown قدیمی تلگرام
    # اون رو شروع ایتالیک در نظر می‌گیره و چون بسته نمی‌شه، کل پیام با خطای
    # "can't parse entities" رد می‌شه. قبلاً هیچ try/except اینجا نبود، پس
    # این خطا مستقیم می‌رفت به هندلر سراسری و کاربر فقط "خطایی پیش آمد" رو
    # می‌بیند بدون اینکه جزئیات سرویس اصلاً نمایش داده بشه. حالا اگر مارک‌داون
    # شکست بخوره، همون متن رو بدون فرمت (parse_mode=None) دوباره می‌فرستیم.
    # -----------------------------------------------------------------
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        if "message is not modified" in str(e).lower():
            pass
        else:
            logger.exception("خطا در نمایش جزئیات سرویس ادمین برای cfg_id=%s", cfg_id)
            try:
                await callback.message.edit_text(text, parse_mode=None, reply_markup=kb)
            except Exception:
                logger.exception("خطا در fallback بدون فرمت برای جزئیات سرویس cfg_id=%s", cfg_id)
                await callback.answer("❌ خطا در نمایش جزئیات سرویس. دوباره تلاش کنید.", show_alert=True)
                return
    await callback.answer()


@router.callback_query(F.data.startswith("svcdetail_"))
async def admin_service_detail(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    cfg_id = int(callback.data.replace("svcdetail_", ""))
    await _render_service_detail(callback, cfg_id)


@router.callback_query(F.data.startswith("svcdelete_"))
async def admin_service_delete(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    cfg_id = int(callback.data.replace("svcdelete_", ""))
    db.set_config_deleted(cfg_id, True)
    await _render_service_detail(callback, cfg_id)


@router.callback_query(F.data.startswith("svcrestore_"))
async def admin_service_restore(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    cfg_id = int(callback.data.replace("svcrestore_", ""))
    db.set_config_deleted(cfg_id, False)
    await _render_service_detail(callback, cfg_id)


@router.callback_query(F.data.startswith("svcpurge_"))
async def admin_service_purge_confirm(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    cfg_id = int(callback.data.replace("svcpurge_", ""))
    await callback.message.edit_text(
        "⚠️ این کار غیرقابل بازگشته و کل اطلاعات این سرویس (شامل فایل‌های ) برای همیشه پاک می‌شه.\n\nمطمئنی؟",
        reply_markup=admin_purge_confirm_keyboard(cfg_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("svcpurgeconfirm_"))
async def admin_service_purge_apply(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    cfg_id = int(callback.data.replace("svcpurgeconfirm_", ""))
    cfg = db.get_config_by_id(cfg_id)
    owner = db.get_user_by_id(cfg["user_id"]) if cfg else None
    uid = owner["telegram_id"] if owner else ""
    db.delete_config_permanently(cfg_id)
    await callback.message.edit_text("✅ سرویس برای همیشه حذف شد.", reply_markup=admin_back_button())
    await callback.answer()


@router.callback_query(F.data.startswith("svcedit_link_"))
async def admin_service_edit_link_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    cfg_id = int(callback.data.replace("svcedit_link_", ""))
    await state.update_data(edit_config_id=cfg_id)
    await state.set_state(AdminStates.waiting_edit_sublink)
    await callback.message.answer("🔗 لینک ساب جدید این سرویس رو ارسال کن:")
    await callback.answer()


@router.message(AdminStates.waiting_edit_sublink)
async def admin_service_edit_link_apply(message: types.Message, state: FSMContext):
    new_link = (message.text or "").strip()
    if not new_link.lower().startswith(("http://", "https://")):
        await message.answer("❌ این یک لینک معتبر نیست؛ لطفاً لینک رو با http یا https ارسال کن:")
        return

    data = await state.get_data()
    cfg_id = data.get("edit_config_id")
    cfg = db.get_config_by_id(cfg_id) if cfg_id else None
    if cfg is None:
        await message.answer("❌ سرویس یافت نشد.")
        await state.clear()
        return

    db.update_config_link(cfg_id, crypto.encrypt_config(new_link))
    await message.answer("✅ لینک ساب سرویس بروزرسانی شد.", reply_markup=admin_back_button())
    await state.clear()


@router.callback_query(F.data.startswith("svcedit_qr_"))
async def admin_service_edit_qr_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    cfg_id = int(callback.data.replace("svcedit_qr_", ""))
    await state.update_data(edit_config_id=cfg_id)
    await state.set_state(AdminStates.waiting_edit_qr)
    await callback.message.answer("🖼 عکس کیوآرکد جدید این سرویس رو ارسال کن:")
    await callback.answer()


@router.message(AdminStates.waiting_edit_qr, F.photo)
async def admin_service_edit_qr_apply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    cfg_id = data.get("edit_config_id")
    cfg = db.get_config_by_id(cfg_id) if cfg_id else None
    if cfg is None:
        await message.answer("❌ سرویس یافت نشد.")
        await state.clear()
        return

    db.set_config_qr(cfg_id, message.photo[-1].file_id)
    await message.answer("✅ عکس کیوآرکد سرویس بروزرسانی شد.", reply_markup=admin_back_button())
    await state.clear()


@router.message(AdminStates.waiting_edit_qr)
async def admin_service_edit_qr_wrong_format(message: types.Message):
    await message.answer("📸 لطفاً عکس کیوآرکد رو ارسال کن (نه متن).")














# ---------------------------------------------------------------------------
# 🎟 مدیریت تخفیف (ساخت کد تخفیف به‌صورت گام‌به‌گام)
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "admin_discount")
async def admin_discount_list(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    discounts = db.get_all_discounts()
    if not discounts:
        text = "🎟 هیچ کد تخفیفی هنوز ثبت نشده.\n\nبرای ساخت کد جدید، دکمه‌ی زیر را بزنید 👇"
    else:
        text = "🎟 کدهای تخفیف فعال:\n\nبرای مشاهده و ویرایش جزئیات هر کد، روی آن بزنید 👇"

    await callback.message.edit_text(text, reply_markup=admin_discount_menu(discounts))
    await callback.answer()


def _discount_detail_text(d: dict) -> str:
    if d.get("discount_type") == "amount":
        value_text = f"💵 {d['amount']:,} تومان"
    else:
        value_text = f"💯 {d['percent']}٪"

    plans_raw = d.get("applicable_plans")
    if not plans_raw:
        plans_text = "همه‌ی پلن‌ها"
    else:
        all_plans = db.get_all_plans()
        keys = json.loads(plans_raw)
        plans_text = ", ".join(all_plans.get(k, {}).get("name", k) for k in keys) or "همه‌ی پلن‌ها"

    users_raw = d.get("allowed_user_ids")
    if not users_raw:
        users_text = "همه‌ی کاربران"
    else:
        ids = json.loads(users_raw)
        users_text = "، ".join(f"`{i}`" for i in ids)

    extra = ""
    if d.get("min_order_amount"):
        extra += f"\n💰 حداقل مبلغ سفارش: {d['min_order_amount']:,} تومان"
    if d.get("max_uses_per_user"):
        extra += f"\n🔂 سقف استفاده برای هر کاربر: {d['max_uses_per_user']}"
    if d.get("expires_at"):
        extra += f"\n⏰ انقضا: {d['expires_at']}"

    return (
        f"🎟 کد تخفیف: `{d['code']}`\n"
        f"{value_text}\n"
        f"🔁 تعداد استفاده‌ی باقی‌مانده: {d['uses']}\n"
        f"🎯 پلن‌های مجاز: {plans_text}\n"
        f"👤 کاربران مجاز: {users_text}"
        f"{extra}"
    )


@router.callback_query(F.data.startswith("discdetail_"))
async def admin_discount_detail(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    discount_id = int(callback.data.replace("discdetail_", ""))
    d = db.get_discount_by_id(discount_id)
    if d is None:
        await callback.answer("❌ این کد تخفیف یافت نشد.", show_alert=True)
        return
    await callback.message.edit_text(
        _discount_detail_text(d), parse_mode="Markdown", reply_markup=discount_detail_keyboard(discount_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("discdelete_"))
async def admin_discount_delete_ask(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    discount_id = int(callback.data.replace("discdelete_", ""))
    d = db.get_discount_by_id(discount_id)
    if d is None:
        await callback.answer("❌ این کد تخفیف یافت نشد.", show_alert=True)
        return
    await callback.message.edit_text(
        f"❗️ آیا از حذف کد `{d['code']}` مطمئن هستید؟",
        parse_mode="Markdown",
        reply_markup=discount_delete_confirm_keyboard(discount_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("discdeleteconfirm_"))
async def admin_discount_delete_confirm(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    discount_id = int(callback.data.replace("discdeleteconfirm_", ""))
    db.delete_discount_by_id(discount_id)
    await callback.answer("✅ کد تخفیف حذف شد.", show_alert=True)
    discounts = db.get_all_discounts()
    text = "🎟 کدهای تخفیف فعال:\n\nبرای مشاهده و ویرایش جزئیات هر کد، روی آن بزنید 👇" if discounts else \
        "🎟 هیچ کد تخفیفی هنوز ثبت نشده.\n\nبرای ساخت کد جدید، دکمه‌ی زیر را بزنید 👇"
    await callback.message.edit_text(text, reply_markup=admin_discount_menu(discounts))


# --- ویرایش مقدار تخفیف (درصد/مبلغ) ---
@router.callback_query(F.data.startswith("discedit_value_"))
async def admin_discount_edit_value_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    discount_id = int(callback.data.replace("discedit_value_", ""))
    d = db.get_discount_by_id(discount_id)
    if d is None:
        await callback.answer("❌ این کد تخفیف یافت نشد.", show_alert=True)
        return
    await state.update_data(edit_discount_id=discount_id)
    label = "درصد جدید را وارد کنید (بین ۱ تا ۱۰۰)" if d.get("discount_type") != "amount" else \
        "مبلغ ثابت جدید را به تومان وارد کنید"
    await callback.message.edit_text(f"✏️ {label}:", reply_markup=admin_back_button())
    await state.set_state(AdminStates.waiting_discount_edit_value)
    await callback.answer()


@router.message(AdminStates.waiting_discount_edit_value)
async def admin_discount_edit_value_save(message: types.Message, state: FSMContext):
    data = await state.get_data()
    discount_id = data.get("edit_discount_id")
    d = db.get_discount_by_id(discount_id) if discount_id else None
    if d is None:
        await message.answer("❌ مشکلی پیش آمد.", reply_markup=admin_discount_menu(db.get_all_discounts()))
        await state.clear()
        return

    if not message.text or not clean_numeric_id(message.text).isdigit():
        await message.answer("❌ فقط عدد وارد کنید:")
        return
    value = int(clean_numeric_id(message.text))
    if d.get("discount_type") == "amount":
        if value <= 0:
            await message.answer("❌ مبلغ باید بزرگ‌تر از صفر باشد:")
            return
        db.update_discount(discount_id, amount=value)
    else:
        if not (1 <= value <= 100):
            await message.answer("❌ درصد باید بین ۱ تا ۱۰۰ باشد:")
            return
        db.update_discount(discount_id, percent=value)

    await state.clear()
    d = db.get_discount_by_id(discount_id)
    await message.answer("✅ مقدار تخفیف بروزرسانی شد.", reply_markup=admin_back_button())
    await message.answer(_discount_detail_text(d), parse_mode="Markdown", reply_markup=discount_detail_keyboard(discount_id))


# --- ویرایش تعداد استفاده‌ی باقی‌مانده ---
@router.callback_query(F.data.startswith("discedit_uses_"))
async def admin_discount_edit_uses_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    discount_id = int(callback.data.replace("discedit_uses_", ""))
    if db.get_discount_by_id(discount_id) is None:
        await callback.answer("❌ این کد تخفیف یافت نشد.", show_alert=True)
        return
    await state.update_data(edit_discount_id=discount_id)
    await callback.message.edit_text(
        "✏️ تعداد دفعات مجاز باقی‌مانده‌ی این کد را وارد کنید (مثلاً 50):",
        reply_markup=admin_back_button(),
    )
    await state.set_state(AdminStates.waiting_discount_edit_uses)
    await callback.answer()


@router.message(AdminStates.waiting_discount_edit_uses)
async def admin_discount_edit_uses_save(message: types.Message, state: FSMContext):
    data = await state.get_data()
    discount_id = data.get("edit_discount_id")
    if not message.text or not clean_numeric_id(message.text).isdigit() or int(clean_numeric_id(message.text)) < 0:
        await message.answer("❌ لطفاً یک عدد صحیح غیرمنفی وارد کنید:")
        return
    if db.get_discount_by_id(discount_id) is None:
        await message.answer("❌ مشکلی پیش آمد.", reply_markup=admin_discount_menu(db.get_all_discounts()))
        await state.clear()
        return

    db.update_discount(discount_id, uses=int(clean_numeric_id(message.text)))
    await state.clear()
    d = db.get_discount_by_id(discount_id)
    await message.answer("✅ تعداد استفاده بروزرسانی شد.", reply_markup=admin_back_button())
    await message.answer(_discount_detail_text(d), parse_mode="Markdown", reply_markup=discount_detail_keyboard(discount_id))


# --- ویرایش حداقل مبلغ سفارش ---
@router.callback_query(F.data.startswith("discedit_minorder_"))
async def admin_discount_edit_minorder_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    discount_id = int(callback.data.replace("discedit_minorder_", ""))
    if db.get_discount_by_id(discount_id) is None:
        await callback.answer("❌ این کد تخفیف یافت نشد.", show_alert=True)
        return
    await state.update_data(edit_discount_id=discount_id)
    await callback.message.edit_text(
        "✏️ حداقل مبلغ سفارش (به تومان) برای استفاده از این کد را وارد کنید.\n"
        "برای برداشتن محدودیت، عدد 0 را ارسال کنید.",
        reply_markup=admin_back_button(),
    )
    await state.set_state(AdminStates.waiting_discount_edit_min_order)
    await callback.answer()


@router.message(AdminStates.waiting_discount_edit_min_order)
async def admin_discount_edit_minorder_save(message: types.Message, state: FSMContext):
    data = await state.get_data()
    discount_id = data.get("edit_discount_id")
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await message.answer("❌ فقط عدد وارد کنید:")
        return
    if db.get_discount_by_id(discount_id) is None:
        await message.answer("❌ مشکلی پیش آمد.", reply_markup=admin_discount_menu(db.get_all_discounts()))
        await state.clear()
        return

    db.update_discount(discount_id, min_order_amount=int(clean_numeric_id(message.text)))
    await state.clear()
    d = db.get_discount_by_id(discount_id)
    await message.answer("✅ حداقل مبلغ سفارش بروزرسانی شد.", reply_markup=admin_back_button())
    await message.answer(_discount_detail_text(d), parse_mode="Markdown", reply_markup=discount_detail_keyboard(discount_id))


# --- ویرایش سقف استفاده‌ی هر کاربر ---
@router.callback_query(F.data.startswith("discedit_maxuser_"))
async def admin_discount_edit_maxuser_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    discount_id = int(callback.data.replace("discedit_maxuser_", ""))
    if db.get_discount_by_id(discount_id) is None:
        await callback.answer("❌ این کد تخفیف یافت نشد.", show_alert=True)
        return
    await state.update_data(edit_discount_id=discount_id)
    await callback.message.edit_text(
        "✏️ سقف تعداد دفعات استفاده‌ی هر کاربر از این کد را وارد کنید.\n"
        "برای بی‌محدودیت‌کردن، عدد 0 را ارسال کنید.",
        reply_markup=admin_back_button(),
    )
    await state.set_state(AdminStates.waiting_discount_edit_max_per_user)
    await callback.answer()


@router.message(AdminStates.waiting_discount_edit_max_per_user)
async def admin_discount_edit_maxuser_save(message: types.Message, state: FSMContext):
    data = await state.get_data()
    discount_id = data.get("edit_discount_id")
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await message.answer("❌ فقط عدد وارد کنید:")
        return
    if db.get_discount_by_id(discount_id) is None:
        await message.answer("❌ مشکلی پیش آمد.", reply_markup=admin_discount_menu(db.get_all_discounts()))
        await state.clear()
        return

    db.update_discount(discount_id, max_uses_per_user=int(clean_numeric_id(message.text)))
    await state.clear()
    d = db.get_discount_by_id(discount_id)
    await message.answer("✅ سقف استفاده‌ی هر کاربر بروزرسانی شد.", reply_markup=admin_back_button())
    await message.answer(_discount_detail_text(d), parse_mode="Markdown", reply_markup=discount_detail_keyboard(discount_id))


# --- ویرایش تاریخ انقضا ---
@router.callback_query(F.data.startswith("discedit_expiry_"))
async def admin_discount_edit_expiry_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    discount_id = int(callback.data.replace("discedit_expiry_", ""))
    if db.get_discount_by_id(discount_id) is None:
        await callback.answer("❌ این کد تخفیف یافت نشد.", show_alert=True)
        return
    await state.update_data(edit_discount_id=discount_id)
    await callback.message.edit_text(
        "✏️ تاریخ انقضا را به‌فرمت `YYYY-MM-DD` (مثلاً 2026-12-31) وارد کنید.\n"
        "برای برداشتن انقضا (کد همیشه معتبر باشد)، عدد 0 را ارسال کنید.",
        parse_mode="Markdown",
        reply_markup=admin_back_button(),
    )
    await state.set_state(AdminStates.waiting_discount_edit_expiry)
    await callback.answer()


@router.message(AdminStates.waiting_discount_edit_expiry)
async def admin_discount_edit_expiry_save(message: types.Message, state: FSMContext):
    data = await state.get_data()
    discount_id = data.get("edit_discount_id")
    if db.get_discount_by_id(discount_id) is None:
        await message.answer("❌ مشکلی پیش آمد.", reply_markup=admin_discount_menu(db.get_all_discounts()))
        await state.clear()
        return

    raw = (message.text or "").strip()
    if raw == "0":
        db.update_discount(discount_id, expires_at=None)
    else:
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            await message.answer("❌ فرمت نامعتبر است؛ به‌صورت YYYY-MM-DD وارد کنید (یا 0 برای حذف انقضا):")
            return
        db.update_discount(discount_id, expires_at=parsed.strftime("%Y-%m-%d 23:59:59"))

    await state.clear()
    d = db.get_discount_by_id(discount_id)
    await message.answer("✅ تاریخ انقضا بروزرسانی شد.", reply_markup=admin_back_button())
    await message.answer(_discount_detail_text(d), parse_mode="Markdown", reply_markup=discount_detail_keyboard(discount_id))


# --- ویرایش کاربران مجاز ---
@router.callback_query(F.data.startswith("discedit_users_"))
async def admin_discount_edit_users_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    discount_id = int(callback.data.replace("discedit_users_", ""))
    if db.get_discount_by_id(discount_id) is None:
        await callback.answer("❌ این کد تخفیف یافت نشد.", show_alert=True)
        return
    await state.update_data(edit_discount_id=discount_id)
    await callback.message.edit_text(
        "👤 آیدی‌های عددی تلگرام مجاز به استفاده از این کد را وارد کنید "
        "(هرکدام با کاما، فاصله یا خط جدید جدا شود).\n\n"
        "برای برداشتن محدودیت (باز کردن کد برای همه‌ی کاربران) عدد 0 را ارسال کنید.",
        reply_markup=admin_back_button(),
    )
    await state.set_state(AdminStates.waiting_discount_edit_users)
    await callback.answer()


@router.message(AdminStates.waiting_discount_edit_users)
async def admin_discount_edit_users_save(message: types.Message, state: FSMContext):
    data = await state.get_data()
    discount_id = data.get("edit_discount_id")
    if db.get_discount_by_id(discount_id) is None:
        await message.answer("❌ مشکلی پیش آمد.", reply_markup=admin_discount_menu(db.get_all_discounts()))
        await state.clear()
        return

    raw = (message.text or "").strip()
    ids = [p for p in re.split(r"[\s,،]+", raw) if p]

    if not ids or ids == ["0"]:
        db.update_discount(discount_id, allowed_user_ids=None)
        summary = "بدون محدودیت (همه‌ی کاربران)"
    else:
        if not all(p.lstrip("-").isdigit() for p in ids):
            await message.answer("❌ فقط آیدی‌های عددی معتبر وارد کنید (یا 0 برای باز کردن برای همه):")
            return
        ids = sorted(set(ids))
        db.update_discount(discount_id, allowed_user_ids=ids)
        summary = "، ".join(f"`{i}`" for i in ids)

    await state.clear()
    d = db.get_discount_by_id(discount_id)
    await message.answer(f"✅ کاربران مجاز بروزرسانی شد: {summary}", parse_mode="Markdown", reply_markup=admin_back_button())
    await message.answer(_discount_detail_text(d), parse_mode="Markdown", reply_markup=discount_detail_keyboard(discount_id))


# --- ویرایش پلن‌های مجاز ---
@router.callback_query(F.data.startswith("discedit_plans_"))
async def admin_discount_edit_plans_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    discount_id = int(callback.data.replace("discedit_plans_", ""))
    d = db.get_discount_by_id(discount_id)
    if d is None:
        await callback.answer("❌ این کد تخفیف یافت نشد.", show_alert=True)
        return
    selected = db._discount_plans(d) or []
    await state.update_data(edit_discount_id=discount_id, edit_discount_plans=selected)
    await callback.message.edit_text(
        "🎯 پلن‌های مجاز برای این کد را انتخاب کنید (هرکدام را بزنید تا انتخاب/لغو شود):",
        reply_markup=discount_plans_edit_keyboard(discount_id, selected),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("discplaned_"))
async def admin_discount_edit_plans_toggle(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    # discplaned_<id>_<key|all|done>
    rest = callback.data.replace("discplaned_", "")
    discount_id_str, _, key = rest.partition("_")
    discount_id = int(discount_id_str)
    data = await state.get_data()
    selected = data.get("edit_discount_plans", [])

    if key == "all":
        selected = []
    elif key == "done":
        db.update_discount(discount_id, applicable_plans=selected or None)
        await state.clear()
        d = db.get_discount_by_id(discount_id)
        await callback.message.edit_text(
            _discount_detail_text(d), parse_mode="Markdown", reply_markup=discount_detail_keyboard(discount_id)
        )
        await callback.answer("✅ پلن‌های مجاز ذخیره شد.")
        return
    else:
        selected = [p for p in selected if p != key] if key in selected else selected + [key]

    await state.update_data(edit_discount_plans=selected)
    await callback.message.edit_reply_markup(reply_markup=discount_plans_edit_keyboard(discount_id, selected))
    await callback.answer()


@router.callback_query(F.data == "new_discount")
async def new_discount_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "🎟 ساخت کد تخفیف جدید — مرحله ۱ از ۶\n\n"
        "✏️ کد تخفیف مورد نظر را بدون فاصله وارد کنید (مثلاً SUMMER20):",
        reply_markup=admin_back_button(),
    )
    await state.set_state(AdminStates.waiting_discount_code_step)
    await callback.answer()


@router.message(AdminStates.waiting_discount_code_step)
async def new_discount_code_input(message: types.Message, state: FSMContext):
    code = message.text.strip().upper() if message.text else ""
    if not code or " " in code:
        await message.answer("❌ کد نامعتبر است؛ بدون فاصله دوباره وارد کنید:")
        return
    await state.update_data(new_discount_code=code)
    await message.answer(
        "🎟 مرحله ۲ از ۶\n\nنوع تخفیف را انتخاب کنید:", reply_markup=discount_type_keyboard()
    )


@router.callback_query(F.data.startswith("disctype_"), AdminStates.waiting_discount_code_step)
async def new_discount_type_chosen(callback: types.CallbackQuery, state: FSMContext):
    disc_type = callback.data.replace("disctype_", "")
    await state.update_data(new_discount_type=disc_type, new_discount_plans=[])
    label = "درصد تخفیف را وارد کنید (عددی بین ۱ تا ۱۰۰، مثلاً 20)" if disc_type == "percent" else \
        "مبلغ ثابت تخفیف را به تومان وارد کنید (مثلاً 20000)"
    await callback.message.edit_text(f"🎟 مرحله ۳ از ۶\n\n💯 {label}:")
    await state.set_state(AdminStates.waiting_discount_value_step)
    await callback.answer()


@router.message(AdminStates.waiting_discount_value_step)
async def new_discount_value_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    disc_type = data.get("new_discount_type", "percent")
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await message.answer("❌ فقط عدد وارد کنید:")
        return
    value = int(clean_numeric_id(message.text))
    if disc_type == "percent" and not (1 <= value <= 100):
        await message.answer("❌ درصد باید بین ۱ تا ۱۰۰ باشد:")
        return
    if disc_type == "amount" and value <= 0:
        await message.answer("❌ مبلغ باید بزرگ‌تر از صفر باشد:")
        return

    await state.update_data(new_discount_value=value)
    await message.answer(
        "🎟 مرحله ۴ از ۶\n\n"
        "🎯 این کد روی کدام پلن‌ها اعمال شود؟ (هرکدام را بزنید تا انتخاب/لغو شود؛ "
        "اگر «همه‌ی پلن‌ها» را بزنید، هیچ محدودیتی نخواهد داشت):",
        reply_markup=discount_plans_select_keyboard([]),
    )


@router.callback_query(F.data.startswith("discplan_"), AdminStates.waiting_discount_value_step)
async def new_discount_plan_toggle(callback: types.CallbackQuery, state: FSMContext):
    key = callback.data.replace("discplan_", "")
    data = await state.get_data()
    selected = data.get("new_discount_plans", [])

    if key == "all":
        selected = []
    elif key == "done":
        await state.update_data(new_discount_plans=selected)
        await callback.message.edit_text(
            "🎟 مرحله ۵ از ۶\n\n"
            "👤 این کد فقط برای چه کاربرانی مجاز باشد؟\n\n"
            "آیدی‌های عددی تلگرام را وارد کنید (هرکدام با کاما، فاصله یا خط جدید جدا شود).\n"
            "اگر می‌خواهید همه‌ی کاربران بتوانند از این کد استفاده کنند، عدد 0 را ارسال کنید."
        )
        await state.set_state(AdminStates.waiting_discount_users_step)
        await callback.answer()
        return
    else:
        selected = [p for p in selected if p != key] if key in selected else selected + [key]

    await state.update_data(new_discount_plans=selected)
    await callback.message.edit_reply_markup(reply_markup=discount_plans_select_keyboard(selected))
    await callback.answer()


@router.message(AdminStates.waiting_discount_users_step)
async def new_discount_users_input(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip()
    ids = [p for p in re.split(r"[\s,،]+", raw) if p]

    if not ids or ids == ["0"]:
        await state.update_data(new_discount_users=None)
    elif all(p.lstrip("-").isdigit() for p in ids):
        await state.update_data(new_discount_users=sorted(set(ids)))
    else:
        await message.answer("❌ فقط آیدی‌های عددی معتبر وارد کنید (یا 0 برای باز کردن برای همه):")
        return

    await message.answer(
        "🎟 مرحله ۶ از ۶\n\n🔁 تعداد دفعات مجاز استفاده از این کد را وارد کنید (مثلاً 50):"
    )
    await state.set_state(AdminStates.waiting_discount_uses_step)


@router.message(AdminStates.waiting_discount_uses_step)
async def new_discount_uses_input(message: types.Message, state: FSMContext):
    if not message.text or not clean_numeric_id(message.text).isdigit() or int(clean_numeric_id(message.text)) <= 0:
        await message.answer("❌ لطفاً یک عدد صحیح مثبت وارد کنید:")
        return

    data = await state.get_data()
    code = data.get("new_discount_code")
    disc_type = data.get("new_discount_type", "percent")
    value = data.get("new_discount_value", 0)
    plans = data.get("new_discount_plans") or None
    allowed_users = data.get("new_discount_users") or None
    uses = int(clean_numeric_id(message.text))

    try:
        db.create_discount(
            code,
            percent=value if disc_type == "percent" else 0,
            uses=uses,
            discount_type=disc_type,
            amount=value if disc_type == "amount" else 0,
            applicable_plans=plans,
            allowed_user_ids=allowed_users,
        )
        value_text = f"{value}٪" if disc_type == "percent" else f"{value:,} تومان"
        all_plans = db.get_all_plans()
        plans_text = "همه‌ی پلن‌ها" if not plans else ", ".join(all_plans.get(p, {}).get("name", p) for p in plans)
        users_text = "همه‌ی کاربران" if not allowed_users else "، ".join(f"`{i}`" for i in allowed_users)
        await message.answer(
            f"✅ کد تخفیف جدید با موفقیت ساخته شد! 🎉\n\n"
            f"🎟 کد: `{code}`\n💯 مقدار: {value_text}\n🎯 پلن‌ها: {plans_text}\n"
            f"👤 کاربران مجاز: {users_text}\n🔁 تعداد استفاده: {uses}",
            parse_mode="Markdown",
            reply_markup=admin_discount_menu(db.get_all_discounts()),
        )
    except Exception:
        await message.answer("❌ این کد قبلاً ثبت شده.", reply_markup=admin_discount_menu(db.get_all_discounts()))
    await state.clear()


# ---------------------------------------------------------------------------
# 🤝 مدیریت دعوت‌ها
# ---------------------------------------------------------------------------
REFERRERS_PER_PAGE = 10


def _referrers_page_text(page: int, total: int) -> str:
    if total == 0:
        return "🤝 هنوز هیچ دعوتی ثبت نشده."
    return (
        f"🤝 مدیریت دعوت‌شده‌ها — مرتب‌شده بر اساس بیشترین دعوت\n\n"
        f"👥 تعداد کل دعوت‌کننده‌ها: {total}\n\n"
        f"روی هر کدام بزنید تا لیست دعوت‌شده‌هایش و وضعیت کیف پولش رو ببینید 👇"
    )


@router.callback_query(F.data == "admin_referrals")
async def admin_referrals(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await _render_referrers_page(callback, 0)
    await callback.answer()


async def _render_referrers_page(callback: types.CallbackQuery, page: int):
    total = db.count_referrers()
    users = db.get_referrers_page(page, REFERRERS_PER_PAGE)
    has_next = total > (page + 1) * REFERRERS_PER_PAGE
    await callback.message.edit_text(
        _referrers_page_text(page, total),
        reply_markup=admin_referrers_page_keyboard(users, page, has_next),
    )


@router.callback_query(F.data.startswith("refpage_"))
async def admin_referrers_page_nav(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    page = int(callback.data.replace("refpage_", ""))
    await _render_referrers_page(callback, page)
    await callback.answer()


@router.callback_query(F.data.startswith("refdetail_"))
async def admin_referrer_detail(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    _, uid, page_str = callback.data.split("_")
    page = int(page_str)

    referrer = db.get_user(uid)
    if referrer is None:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return

    invited = db.get_referred_users(referrer["id"])
    text = (
        f"🤝 دعوت‌شده‌های {referrer['name']} (🆔 {referrer['telegram_id']})\n\n"
        f"💛 کیف پول آزاد دعوت‌کننده: {referrer['wallet']:,} تومان\n"
        f"🔒 کیف پول مسدود دعوت‌کننده (در انتظار): {referrer['locked_wallet']:,} تومان\n"
        f"👥 تعداد دعوت: {referrer['invited_count']} | ✅ موفق: {referrer['successful_invites']}\n\n"
        f"📋 لیست افراد دعوت‌شده:\n"
    )
    if not invited:
        text += "— هنوز هیچ کاربری ثبت نشده."
    else:
        for i, u in enumerate(invited, 1):
            reward = u.get("referral_reward") or 0
            status = u.get("referral_status") or "-"
            text += (
                f"{i}. {u['name']} | 🆔 {u['telegram_id']} | "
                f"🎁 پاداش: {reward:,} تومان | وضعیت: {status}\n"
            )

    await callback.message.edit_text(text, reply_markup=admin_referred_detail_keyboard(uid, page))
    await callback.answer()


# ---------------------------------------------------------------------------
# 🤝 نمایندگی — با ثبت آیدی عددی یک فرد، همه‌ی خریدهای VIP بعدی او به‌صورت
# خودکار با درصد تعیین‌شده تخفیف می‌خورد (بدون نیاز به وارد کردن کد تخفیف).
# ---------------------------------------------------------------------------
def _user_detail_text(user: dict) -> str:
    """همان متن استاندارد صفحه‌ی «مدیریت کاربر» (بخش کاربران)؛ برای اینکه صفحه‌ی
    نماینده هم دقیقاً همین محیط را نشان دهد، این تابع مشترک استفاده می‌شود."""
    stats = db.get_referral_stats(user["id"])
    return (
        f"👤 {user['name']}\n"
        f"🆔 {user['telegram_id']}\n\n"
        f"👛 کیف پول آزاد: {user['wallet']:,} تومان\n"
        f"🔒 کیف پول مسدود: {user['locked_wallet']:,} تومان\n"
        f"🛒 کل خرید: {user['total_purchase']:,} تومان\n"
        f"📅 عضویت: {user['joined']}\n\n"
        f"🔗 کد دعوت: {user['invite_code']}\n"
        f"👥 دعوت: {stats['invited_count']} | موفق: {stats['successful_invites']}"
    )


@router.callback_query(F.data == "admin_agency")
async def admin_agency_list(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    agents = db.get_all_agents()
    text = (
        "🤝 هنوز هیچ نماینده‌ای ثبت نشده.\n\nبرای افزودن، دکمه‌ی زیر را بزنید 👇"
        if not agents else
        "🤝 نمایندگان فعلی (تخفیف خودکار روی VIP)\n\nروی هرکدام بزنید تا مثل بخش «کاربران» مدیریتش کنید 👇"
    )

    await callback.message.edit_text(text, reply_markup=admin_agency_menu(agents))
    await callback.answer()


@router.callback_query(F.data == "new_agent")
async def new_agent_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await callback.message.edit_text(
        "🤝 افزودن نماینده — مرحله ۱ از ۲\n\n"
        "🆔 آیدی عددی (Telegram ID) فرد را ارسال کنید:",
        reply_markup=admin_back_button(),
    )
    await state.set_state(AdminStates.waiting_agent_id_step)
    await callback.answer()


@router.message(AdminStates.waiting_agent_id_step)
async def new_agent_id_input(message: types.Message, state: FSMContext):
    tid = (message.text or "").strip()
    if not tid.isdigit():
        await message.answer("❌ آیدی عددی نامعتبر است؛ فقط عدد ارسال کنید:")
        return
    await state.update_data(new_agent_id=tid)
    await message.answer(
        f"🧾 مرحله ۲ از ۲\n\n💯 درصد تخفیف VIP برای این نماینده را وارد کنید "
        f"(پیش‌فرض پیشنهادی: {AGENCY_VIP_DISCOUNT_PERCENT}):"
    )
    await state.set_state(AdminStates.waiting_agent_percent_step)


@router.message(AdminStates.waiting_agent_percent_step)
async def new_agent_percent_input(message: types.Message, state: FSMContext):
    if not message.text or not clean_numeric_id(message.text).isdigit() or not (1 <= int(clean_numeric_id(message.text)) <= 100):
        await message.answer("❌ لطفاً یک عدد بین ۱ تا ۱۰۰ وارد کنید:")
        return
    data = await state.get_data()
    tid = data.get("new_agent_id")
    percent = int(clean_numeric_id(message.text))
    db.add_agent(tid, percent)
    await message.answer(
        f"✅ نماینده ثبت شد!\n\n🆔 {tid}\n💯 تخفیف VIP: {percent}٪\n\n"
        f"از این به بعد، خریدهای VIP این آیدی به‌صورت خودکار {percent}٪ تخفیف می‌خورد.",
        reply_markup=admin_agency_menu(db.get_all_agents()),
    )
    await state.clear()


@router.callback_query(F.data.startswith("deleteagent_"))
async def delete_agent(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    tid = callback.data.replace("deleteagent_", "")
    db.remove_agent(tid)
    await callback.answer("✅ نماینده حذف شد.")
    await admin_agency_list(callback)


# --- 👤 باز کردن صفحه‌ی یک نماینده — دقیقاً همان صفحه‌ی «مدیریت کاربر»
# (بخش کاربران)، به‌علاوه‌ی دکمه‌ی «💯 تغییر درصد تخفیف نمایندگی» ---
@router.callback_query(F.data.startswith("agentopen_"))
async def admin_agent_open(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    tid = callback.data.replace("agentopen_", "")
    agent = db.get_agent(tid)
    if agent is None:
        await callback.answer("❌ این نماینده یافت نشد.", show_alert=True)
        return

    user = db.get_user(tid)
    if user is None:
        await callback.message.edit_text(
            f"🤝 نماینده 🆔 {tid} | 💯 {agent['vip_discount_percent']}٪\n\n"
            "⚠️ این آیدی هنوز ربات را /start نزده؛ اطلاعات کاربری‌ای برایش ثبت نشده.",
            reply_markup=admin_agent_actions_keyboard(tid),
        )
        await callback.answer()
        return

    text = _user_detail_text(user) + f"\n\n🤝 درصد تخفیف نمایندگی (VIP): {agent['vip_discount_percent']}٪"
    await callback.message.edit_text(text, reply_markup=admin_agent_actions_keyboard(tid))
    await callback.answer()


@router.callback_query(F.data.startswith("editagentpercent_"))
async def admin_agent_edit_percent_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    tid = callback.data.replace("editagentpercent_", "")
    if db.get_agent(tid) is None:
        await callback.answer("❌ این نماینده یافت نشد.", show_alert=True)
        return
    await state.update_data(edit_agent_id=tid)
    await state.set_state(AdminStates.waiting_agent_edit_percent)
    await callback.message.edit_text("💯 درصد تخفیف جدید (بین ۱ تا ۱۰۰) را ارسال کنید:")
    await callback.answer()


@router.message(AdminStates.waiting_agent_edit_percent)
async def admin_agent_edit_percent_apply(message: types.Message, state: FSMContext):
    if not message.text or not clean_numeric_id(message.text).isdigit() or not (1 <= int(clean_numeric_id(message.text)) <= 100):
        await message.answer("❌ لطفاً یک عدد بین ۱ تا ۱۰۰ وارد کنید:")
        return
    data = await state.get_data()
    tid = data.get("edit_agent_id")
    agent = db.get_agent(tid) if tid else None
    if agent is None:
        await message.answer("❌ مشکلی پیش آمد؛ از ابتدا امتحان کنید.", reply_markup=admin_agency_menu(db.get_all_agents()))
        await state.clear()
        return

    percent = int(clean_numeric_id(message.text))
    db.add_agent(tid, percent, agent.get("note"))
    await message.answer(
        f"✅ درصد تخفیف نماینده به‌روزرسانی شد:\n🆔 {tid}\n💯 {percent}٪",
        reply_markup=admin_agent_actions_keyboard(tid),
    )
    await state.clear()


# ---------------------------------------------------------------------------
# 🗂 دسته‌بندی‌های VIP — می‌توان هر تعداد دسته و داخل هرکدام هر تعداد پلن اضافه
# کرد؛ همه‌شان خودکار در «🛒 خرید اشتراک → 🚀 سرور VIP» برای کاربر ظاهر می‌شوند.
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "admin_vip_categories")
async def admin_vip_categories_list(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await callback.message.edit_text(
        "🗂 دسته‌بندی‌های VIP\n\n"
        "این دسته‌ها همان چیزی هستند که کاربر موقع «خرید اشتراک → سرور VIP» می‌بیند.\n"
        "برای مدیریت پلن‌های داخل هر دسته، روی آن بزنید 👇",
        reply_markup=admin_vip_categories_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "newvipcat")
async def admin_new_vip_category_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_vip_category_name)
    await callback.message.edit_text("🗂 نام دسته‌بندی جدید را ارسال کنید (مثلاً «💎 حجم بالای ویژه»):")
    await callback.answer()


@router.message(AdminStates.waiting_vip_category_name)
async def admin_new_vip_category_apply(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer("❌ نام نمی‌تواند خالی باشد؛ دوباره ارسال کنید:")
        return
    cat = db.create_vip_category(name)
    await message.answer(
        f"✅ دسته‌بندی «{name}» ساخته شد!\n\nحالا می‌توانید از داخل همین دسته، پلن اضافه کنید 👇",
        reply_markup=admin_vip_category_detail_keyboard(cat["key"]),
    )
    await state.clear()


@router.callback_query(F.data.startswith("admincat_"))
async def admin_vip_category_detail(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    category_key = callback.data.replace("admincat_", "")
    cat = db.get_vip_category(category_key)
    if cat is None:
        await callback.answer("❌ این دسته یافت نشد.", show_alert=True)
        return
    plans = db.get_vip_plans(cat["id"])
    text = f"🗂 {cat['name']}\n\n📦 تعداد پلن: {len(plans)}\n\nبرای مدیریت هر پلن روی آن بزنید 👇"
    await callback.message.edit_text(text, reply_markup=admin_vip_category_detail_keyboard(category_key))
    await callback.answer()


@router.callback_query(F.data.startswith("delvipcat_"))
async def admin_delete_vip_category(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    category_key = callback.data.replace("delvipcat_", "")
    cat = db.get_vip_category(category_key)
    if cat is None:
        await callback.answer("❌ این دسته یافت نشد.", show_alert=True)
        return
    ok = db.delete_vip_category(cat["id"])
    if not ok:
        await callback.answer("❌ این دسته پلن دارد؛ اول همه‌ی پلن‌هایش را حذف کنید.", show_alert=True)
        return
    await callback.answer("✅ دسته حذف شد.")
    await admin_vip_categories_list(callback)


# --- افزودن پلن جدید به یک دسته (۴ مرحله: نام / قیمت / حجم گیگ / مدت روز) ---
@router.callback_query(F.data.startswith("newvipplan_"))
async def admin_new_vip_plan_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    category_key = callback.data.replace("newvipplan_", "")
    if db.get_vip_category(category_key) is None:
        await callback.answer("❌ این دسته یافت نشد.", show_alert=True)
        return
    await state.update_data(new_vip_plan_category=category_key)
    await state.set_state(AdminStates.waiting_vip_plan_name)
    await callback.message.edit_text(
        "📦 افزودن پلن جدید — مرحله ۱ از ۵\n\n✏️ نام پلن را ارسال کنید (مثلاً «۲۰۰ گیگ | کاربر و زمان ∞»):"
    )
    await callback.answer()


@router.message(AdminStates.waiting_vip_plan_name)
async def admin_new_vip_plan_name(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer("❌ نام نمی‌تواند خالی باشد؛ دوباره ارسال کنید:")
        return
    await state.update_data(new_vip_plan_name=name)
    await state.set_state(AdminStates.waiting_vip_plan_price)
    await message.answer("📦 مرحله ۲ از ۵\n\n💰 قیمت را به تومان (فقط عدد) ارسال کنید:")


@router.message(AdminStates.waiting_vip_plan_price)
async def admin_new_vip_plan_price(message: types.Message, state: FSMContext):
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await message.answer("❌ فقط عدد ارسال کنید:")
        return
    await state.update_data(new_vip_plan_price=int(clean_numeric_id(message.text)))
    await state.set_state(AdminStates.waiting_vip_plan_gb)
    await message.answer(
        "📦 مرحله ۳ از ۵\n\n🗜 حجم را به گیگابایت ارسال کنید (اگر نامحدود است، عدد 0 را بفرستید):"
    )


@router.message(AdminStates.waiting_vip_plan_gb)
async def admin_new_vip_plan_gb(message: types.Message, state: FSMContext):
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await message.answer("❌ فقط عدد ارسال کنید (برای نامحدود، 0):")
        return
    await state.update_data(new_vip_plan_gb=int(clean_numeric_id(message.text)))
    await state.set_state(AdminStates.waiting_vip_plan_days)
    await message.answer("📦 مرحله ۴ از ۵\n\n⏳ مدت را به روز ارسال کنید (اگر نامحدود است، عدد 0 را بفرستید):")


@router.message(AdminStates.waiting_vip_plan_days)
async def admin_new_vip_plan_days(message: types.Message, state: FSMContext):
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await message.answer("❌ فقط عدد ارسال کنید (برای نامحدود، 0):")
        return
    await state.update_data(new_vip_plan_days=int(clean_numeric_id(message.text)))
    await state.set_state(AdminStates.waiting_vip_plan_userlimit)
    await message.answer(
        "📦 مرحله ۵ از ۵\n\n"
        "👥 سقف کاربر همزمان (HWID Limit) را به عدد ارسال کنید (۰ تا ۱۰، ۰ = نامحدود):"
    )


@router.message(AdminStates.waiting_vip_plan_userlimit)
async def admin_new_vip_plan_userlimit(message: types.Message, state: FSMContext):
    user_limit = parse_int_in_range((message.text or "").strip(), 0, 10)
    if user_limit is None:
        await message.answer("❌ عددی بین ۰ تا ۱۰ ارسال کنید (۰ = نامحدود):")
        return
    data = await state.get_data()
    category_key = data.get("new_vip_plan_category")
    cat = db.get_vip_category(category_key)
    if cat is None:
        await message.answer("❌ مشکلی پیش آمد؛ از ابتدا امتحان کنید.")
        await state.clear()
        return

    days = data.get("new_vip_plan_days", 0)
    name = data.get("new_vip_plan_name")
    price = data.get("new_vip_plan_price")
    volume_gb = data.get("new_vip_plan_gb", 0)

    plan_key = db.add_vip_plan(cat["id"], name, price, days=days, volume_gb=volume_gb, user_limit=user_limit)
    await message.answer(
        f"✅ پلن جدید اضافه شد! 🎉\n\n📦 {name}\n💰 {price:,} تومان\n🗜 "
        f"{volume_gb if volume_gb else 'نامحدود'} گیگ\n⏳ {days if days else 'نامحدود'} روز\n👥 {'نامحدود' if user_limit == 0 else f'{user_limit} کاربر'} همزمان",
        reply_markup=admin_vip_category_detail_keyboard(category_key),
    )
    await state.clear()


# --- مشاهده/ویرایش/حذف یک پلن مشخص ---
@router.callback_query(F.data.startswith("vipplan_"))
async def admin_vip_plan_detail(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    plan_key = callback.data.replace("vipplan_", "")
    plan = db.get_vip_plan(plan_key)
    if plan is None:
        await callback.answer("❌ این پلن یافت نشد.", show_alert=True)
        return
    cat = db.get_vip_category(plan["category_id"])
    text = (
        f"📦 {plan['name']}\n\n"
        f"💰 قیمت: {plan['price']:,} تومان\n"
        f"🗜 حجم: {plan['volume_gb'] if plan['volume_gb'] else 'نامحدود'} گیگ\n"
        f"⏳ مدت: {plan['days'] if plan['days'] else 'نامحدود'} روز\n"
        f"👥 سقف کاربر: {'نامحدود' if plan.get('user_limit', 0) == 0 else str(plan['user_limit']) + ' کاربر'}\n"
        f"🗂 دسته: {cat['name'] if cat else '-'}"
    )
    await callback.message.edit_text(
        text, reply_markup=admin_vip_plan_detail_keyboard(plan_key, cat["key"] if cat else "")
    )
    await callback.answer()


def _vip_plan_edit_starter(field_state, prompt: str, prefix: str):
    async def handler(callback: types.CallbackQuery, state: FSMContext):
        if not _is_admin(callback.from_user.id):
            await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
            return
        plan_key = callback.data.replace(prefix, "")
        if db.get_vip_plan(plan_key) is None:
            await callback.answer("❌ این پلن یافت نشد.", show_alert=True)
            return
        await state.update_data(edit_vip_plan_key=plan_key)
        await state.set_state(field_state)
        await callback.message.edit_text(prompt)
        await callback.answer()
    return handler


router.callback_query(F.data.startswith("vipplanname_"))(
    _vip_plan_edit_starter(AdminStates.waiting_vip_plan_edit_name, "✏️ نام جدید پلن را ارسال کنید:", "vipplanname_")
)
router.callback_query(F.data.startswith("vipplanprice_"))(
    _vip_plan_edit_starter(AdminStates.waiting_vip_plan_edit_price, "💰 قیمت جدید را به تومان (فقط عدد) ارسال کنید:", "vipplanprice_")
)
router.callback_query(F.data.startswith("vipplangb_"))(
    _vip_plan_edit_starter(AdminStates.waiting_vip_plan_edit_gb, "🗜 حجم جدید را به گیگ (فقط عدد، 0 = نامحدود) ارسال کنید:", "vipplangb_")
)
router.callback_query(F.data.startswith("vipplandays_"))(
    _vip_plan_edit_starter(AdminStates.waiting_vip_plan_edit_days, "⏳ مدت جدید را به روز (فقط عدد، 0 = نامحدود) ارسال کنید:", "vipplandays_")
)
router.callback_query(F.data.startswith("vipplanuserlimit_"))(
    _vip_plan_edit_starter(
        AdminStates.waiting_vip_plan_edit_userlimit,
        "👥 سقف کاربر همزمان جدید را ارسال کنید (۰ تا ۱۰، ۰ = نامحدود):",
        "vipplanuserlimit_"
    )
)


async def _after_vip_plan_edit(message: types.Message, state: FSMContext, plan_key: str, success_text: str):
    plan = db.get_vip_plan(plan_key)
    cat = db.get_vip_category(plan["category_id"]) if plan else None
    await message.answer(
        success_text,
        reply_markup=admin_vip_plan_detail_keyboard(plan_key, cat["key"] if cat else ""),
    )
    await state.clear()


@router.message(AdminStates.waiting_vip_plan_edit_name)
async def admin_vip_plan_edit_name_apply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    plan_key = data.get("edit_vip_plan_key")
    new_name = (message.text or "").strip()
    if not plan_key or not new_name:
        await message.answer("❌ متن نامعتبر است؛ دوباره ارسال کنید:")
        return
    db.update_vip_plan(plan_key, name=new_name)
    await _after_vip_plan_edit(message, state, plan_key, f"✅ نام پلن به‌روزرسانی شد:\n📦 {new_name}")


@router.message(AdminStates.waiting_vip_plan_edit_price)
async def admin_vip_plan_edit_price_apply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    plan_key = data.get("edit_vip_plan_key")
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await message.answer("❌ فقط عدد ارسال کنید:")
        return
    price = int(clean_numeric_id(message.text))
    db.update_vip_plan(plan_key, price=price)
    await _after_vip_plan_edit(message, state, plan_key, f"✅ قیمت پلن به‌روزرسانی شد:\n💰 {price:,} تومان")


@router.message(AdminStates.waiting_vip_plan_edit_gb)
async def admin_vip_plan_edit_gb_apply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    plan_key = data.get("edit_vip_plan_key")
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await message.answer("❌ فقط عدد ارسال کنید (0 = نامحدود):")
        return
    volume_gb = int(clean_numeric_id(message.text))
    db.update_vip_plan(plan_key, volume_gb=volume_gb)
    await _after_vip_plan_edit(
        message, state, plan_key, f"✅ حجم پلن به‌روزرسانی شد:\n🗜 {volume_gb if volume_gb else 'نامحدود'} گیگ"
    )


@router.message(AdminStates.waiting_vip_plan_edit_days)
async def admin_vip_plan_edit_days_apply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    plan_key = data.get("edit_vip_plan_key")
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await message.answer("❌ فقط عدد ارسال کنید (0 = نامحدود):")
        return
    days = int(clean_numeric_id(message.text))
    db.update_vip_plan(plan_key, days=days)
    await _after_vip_plan_edit(
        message, state, plan_key, f"✅ مدت پلن به‌روزرسانی شد:\n⏳ {days if days else 'نامحدود'} روز"
    )


@router.message(AdminStates.waiting_vip_plan_edit_userlimit)
async def admin_vip_plan_edit_userlimit_apply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    plan_key = data.get("edit_vip_plan_key")
    user_limit = parse_int_in_range((message.text or "").strip(), 0, 10)
    if user_limit is None:
        await message.answer("❌ عددی بین ۰ تا ۱۰ ارسال کنید (۰ = نامحدود):")
        return
    db.update_vip_plan(plan_key, user_limit=user_limit)
    await _after_vip_plan_edit(
        message, state, plan_key,
        f"✅ سقف کاربر همزمان پلن به‌روزرسانی شد:\n👥 {'نامحدود' if user_limit == 0 else f'{user_limit} کاربر'}"
    )


@router.callback_query(F.data.startswith("delvipplan_"))
async def admin_delete_vip_plan(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    plan_key = callback.data.replace("delvipplan_", "")
    plan = db.get_vip_plan(plan_key)
    if plan is None:
        await callback.answer("❌ این پلن یافت نشد.", show_alert=True)
        return
    cat = db.get_vip_category(plan["category_id"])
    db.delete_vip_plan(plan_key)
    await callback.answer("✅ پلن حذف شد.")
    if cat:
        callback.data = f"admincat_{cat['key']}"
        await admin_vip_category_detail(callback)
    else:
        await admin_vip_categories_list(callback)


# --- ↕️ تغییر ترتیب دسته‌بندی‌ها/پلن‌های VIP ---
@router.callback_query(F.data.startswith("movevipcat_"))
async def admin_move_vip_category(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    raw = callback.data.replace("movevipcat_", "")
    category_key, _, direction = raw.rpartition("_")
    cat = db.get_vip_category(category_key)
    if cat is None:
        await callback.answer("❌ این دسته یافت نشد.", show_alert=True)
        return
    db.move_vip_category(cat["id"], direction)
    await callback.answer()
    await admin_vip_categories_list(callback)


@router.callback_query(F.data.startswith("movevipplan_"))
async def admin_move_vip_plan(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    raw = callback.data.replace("movevipplan_", "")
    plan_key, _, direction = raw.rpartition("_")
    plan = db.get_vip_plan(plan_key)
    if plan is None:
        await callback.answer("❌ این پلن یافت نشد.", show_alert=True)
        return
    db.move_vip_plan(plan["id"], direction)
    await callback.answer()
    cat = db.get_vip_category(plan["category_id"])
    if cat:
        callback.data = f"admincat_{cat['key']}"
        await admin_vip_category_detail(callback)


# 📢 پیام همگانی
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await callback.message.edit_text(
        "📢 پیامی که می‌خواهید برای همه کاربران ارسال شود را بفرستید (متن، عکس، فیلم یا یک پیام فوروارد‌شده هم می‌توانید بفرستید):",
        reply_markup=admin_back_button(),
    )
    await state.set_state(UserStates.waiting_broadcast)
    await callback.answer()


@router.message(UserStates.waiting_broadcast)
async def admin_broadcast_send(message: types.Message, state: FSMContext):
    # 🐛 فیکس: قبلاً این هندلر فقط برای ADMIN_ID (ادمین اصلی) فیلتر شده بود، در حالی که
    # دکمه‌ی «📢 پیام همگانی» (چه از منوی پایین صفحه، چه از پنل ادمین) به ادمین‌های فرعی
    # دارای مجوز broadcast هم اجازه‌ی ورود به همین state را می‌داد. در نتیجه وقتی ادمین
    # فرعی متن پیام همگانی را ارسال می‌کرد، هیچ هندلری آن پیام را نمی‌گرفت (فیلتر رد می‌شد)
    # و کاربر بدون هیچ پاسخی می‌ماند. حالا مجوز را دوباره همینجا (به‌جای فیلتر ADMIN_ID) چک می‌کنیم.
    if not _admin_perm(message.from_user.id, "broadcast"):
        return
    # پیام همگانی به همه‌ی کاربران؛ copy_message هر نوع پیامی (متن، عکس،
    # فیلم، فوروارد‌شده) را عیناً ارسال می‌کند؛ کاربران مسدود حذف می‌شوند و بین
    # هر ارسال یک مکث کوتاه تاخیر برای جلوگیری از محدودیت flood تلگرام گذارده می‌شود.
    # بهینه‌سازی سرعت: قبلاً برای هر کاربر یک کوئری جداگانه به دیتابیس زده می‌شد (is_user_blocked)
    # درحالی که وضعیت بلاک همین الان داخل خروجی get_all_users() موجود بود (N+1 کوئری). حالا مستقیم از همان دیتای بارگذاری‌شده استفاده می‌شود.
    users = [u for u in db.get_all_users() if not u.get("is_blocked")]
    sent, failed = 0, 0

    status_msg = await message.answer(f"📢 در حال ارسال به {len(users)} کاربر...")

    # 🚀 بهینه‌سازی سرعت: به‌جای ارسال یکی‌یکی و توقف ثابت بین هر پیام (که برای یک گروه بزرگ کاربر خیلی کند می‌شود)، حالا تا سقف مجاز تلگرام (حدوداً ۲۵-۳۰ پیام در ثانیه) به‌صورت هم‌زمان ارسال می‌شوند (دهها برابر سریع‌تر از حالت قبلی که یکی‌یکی با تاخیر ۵۰ میلی‌ثانیه ارسال می‌شد).
    semaphore = asyncio.Semaphore(25)
    lock = asyncio.Lock()
    counters = {"sent": 0, "failed": 0, "done": 0}

    async def _send_one(u):
        async with semaphore:
            try:
                await send_notification_sticker(message.bot, int(u["telegram_id"]), "notif_broadcast")
                await message.bot.copy_message(int(u["telegram_id"]), message.chat.id, message.message_id)
                ok = True
            except Exception:
                ok = False
        async with lock:
            counters["done"] += 1
            if ok:
                counters["sent"] += 1
            else:
                counters["failed"] += 1
            if counters["done"] % 200 == 0:
                try:
                    await status_msg.edit_text(
                        f"📢 در حال ارسال... ({counters['done']}/{len(users)}) ✅ {counters['sent']} | ❌ {counters['failed']}"
                    )
                except Exception:
                    pass

    await asyncio.gather(*(_send_one(u) for u in users))
    sent, failed = counters["sent"], counters["failed"]

    await status_msg.edit_text(f"✅ ارسال شد به {sent} نفر. ناموفق: {failed} نفر.")
    await state.clear()


# ---------------------------------------------------------------------------
# 📚 مدیریت راهنما و اموزش‌ها — افزودن/ویرایش/حذف/تغییر ترتیب (متن/عکس/فیلم)
# ---------------------------------------------------------------------------
def _guide_detail_text(guide: dict) -> str:
    text = f"📚 {guide['title']}"
    if guide.get("body_text"):
        text += f"\n\n{guide['body_text']}"
    return text


async def _send_guide_detail(target: types.Message, guide_id: int):
    guide = db.get_guide(guide_id)
    if guide is None:
        await target.answer("❌ این راهنما دیگر موجود نیست.")
        return
    guides = db.get_guides()
    idx = next((i for i, g in enumerate(guides) if g["id"] == guide_id), 0)
    caption = _guide_detail_text(guide)
    kb = admin_guide_detail_keyboard(guide_id, idx, len(guides))
    try:
        if guide["content_type"] == "photo" and guide.get("file_id"):
            await target.answer_photo(guide["file_id"], caption=caption, reply_markup=kb)
        elif guide["content_type"] == "video" and guide.get("file_id"):
            await target.answer_video(guide["file_id"], caption=caption, reply_markup=kb)
        else:
            await target.answer(caption, reply_markup=kb)
    except Exception:
        await target.answer(caption, reply_markup=kb)


@router.callback_query(F.data == "admin_guides")
async def admin_guides_list(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    guides = db.get_guides()
    text = (
        f"📚 مدیریت راهنما و اموزش‌ها\n\nتعداد: {len(guides)}\n\n"
        "از اینجا می‌تونید راهنما/آموزش جدید اضافه کنید یا موردهای موجود را ویرایش کنید:"
    )
    try:
        await callback.message.edit_text(text, reply_markup=admin_guides_menu(guides))
    except Exception:
        await callback.message.answer(text, reply_markup=admin_guides_menu(guides))
    await callback.answer()


@router.callback_query(F.data == "guidenew")
async def admin_guide_new_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_guide_title)
    await callback.message.answer(
        "📚 عنوان راهنما/آموزش جدید را بفرستید:",
        reply_markup=admin_guide_cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_guide_title)
async def admin_guide_new_title(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    title = (message.text or "").strip()
    if not title:
        await message.answer("❌ عنوان نمی‌تواند خالی باشد. دوباره بفرستید:")
        return
    await state.update_data(guide_new_title=title)
    await state.set_state(AdminStates.waiting_guide_content)
    await message.answer(
        "📝 حالا محتوای این راهنما را بفرستید (متن، عکس با کپشن، یا فیلم با کپشن):",
        reply_markup=admin_guide_cancel_keyboard(),
    )


@router.message(AdminStates.waiting_guide_content)
async def admin_guide_new_content(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    data = await state.get_data()
    title = data.get("guide_new_title")
    if not title:
        await state.clear()
        return

    if message.photo:
        content_type, file_id, body_text = "photo", message.photo[-1].file_id, (message.caption or "")
    elif message.video:
        content_type, file_id, body_text = "video", message.video.file_id, (message.caption or "")
    else:
        content_type, file_id, body_text = "text", None, (message.text or "")

    if not body_text and not file_id:
        await message.answer("❌ محتوا نمی‌تواند خالی باشد. دوباره بفرستید:")
        return

    guide = db.create_guide(title=title, content_type=content_type, body_text=body_text or None, file_id=file_id)
    await state.clear()
    await message.answer(f"✅ راهنمای «{title}» اضافه شد.")
    await _send_guide_detail(message, guide["id"])


@router.callback_query(F.data.startswith("guideadminopen_"))
async def admin_guide_open(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    guide_id = int(callback.data.replace("guideadminopen_", ""))
    await _send_guide_detail(callback.message, guide_id)
    await callback.answer()


@router.callback_query(F.data.startswith("guidemove_"))
async def admin_guide_move(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    raw = callback.data.replace("guidemove_", "")
    guide_id_str, _, direction = raw.rpartition("_")
    guide_id = int(guide_id_str)
    db.move_guide(guide_id, direction)
    await _send_guide_detail(callback.message, guide_id)
    await callback.answer("↕️ ترتیب به‌روزرسانی شد.")


@router.callback_query(F.data.startswith("guideeditname_"))
async def admin_guide_edit_name_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    guide_id = int(callback.data.replace("guideeditname_", ""))
    guide = db.get_guide(guide_id)
    if guide is None:
        await callback.answer("❌ این راهنما دیگر موجود نیست.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_guide_edit_title)
    await state.update_data(guide_edit_id=guide_id)
    await callback.message.answer(
        f"✏️ عنوان جدید برای «{guide['title']}» را بفرستید:",
        reply_markup=admin_guide_cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_guide_edit_title)
async def admin_guide_edit_name_save(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    data = await state.get_data()
    guide_id = data.get("guide_edit_id")
    if not guide_id:
        await state.clear()
        return
    title = (message.text or "").strip()
    if not title:
        await message.answer("❌ عنوان نمی‌تواند خالی باشد. دوباره بفرستید:")
        return
    db.update_guide(guide_id, title=title)
    await state.clear()
    await message.answer("✅ عنوان به‌روزرسانی شد.")
    await _send_guide_detail(message, guide_id)


@router.callback_query(F.data.startswith("guideeditcontent_"))
async def admin_guide_edit_content_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    guide_id = int(callback.data.replace("guideeditcontent_", ""))
    guide = db.get_guide(guide_id)
    if guide is None:
        await callback.answer("❌ این راهنما دیگر موجود نیست.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_guide_edit_content)
    await state.update_data(guide_edit_id=guide_id)
    await callback.message.answer(
        f"📝 محتوای جدید برای «{guide['title']}» را بفرستید (متن، عکس با کپشن، یا فیلم با کپشن):",
        reply_markup=admin_guide_cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_guide_edit_content)
async def admin_guide_edit_content_save(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    data = await state.get_data()
    guide_id = data.get("guide_edit_id")
    if not guide_id:
        await state.clear()
        return

    if message.photo:
        content_type, file_id, body_text = "photo", message.photo[-1].file_id, (message.caption or "")
    elif message.video:
        content_type, file_id, body_text = "video", message.video.file_id, (message.caption or "")
    else:
        content_type, file_id, body_text = "text", None, (message.text or "")

    if not body_text and not file_id:
        await message.answer("❌ محتوا نمی‌تواند خالی باشد. دوباره بفرستید:")
        return

    db.update_guide(guide_id, content_type=content_type, body_text=body_text, file_id=file_id)
    await state.clear()
    await message.answer("✅ محتوا به‌روزرسانی شد.")
    await _send_guide_detail(message, guide_id)


@router.callback_query(F.data.startswith("guidedelete_"))
async def admin_guide_delete_confirm(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    guide_id = int(callback.data.replace("guidedelete_", ""))
    guide = db.get_guide(guide_id)
    if guide is None:
        await callback.answer("❌ این راهنما دیگر موجود نیست.", show_alert=True)
        return
    await callback.message.answer(
        f"❗️ آیا از حذف «{guide['title']}» مطمئن هستید؟",
        reply_markup=admin_guide_delete_confirm_keyboard(guide_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("guidedeleteconfirm_"))
async def admin_guide_delete_do(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    guide_id = int(callback.data.replace("guidedeleteconfirm_", ""))
    guide = db.get_guide(guide_id)
    title = guide["title"] if guide else ""
    db.delete_guide(guide_id)
    guides = db.get_guides()
    await callback.message.answer(
        f"✅ راهنمای «{title}» حذف شد.\n\n📚 مدیریت راهنما و اموزش‌ها\n\nتعداد: {len(guides)}",
        reply_markup=admin_guides_menu(guides),
    )
    await callback.answer("🗑 حذف شد.")


# ---------------------------------------------------------------------------
# 🎬 مدیریت استیکر/ویدیوی تستی هر بخش از منو
# ---------------------------------------------------------------------------
_STICKER_SECTION_ORDER = list(STICKER_SECTION_LABELS.keys())


def _sticker_status(section_key: str):
    """وضعیت فعلی یک بخش را برمی‌گرداند: (is_enabled, has_custom, file_id_or_None)."""
    row = db.get_section_sticker(section_key)
    if row is None:
        return True, False, None  # پیش‌فرض: فعال، بدون سفارشی‌سازی
    return bool(row["is_enabled"]), True, row.get("file_id")


async def _send_sticker_section_detail(target: types.Message, section_key: str):
    label = STICKER_SECTION_LABELS.get(section_key, section_key)
    is_enabled, has_custom, file_id = _sticker_status(section_key)

    if is_enabled:
        status_line = "✅ فعال — سفارشی (آپلود‌شده توسط ادمین)" if has_custom else "➖ فعال — استیکر پیش‌فرض پروژه"
    else:
        status_line = "🛑 غیرفعال — هیچ استیکری نشان داده نمی‌شود"

    text = f"🎬 {label}\n\nوضعیت: {status_line}"
    kb = admin_sticker_detail_keyboard(section_key, has_custom=has_custom, is_enabled=is_enabled)

    try:
        if is_enabled:
            if file_id:
                await target.answer_sticker(file_id)
            else:
                filename = STICKER_FILES.get(section_key)
                if filename:
                    await target.answer_sticker(FSInputFile(os.path.join(STICKERS_DIR, filename)))
    except Exception:
        logger.exception("خطا در پیش‌نمایش استیکر بخش '%s'", section_key)

    await target.answer(text, reply_markup=kb)


@router.callback_query(F.data == "admin_stickers")
async def admin_stickers_list(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    sections = []
    for key in _STICKER_SECTION_ORDER:
        is_enabled, has_custom, _fid = _sticker_status(key)
        if not is_enabled:
            emoji = "🛑"
        elif has_custom:
            emoji = "✅"
        else:
            emoji = "➖"
        sections.append({"key": key, "label": STICKER_SECTION_LABELS.get(key, key), "status_emoji": emoji})
    text = (
        "🎬 مدیریت استیکرهای منو\n\n"
        "✅ = سفارشی‌شده، ➖ = پیش‌فرض پروژه، 🛑 = غیرفعال\n\n"
        "یکی از بخش‌ها رو انتخاب کن:"
    )
    try:
        await callback.message.edit_text(text, reply_markup=admin_stickers_menu(sections))
    except Exception:
        await callback.message.answer(text, reply_markup=admin_stickers_menu(sections))
    await callback.answer()


@router.message(F.text == "🎬 استیکرهای منو")
async def admin_stickers_list_msg(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    sections = []
    for key in _STICKER_SECTION_ORDER:
        is_enabled, has_custom, _fid = _sticker_status(key)
        if not is_enabled:
            emoji = "🛑"
        elif has_custom:
            emoji = "✅"
        else:
            emoji = "➖"
        sections.append({"key": key, "label": STICKER_SECTION_LABELS.get(key, key), "status_emoji": emoji})
    text = (
        "🎬 مدیریت استیکرهای منو\n\n"
        "✅ = سفارشی‌شده، ➖ = پیش‌فرض پروژه، 🛑 = غیرفعال\n\n"
        "یکی از بخش‌ها رو انتخاب کن:"
    )
    await message.answer(text, reply_markup=admin_stickers_menu(sections))


@router.callback_query(F.data.startswith("stickeropen_"))
async def admin_sticker_open(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    section_key = callback.data.replace("stickeropen_", "")
    if section_key not in STICKER_SECTION_LABELS:
        await callback.answer("❌ بخش یافت نشد.", show_alert=True)
        return
    await _send_sticker_section_detail(callback.message, section_key)
    await callback.answer()


@router.callback_query(F.data.startswith("stickerset_"))
async def admin_sticker_set_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    section_key = callback.data.replace("stickerset_", "")
    if section_key not in STICKER_SECTION_LABELS:
        await callback.answer("❌ بخش یافت نشد.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_sticker_upload)
    await state.update_data(sticker_section_key=section_key)
    label = STICKER_SECTION_LABELS.get(section_key, section_key)
    await callback.message.answer(
        f"📤 استیکر/فایل موردنظرت رو برای «{label}» بفرست.\n\n"
        "فقط یک استیکر متحرک (ویدیویی) معتبر تلگرام قابل قبوله؛ هر ویدیوی معمولی رو تلگرام به‌عنوان استیکر قبول نمی‌کنه.",
        reply_markup=admin_sticker_cancel_keyboard(section_key),
    )
    await callback.answer()


@router.message(AdminStates.waiting_sticker_upload)
async def admin_sticker_upload_receive(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    data = await state.get_data()
    section_key = data.get("sticker_section_key")
    if not section_key:
        await state.clear()
        return

    file_id = None
    if message.sticker:
        file_id = message.sticker.file_id
    elif message.document:
        file_id = message.document.file_id
    elif message.video:
        file_id = message.video.file_id
    elif message.animation:
        file_id = message.animation.file_id

    if not file_id:
        await message.answer("❌ فقط استیکر یا فایل/ویدیوی قابل قبوله. دوباره بفرست یا انصراف بده:")
        return

    db.set_section_sticker(section_key, file_id)
    invalidate_section_sticker_cache(section_key)
    await state.clear()
    label = STICKER_SECTION_LABELS.get(section_key, section_key)
    await message.answer(f"✅ استیکر «{label}» ذخیره شد.")
    await _send_sticker_section_detail(message, section_key)


@router.callback_query(F.data.startswith("stickeroff_"))
async def admin_sticker_off(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    section_key = callback.data.replace("stickeroff_", "")
    if section_key not in STICKER_SECTION_LABELS:
        await callback.answer("❌ بخش یافت نشد.", show_alert=True)
        return
    db.set_section_sticker_enabled(section_key, False)
    invalidate_section_sticker_cache(section_key)
    await _send_sticker_section_detail(callback.message, section_key)
    await callback.answer("🛑 غیرفعال شد.")


@router.callback_query(F.data.startswith("stickeron_"))
async def admin_sticker_on(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    section_key = callback.data.replace("stickeron_", "")
    if section_key not in STICKER_SECTION_LABELS:
        await callback.answer("❌ بخش یافت نشد.", show_alert=True)
        return
    db.set_section_sticker_enabled(section_key, True)
    invalidate_section_sticker_cache(section_key)
    await _send_sticker_section_detail(callback.message, section_key)
    await callback.answer("✅ فعال شد.")


@router.callback_query(F.data.startswith("stickerreset_"))
async def admin_sticker_reset(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    section_key = callback.data.replace("stickerreset_", "")
    if section_key not in STICKER_SECTION_LABELS:
        await callback.answer("❌ بخش یافت نشد.", show_alert=True)
        return
    db.reset_section_sticker(section_key)
    invalidate_section_sticker_cache(section_key)
    await _send_sticker_section_detail(callback.message, section_key)
    await callback.answer("♻️ به حالت پیش‌فرض برگشت.")


# ---------------------------------------------------------------------------
# 💾 بکاپ
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "admin_backup")
async def admin_backup(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    try:
        if db.USE_TURSO:
            export_path = "/tmp/backup_export.json"
            db.export_backup_json(export_path)
            backup_file = FSInputFile(export_path, filename="backup.json")
            caption = "💾 بکاپ دیتابیس (Turso — JSON)"
        else:
            backup_file = FSInputFile(DATABASE_PATH)
            caption = "💾 بکاپ دیتابیس"
        await callback.message.answer_document(backup_file, caption=caption)
    except Exception:
        await callback.message.answer("❌ خطا در ساخت بکاپ.")
    await callback.answer()


# ---------------------------------------------------------------------------
# ℹ️ اطلاعات ربات (قالب فروشی)
# ---------------------------------------------------------------------------
def _botinfo_status_text():
    values = bot_info.all_values()
    labels = bot_info.labels()
    lines = ["ℹ️ اطلاعات ربات\n", "برای ویرایش روی هر کدام بزنید:\n"]
    for key, label in labels.items():
        val = values.get(key) or "—"
        lines.append(f"• {label}: {val}")
    channels = bot_info.get_required_channels()
    lines.append(f"\n• کانال‌های اجباری: {len(channels)} عدد")
    return "\n".join(lines)


@router.callback_query(F.data == "admin_botinfo")
async def admin_botinfo_open(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    try:
        await callback.message.edit_text(_botinfo_status_text(), reply_markup=admin_botinfo_menu())
    except TelegramBadRequest:
        await callback.message.answer(_botinfo_status_text(), reply_markup=admin_botinfo_menu())
    await callback.answer()


@router.message(F.text == "ℹ️ اطلاعات ربات")
async def admin_botinfo_open_msg(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(_botinfo_status_text(), reply_markup=admin_botinfo_menu())


@router.callback_query(F.data.startswith("botinfoedit_"))
async def admin_botinfo_edit_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    key = callback.data.replace("botinfoedit_", "")
    labels = bot_info.labels()
    if key not in labels:
        await callback.answer("❌ یافت نشد.", show_alert=True)
        return
    await state.update_data(botinfo_key=key)
    await state.set_state(AdminStates.waiting_botinfo_value)
    current = bot_info.get(key) or "—"
    await callback.message.answer(
        f"✏️ مقدار جدید برای «{labels[key]}» را بفرستید.\nمقدار فعلی: {current}",
        reply_markup=admin_botinfo_field_keyboard(key),
    )
    await callback.answer()


@router.message(AdminStates.waiting_botinfo_value)
async def admin_botinfo_edit_save(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    data = await state.get_data()
    key = data.get("botinfo_key")
    labels = bot_info.labels()
    if not key or key not in labels:
        await state.clear()
        await message.answer("❌ خطای داخلی. دوباره تلاش کنید.")
        return
    value = (message.text or "").strip()
    if key == "config_name_prefix":
        cleaned = re.sub(r"[^A-Za-z0-9_]+", "", value)
        if not cleaned:
            await message.answer(
                "❌ پیشوند باید فقط از حروف/عدد انگلیسی و زیرخط (_) تشکیل شده باشد؛ دوباره وارد کن:"
            )
            return
        value = cleaned
    from utils import _telegram_utf16_len
    if _telegram_utf16_len(value) > 4096:
        await message.answer(
            f"❌ این متن از سقف ۴۰۹۶ واحد UTF-16 تلگرام بیشتر است ({_telegram_utf16_len(value)}). لطفاً متن کوتاه‌تری بفرست:"
        )
        return
    entities = []
    for entity in (message.entities or []):
        try:
            entities.append(entity.model_dump(exclude_none=True))
        except Exception:
            try:
                entities.append(entity.dict(exclude_none=True))
            except Exception:
                pass
    bot_info.set(key, value, entities=entities if key == "welcome_text" else None)
    await state.clear()
    await message.answer(f"✅ «{labels[key]}» به‌روز شد.", reply_markup=admin_botinfo_menu())


@router.callback_query(F.data == "botinfochannels")
async def admin_botinfo_channels_open(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    channels = bot_info.get_required_channels()
    text = "📢 کانال‌های عضویت اجباری\n\nروی هرکدام بزنید تا حذف شود." if channels else "📢 هیچ کانال اجباریثبت نشده."
    try:
        await callback.message.edit_text(text, reply_markup=admin_botinfo_channels_menu(channels))
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=admin_botinfo_channels_menu(channels))
    await callback.answer()


@router.callback_query(F.data == "botinfochadd")
async def admin_botinfo_channel_add_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_botinfo_channel_add)
    await callback.message.answer(
        "➕ افزودن کانال اجباری\n\nفرمت زیر را ارسال کنید (با | جدا شده):\nآیدی/یوزرنیم کانال | نام نمایشی | لینک دعوت\nمثال: -1001234567890 | کانال ما | https://t.me/mychannel",
        reply_markup=admin_botinfo_field_keyboard(""),
    )
    await callback.answer()


@router.message(AdminStates.waiting_botinfo_channel_add)
async def admin_botinfo_channel_add_save(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    parts = [p.strip() for p in (message.text or "").split("|")]
    # 🐛 فیکس: قبلاً فقط خالی‌نبودن آیدی کانال (parts[0]) چک می‌شد؛ اگر ادمین لینک دعوت
    # (parts[2]) را خالی می‌فرستاد یا فراموش می‌کرد، همان مقدار خالی مستقیم به‌عنوان url
    # دکمه‌ی «عضویت» ذخیره می‌شد و بعداً برای هر کاربری که هنوز عضو نشده بود، تلگرام موقع
    # نمایش منوی عضویت اجباری خطای BUTTON_URL_INVALID می‌داد و /start با ارور مواجه می‌شد.
    if len(parts) != 3 or not parts[0] or not parts[2]:
        await message.answer("❌ فرمت نادرست یا لینک دعوت خالی است. هر سه بخش (آیدی | نام | لینک دعوت) باید پر باشند. دوباره تلاش کنید یا /cancel بزنید.")
        return
    raw_chat_id, name, url = parts
    raw_chat_id = clean_numeric_id(raw_chat_id)

    # 🐛 فیکس: قبلاً هرچی ادمین می‌فرستاد بدون هیچ بررسی ذخیره می‌شد؛ اگر فرمت آیدی/یوزرنیم اشتباه بود (مثلاً بدون پیشوند "-100" برای کانال/سوپرگروه، یا بودن خود لینک دعوت به‌جای آیدی/یوزرنیم) یا ربات هنوز ادمین کانال نشده بود، ربات بی‌صدا ذخیره می‌شد و بعداً تأیید عضویت هیچ‌وقت برای هیچ کاربری درست قبول نمی‌شد. اینجا همان لحظه خود ربات را تست می‌کنیم.
    chat_id_input = raw_chat_id if raw_chat_id.startswith("@") or raw_chat_id.startswith("-") or raw_chat_id.lstrip("-").isdigit() else f"@{raw_chat_id.lstrip('@')}"
    try:
        chat = await message.bot.get_chat(chat_id_input)
    except Exception as e:
        await message.answer(
            "❌ ربات نتوانست این کانال را پیدا کند. ممکن است:\n"
            "• فرمت آیدی اشتباه باشد (برای کانال باید با «-100» شروع شود، متل -1001234567890)\n"
            "• ربات هنوز به این کانال اضافه/عضو نشده باشد\n\n"
            f"خطای دقیق: {e}\n\nدوباره تلاش کنید یا /cancel بزنید."
        )
        return

    try:
        bot_member = await message.bot.get_chat_member(chat.id, message.bot.id)
        if bot_member.status not in ("administrator", "creator"):
            await message.answer(
                "⚠️ ربات عضو این کانال هست ولی «ادمین» نیست. برای اینکه ربات بتواند عضویت کاربرها را در این کانال ببیند، باید ربات را در آن کانال «ادمین» کنی ‌(نه فقط عضو)، بعد دوباره همین پیام را بفرست."
            )
            return
    except Exception as e:
        await message.answer(f"❌ بررسی وضعیت عضویت ربات در این کانال با خطا مواجه شد: {e}\n\nدوباره تلاش کنید یا /cancel بزنید.")
        return

    # 🐛 فیکس: به‌جای متنی که ادمین خودش تایپ کرده، همیشه از chat.id عددی واقعی که تلگرام برمی‌گرداند استفاده می‌کنیم تا همیشه با همان فرمتی که check_membership انتظار دارد ذخیره شود (نه متنی که ادمین تایکرده و ممکن است فرمتش اشتباه باشد).
    bot_info.add_required_channel(chat.id, name, url)
    await state.clear()
    channels = bot_info.get_required_channels()
    await message.answer(
        f"✅ کانال اضافه شد و تایید شد که ربات به درستی در آن ادمین است (آیدی واقعی: {chat.id}).",
        reply_markup=admin_botinfo_channels_menu(channels),
    )


@router.callback_query(F.data.startswith("botinfochdel_"))
async def admin_botinfo_channel_del(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    chat_id = callback.data.replace("botinfochdel_", "")
    bot_info.remove_required_channel(chat_id)
    channels = bot_info.get_required_channels()
    text = "📢 کانال‌های عضویت اجباری" if channels else "📢 هیچ کانال اجباریثبت نشده."
    try:
        await callback.message.edit_text(text, reply_markup=admin_botinfo_channels_menu(channels))
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=admin_botinfo_channels_menu(channels))
    await callback.answer("✅ حذف شد.")


@router.message(F.text == "📝 مدیریت متن‌های کاربر")
async def admin_texts_from_menu(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        "📝 مدیریت جامع متن‌های کاربر و اعلان‌ها\n\nیک بخش را انتخاب کنید:",
        reply_markup=_text_manager_keyboard(),
    )


@router.callback_query(F.data == "errlog")
async def admin_error_logs_open_callback(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await _open_error_logs(callback, edit=True)
    await callback.answer()


@router.callback_query(F.data == "admin_free_test_settings")
async def admin_free_test_settings_open_callback(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_free_test_settings)
    await callback.message.edit_text(_free_test_settings_text())
    await callback.answer()


# ---------------------------------------------------------------------------
# 🎁 تنظیم حجم/روز پلن «تست رایگان» از پنل ادمین
# ---------------------------------------------------------------------------
FREE_TEST_MIN_VOLUME_MB = 50
FREE_TEST_MAX_VOLUME_MB = 1024
FREE_TEST_MIN_DAYS = 1
FREE_TEST_MAX_DAYS = 7
FREE_TEST_MIN_PRICE = 0
FREE_TEST_MAX_PRICE = 2000


def _free_test_settings_text() -> str:
    plan = db.get_effective_free_test_plan()
    price = plan.get("price", 0)
    price_label = "رایگان" if price == 0 else f"{price:,} تومان"
    return (
        "🎁 تنظیم پلن «تست رایگان»\n\n"
        f"مقدار فعلی: {plan['name']} — قیمت: {price_label}\n\n"
        "برای تغییر، حجم (مگابایت)، تعداد روز و قیمت (تومان) را با | جدا و ارسال کنید.\n"
        f"محدوده‌ی مجاز: حجم بین {FREE_TEST_MIN_VOLUME_MB} تا {FREE_TEST_MAX_VOLUME_MB} مگابایت، "
        f"روز بین {FREE_TEST_MIN_DAYS} تا {FREE_TEST_MAX_DAYS} روز، "
        f"قیمت بین {FREE_TEST_MIN_PRICE} (رایگان) تا {FREE_TEST_MAX_PRICE} تومان.\n"
        "مثال: 500 | 3 | 1500\n\n"
        "برای انصراف /cancel بزنید."
    )


@router.message(F.text == "🎁 تنظیم تست رایگان")
async def admin_free_test_settings_open(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.set_state(AdminStates.waiting_free_test_settings)
    await message.answer(_free_test_settings_text())


@router.message(AdminStates.waiting_free_test_settings)
async def admin_free_test_settings_save(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    if text == "/cancel":
        await state.clear()
        await message.answer("❌ لغو شد.", reply_markup=_admin_reply_kb_for(message.from_user.id))
        return
    parts = [p.strip() for p in text.split("|")]
    if len(parts) != 3:
        await message.answer("❌ فرمت نادرست. مثال درست: 500 | 3 | 1500\n\nدوباره تلاش کنید یا /cancel بزنید.")
        return
    volume_str, days_str, price_str = parts
    volume_mb = parse_int_in_range(volume_str, FREE_TEST_MIN_VOLUME_MB, FREE_TEST_MAX_VOLUME_MB)
    days = parse_int_in_range(days_str, FREE_TEST_MIN_DAYS, FREE_TEST_MAX_DAYS)
    price = parse_int_in_range(price_str, FREE_TEST_MIN_PRICE, FREE_TEST_MAX_PRICE)
    if volume_mb is None or days is None or price is None:
        await message.answer(
            f"❌ مقدار نامعتبر. حجم باید بین {FREE_TEST_MIN_VOLUME_MB} تا {FREE_TEST_MAX_VOLUME_MB} مگابایت، "
            f"روز باید بین {FREE_TEST_MIN_DAYS} تا {FREE_TEST_MAX_DAYS} و "
            f"قیمت باید بین {FREE_TEST_MIN_PRICE} تا {FREE_TEST_MAX_PRICE} تومان باشد.\n\nدوباره تلاش کنید یا /cancel بزنید."
        )
        return
    try:
        db.set_free_test_override(volume_mb, days, price)
    except Exception as e:
        logger.exception("خطا در ذخیره تنظیمات تست رایگان")
        await message.answer(f"❌ ذخیره تنظیمات ناموفق بود: {e}")
        return
    await state.clear()
    plan = db.get_effective_free_test_plan()
    price_label = "رایگان" if price == 0 else f"{price:,} تومان"
    await message.answer(
        f"✅ پلن «تست رایگان» به‌روزرسانی شد: {plan['name']} — قیمت: {price_label}",
        reply_markup=_admin_reply_kb_for(message.from_user.id),
    )


# ---------------------------------------------------------------------------
# 🧩 تنظیم قیمت/محدوده‌ی «بساز سرویس خودت» از پنل ادمین
# ---------------------------------------------------------------------------








# ---------------------------------------------------------------------------
# 🛡️ اتصال پنل پاسارگارد (پنل VPN دوم، در کنار مرزبان)
# ---------------------------------------------------------------------------
def _pasargad_status_text():
    lines = ["🛡️ پنل پاسارگارد (PasarGuard)\n"]
    lines.append(f"وضعیت: {'✅ فعال' if PASARGAD_ENABLED else '⚪ غیرفعال (PASARGAD_BASE_URL/PASARGAD_USERNAME/PASARGAD_PASSWORD در env تنظیم نشده)'}")
    return "\n".join(lines)


@router.callback_query(F.data == "admin_pasargad")
async def admin_pasargad_open(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    text = _pasargad_status_text()
    try:
        await callback.message.edit_text(text, reply_markup=admin_pasargad_menu(text))
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=admin_pasargad_menu(text))
    await callback.answer()


@router.message(F.text == "🛡️ اتصال پنل پاسارگارد")
async def admin_pasargad_open_msg(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    text = _pasargad_status_text()
    await message.answer(text, reply_markup=admin_pasargad_menu(text))


@router.callback_query(F.data == "pasargadtest")
async def admin_pasargad_test(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await callback.answer("⏳ در حال بررسی...")
    ok, data, msg = await pasargad.test_connection()
    icon = "✅" if ok else "❌"
    await callback.message.answer(f"{icon} {msg}")


# ---------------------------------------------------------------------------
# 🦋 اتصال پنل Rebecca
# ---------------------------------------------------------------------------
def _rebecca_status_text():
    lines = ["🦋 پنل Rebecca\n"]
    lines.append(f"وضعیت: {'✅ فعال' if REBECCA_ENABLED else '⚪ غیرفعال (REBECCA_BASE_URL/REBECCA_USERNAME/REBECCA_PASSWORD در env تنظیم نشده)'}")
    lines.append("\nنکته: ربات قابلیت HWID را از OpenAPI خود Rebecca تشخیص می‌دهد؛ اگر HWID در API اعلام نشده باشد، برای جلوگیری از فروش محدودیتِ اجرا‌نشده، سفارش با سقف دستگاه رد می‌شود.")
    return "\n".join(lines)


@router.callback_query(F.data == "admin_rebecca")
async def admin_rebecca_open(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    text = _rebecca_status_text()
    try:
        await callback.message.edit_text(text, reply_markup=admin_rebecca_menu())
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=admin_rebecca_menu())
    await callback.answer()


@router.message(F.text == "🦋 اتصال پنل Rebecca")
async def admin_rebecca_open_msg(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(_rebecca_status_text(), reply_markup=admin_rebecca_menu())


@router.callback_query(F.data == "rebeccatest")
async def admin_rebecca_test(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    import rebecca
    await callback.answer("⏳ در حال بررسی...")
    ok, data, msg = await rebecca.test_connection()
    icon = "✅" if ok else "❌"
    hwid = "" if not ok else f"\nHWID: {'✅ پشتیبانی می‌شود' if isinstance(data, dict) and data.get('hwid_supported') else '⚠️ در API تشخیص داده نشد'}"
    await callback.message.answer(f"{icon} {msg}{hwid}")


@router.callback_query(F.data == "rebecca_templates")
async def admin_rebecca_templates(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    import rebecca
    await callback.answer("⏳ در حال دریافت...")
    ok, data, msg = await rebecca.get_templates(force_refresh=True)
    if not ok:
        await callback.message.answer(f"❌ {msg}")
        return
    items = data if isinstance(data, list) else []
    lines = ["🦋 تمپلیت‌های Rebecca:\n"]
    for item in items[:50]:
        if isinstance(item, dict):
            lines.append(f"• #{item.get('id')} — {item.get('name') or item.get('remark') or 'بدون نام'}")
    if len(items) > 50:
        lines.append(f"\n... و {len(items)-50} مورد دیگر")
    await callback.message.answer("\n".join(lines) if len(lines) > 1 else "🦋 هیچ تمپلیتی در Rebecca پیدا نشد.")


# 🧩 تنظیم قیمت/محدوده‌ی «بساز سرویس خودت»
CUSTOM_BUILD_SETTINGS_MIN_PRICE = 0
CUSTOM_BUILD_SETTINGS_MAX_PRICE = 1_000_000
CUSTOM_BUILD_SETTINGS_MIN_GB_BOUND = 1
CUSTOM_BUILD_SETTINGS_MAX_GB_BOUND = 10_000
CUSTOM_BUILD_SETTINGS_MIN_DAYS_BOUND = 1
CUSTOM_BUILD_SETTINGS_MAX_DAYS_BOUND = 3650

def _custom_build_settings_text() -> str:
    s = db.get_effective_custom_build_settings()
    return (f"🧩 تنظیم «بساز سرویس خودت»\n\n"
            f"هر گیگابایت: {s['price_per_gb']:,} تومان + هر ۳۰ روز: {s['price_per_30_days']:,} تومان\n"
            f"حجم: {s['min_gb']} تا {s['max_gb']} گیگابایت — روز: {s['min_days']} تا {s['max_days']} روز\n\n"
            "قیمت هر گیگ | قیمت هر ۳۰ روز | حداقل گیگ | حداکثر گیگ | حداقل روز | حداکثر روز\n"
            "مثال: 5000 | 5000 | 5 | 1000 | 30 | 1000\n\nبرای انصراف /cancel بزنید.")

@router.callback_query(F.data == "admin_custom_build_settings")
async def admin_custom_build_settings_open_callback(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True); return
    await state.set_state(AdminStates.waiting_custom_build_settings)
    await callback.message.answer(_custom_build_settings_text(), reply_markup=admin_custom_build_settings_keyboard())
    await callback.answer()

@router.message(F.text == "🧩 تنظیم بساز سرویس خودت")
async def admin_custom_build_settings_open(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id): return
    await state.set_state(AdminStates.waiting_custom_build_settings)
    await message.answer(_custom_build_settings_text(), reply_markup=admin_custom_build_settings_keyboard())

@router.message(AdminStates.waiting_custom_build_settings)
async def admin_custom_build_settings_save(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id): return
    raw=(message.text or "").strip()
    if raw == "/cancel":
        await state.clear(); await message.answer("❌ لغو شد.", reply_markup=_admin_reply_kb_for(message.from_user.id)); return
    parts=[x.strip() for x in raw.split("|")]
    if len(parts)!=6:
        await message.answer("❌ فرمت نادرست. مثال: 5000 | 5000 | 5 | 1000 | 30 | 1000"); return
    vals=[parse_int_in_range(parts[0],0,1_000_000),parse_int_in_range(parts[1],0,1_000_000),parse_int_in_range(parts[2],1,10_000),parse_int_in_range(parts[3],1,10_000),parse_int_in_range(parts[4],1,3650),parse_int_in_range(parts[5],1,3650)]
    if any(v is None for v in vals) or vals[3] < vals[2] or vals[5] < vals[4]:
        await message.answer("❌ مقادیر نامعتبر یا محدوده برعکس است. دوباره تلاش کنید یا /cancel بزنید."); return
    db.set_custom_build_override(*vals)
    await state.clear()
    await message.answer(f"✅ تنظیمات ذخیره شد: هر گیگ {vals[0]:,} تومان + هر ۳۰ روز {vals[1]:,} تومان | حجم {vals[2]} تا {vals[3]} گیگ | روز {vals[4]} تا {vals[5]}", reply_markup=_admin_reply_kb_for(message.from_user.id))
