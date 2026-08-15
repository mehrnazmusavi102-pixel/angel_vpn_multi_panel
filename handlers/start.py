"""
handlers/start.py
دستور /start، بررسی عضویت اجباری در کانال‌ها، و پردازش لینک دعوت اختصاصی
(/start BVPNXXXXX).

نکته: منطق بررسی عضویت کانال‌ها (check_membership) دست‌نخورده باقی مانده.
"""

import logging
import traceback as _traceback_module

from aiogram import Router, F, types
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest

import database as db
from text_catalog import text as t
from utils import show_menu_with_sticker, get_main_keyboard, truncate_for_telegram, is_message_too_long_error
from keyboards import (
    join_channels_keyboard,
    main_reply_keyboard,
    admin_reply_keyboard,
)
import bot_info
from config import ADMIN_ID, REFERRAL_LOCK_AMOUNT, REFERRAL_MIN_VOLUME_GB

router = Router(name="start")
logger = logging.getLogger(__name__)


def _admin_reply_kb_for(user_id: int):
    if user_id == ADMIN_ID:
        return admin_reply_keyboard(is_main_admin=True)
    adm = db.get_sub_admin(str(user_id)) or {}
    return admin_reply_keyboard(permissions=set(adm.get("permissions") or []), is_main_admin=False)


async def check_membership(bot, user_id: int, debug: list | None = None) -> list:
    # 🐛 دیباگ موقت: اگر لیست debug پاس داده شود، برای هر کانال وضعیت/خطای دقیق تلگرام پر می‌شود تا بدون دسترسی به لاگهای سرور بتونیم ریشه‌ی دقیق رد‌شدن را پیدا کنیم.
    not_joined = []
    for ch in bot_info.get_required_channels():
        try:
            member = await bot.get_chat_member(ch["id"], user_id)
            if debug is not None:
                debug.append(f"{ch.get('name') or ch.get('id')} (id={ch.get('id')!r}): status={member.status!r}")
            if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
                not_joined.append(ch)
        except Exception as e:
            logger.error(f"check_membership failed for channel {ch['id']}: {e}")
            if debug is not None:
                debug.append(f"{ch.get('name') or ch.get('id')} (id={ch.get('id')!r}): خطا = {e}")
            not_joined.append(ch)
    return not_joined


def _ensure_user(telegram_id, full_name: str, referrer_code: str | None = None):
    """کاربر را اگر وجود نداشت می‌سازد؛ کد دعوت معتبر را هم پاس می‌دهد.
    این تابع فقط باید بعد از تأیید عضویت کاربر در کانال‌های اجباری صدا زده شود،
    چون همین‌جا رکورد دعوت ساخته و ۴۰,۰۰۰ تومان در کیف پول مسدود معرف قفل می‌شود."""
    return db.create_user(telegram_id, full_name, referrer_invite_code=referrer_code)


async def _notify_referrer_of_new_join(bot, user: dict):
    """
    وقتی عضویت یک کاربر تازه (که از لینک دعوت وارد شده) در کانال‌ها تأیید می‌شود،
    یک پیام حاوی آیدی و نام او برای معرفش ارسال می‌شود تا بداند چه کسی از طریق
    لینک او وارد ربات شده است.
    """
    if not user or not user.get("referrer_id"):
        return

    referrer = db.get_user_by_id(user["referrer_id"])
    if referrer is None:
        return

    try:
        await bot.send_message(
            int(referrer["telegram_id"]),
            f"🎉 یک عضو جدید از طریق لینک دعوت شما وارد ربات شد و عضویتش تأیید شد!\n\n"
            f"👤 نام: {user['name']}\n"
            f"🆔 آیدی: `{user['telegram_id']}`\n\n"
            f"💰 پس از اینکه این کاربر یک خرید حجم {REFERRAL_MIN_VOLUME_GB} گیگ یا بیشتر انجام دهد، "
            f"{REFERRAL_LOCK_AMOUNT:,} تومان به‌صورت خودکار به کیف پول شما آزاد می‌شود.",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"failed to notify referrer {referrer['telegram_id']}: {e}")


def _welcome_text(first_name: str) -> str:
    # 🐛 فیکس: قبلاً این متن کاملاً تایقی در کد ثابت بود و مقدار ذخیره‌شده از پنل ادمین («ℹ️ اطلاعات ربات» → «پیام خوش‌آمدگویی /start») اصلاً خوانده نمی‌شد؛ ادمین ذخیره می‌کرد ولی هیچ‌وقت در ربات واقعی دیده نمی‌شد. حالا از bot_info.get("welcome_text") خوانده می‌شود؛ اگر متن ذخیره‌شده شامل "{name}" باشد، با نام کوچک کاربر جایگزین می‌شود.
    from text_catalog import RichText
    template = RichText(bot_info.get("welcome_text"), [])
    if "{name}" in template:
        # جایگزینی {name} ممکن است offset entityهای بعد از آن را جابه‌جا کند؛
        # entityهای قبل/بعد را با اختلاف UTF-16 جدید منتقل می‌کنیم و فقط entity
        # غیرقابل‌تعیینِ داخل خود placeholder را کنار می‌گذاریم.
        raw = str(template)
        token = "{name}"
        src_index = raw.find(token)
        src_before = len(raw[:src_index].encode("utf-16-le")) // 2
        token_units = len(token.encode("utf-16-le")) // 2
        replacement_units = len(first_name.encode("utf-16-le")) // 2
        delta = replacement_units - token_units
        new_entities = []
        for entity in getattr(template, "entities", []):
            e = dict(entity)
            off = int(e.get("offset", 0)); length = int(e.get("length", 0)); end = off + length
            if end <= src_before:
                pass
            elif off >= src_before + token_units:
                e["offset"] = off + delta
            else:
                continue
            new_entities.append(e)
        template = type(template)(raw.replace(token, first_name, 1), new_entities)
    tail = "\n\nاز منوی پایین صفحه می‌توانید به همه‌ی امکانات ربات دسترسی داشته باشید.\n\nلطفاً یکی از گزینه‌ها را انتخاب کنید 👇"
    if isinstance(template, RichText) and template.entities:
        return RichText(str(template) + tail, template.entities)
    return str(template) + tail


def _is_admin(user_id: int) -> bool:
    # 🐛 فیکس: قبلاً فقط آیدی خود ADMIN_ID (ادمین اصلی) ادمین حساب می‌شد؛ برای همین
    # هر ادمین فرعی بعد از /start (یا دکمه‌ی تأیید عضویت) به حالت کاربر عادی برمی‌گشت
    # (متن خوشامدگویی + منوی خرید/تست رایگان) و پنل مدیریتیش دیده نمی‌شد. حالا
    # ادمین‌های فرعی هم اینجا شناخته می‌شوند تا همیشه پنل/منوی ادمینی درست به آن‌ها نشان داده شود.
    return user_id == ADMIN_ID or db.is_sub_admin(str(user_id))


async def _send_guaranteed_start_fallback(bot, chat_id: int, user_id: int) -> None:
    """🆕 فیکس نهایی برای /start: درخواست کاربر این است که /start به هر نحوی کار کند. قبلاً هر خطای پیش‌بینی‌نشده‌ای در مسیر /start باعث می‌شد کاربر هیچ پاسخی دریافت نکند. این تابع آخرین لایه‌ی دفاع است: حتی اگر هر کدام از منطق اصلی /start شکست بخورد (مثلاً دیتابیس، متن قابل‌ویرایش، کیبورد/دکمه، یا هر خطای ناشناخته‌ی دیگر)، بازهم تلاش می‌کند حداقل یک پیام ساده با کیبورد اصلی برسد، تا کاربر بتواند حداقل از دکمه‌های پایین صفحه ادامه بدهد."""
    try:
        await bot.send_message(
            chat_id,
            "🏠 خوش آمدید!\n\nاز دکمه‌های پایین صفحه می‌توانید به امکانات ربات دسترسی داشته باشید.",
            reply_markup=get_main_keyboard(user_id),
        )
        return
    except Exception:
        logger.exception("لایه‌ی اول fallback نهایی /start هم شکست خورد")
    try:
        await bot.send_message(chat_id, "🏠 خوش آمدید!")
    except Exception:
        logger.exception("حتی ساده‌ترین پیام fallback هم برای /start ارسال نشد (احتمالاً کاربر ربات را بلاک کرده است)")


def _log_unhandled_error(error_type: str, context: str) -> None:
    """ثبت خطای غیرمنتظره در جدول «لاگ خطاها» پنل ادمین تا خودِ خطا (برخلاف فیکس‌های قبلی) مانع دیده‌شدن مشکل نشود؛ هر خطای ثبت هم خودش درون try/except است تا اگر دیتابیس در دسترس نبود/خطا داد، خود این تابع هیچ‌وقت باعث شکست کلی پاسخ به /start نشود."""
    try:
        db.log_error(
            error_type=error_type,
            message="خطای غیرمنتظره در مسیر /start که قبلاً مدیریت نمی‌شد (لایه‌ی محافظتی نهایی کار کرد)",
            traceback_text=_traceback_module.format_exc(),
            context=context,
        )
    except Exception:
        logger.exception("حتی ثبت این خطا در جدول لاگ خطاها هم شکست خورد")


@router.message(Command("start"))
async def start(message: types.Message, command: CommandObject, state: FSMContext):
    """🆕 فیکس نهایی: طبق درخواست کاربر، /start باید «به هر نحو» کار کند. قبلاً اگر در هر جای منطق این تابع (حتی جاهایی که قبلاً محافظتی نداشت، مثل check_membership یا دیتابیس) خطای پیش‌بینی‌نشده‌ای رخ می‌داد، کاربر با سکوت کامل مواجه می‌شد و /start اصلاً پاسخی دریافت نمی‌کرد. حالا کل منطق داخل یک try/except قرار گرفته و هر خطای غیرمنتظره‌ای دیگر هم گرفته می‌شود؛ در بدترین حالت هم کاربر یک پیام حداقلی با کیبورد اصلی دریافت می‌کند، نه سکوت کامل."""
    try:
        await _start_impl(message, command, state)
    except Exception:
        logger.exception("خطای غیرمنتظره و مدیریت‌نشده در پردازش /start")
        _log_unhandled_error("StartCommandFailure", "start")
        await _send_guaranteed_start_fallback(message.bot, message.chat.id, message.from_user.id)


async def _start_impl(message: types.Message, command: CommandObject, state: FSMContext):
    user_id = message.from_user.id
    debug = [] if _is_admin(user_id) else None
    not_joined = await check_membership(message.bot, user_id, debug=debug)

    referrer_code = command.args.strip() if command.args else None
    # کد دعوت را تا زمان تأیید عضویت کاربر در کانال‌ها نگه می‌داریم تا رسماً
    # ثبت نشود و پاداش معرف زودتر از موعد قفل نشود.
    if referrer_code:
        await state.update_data(pending_referrer_code=referrer_code)

    if not_joined:
        # 🐛 فیکس: قبلاً اینجا فقط یک پیام متنی بدون استیکر فرستاده می‌شد، برای
        # همین وقتی که کاربر برای اولین بار /start می‌زد و هنوز عضو کانال‌ها نشده، استیکر
        # «شروع با /start» اصلاً دیده نمی‌شد (فقط بعد از تأیید عضویت در check_join). حالا
        # همین استیکر درست بالای لیست کانال‌های اجباری هم نشان داده می‌شود.
        # show_main_keyboard=False عمداً پاس داده شده چون عضویت کاربر هنوز تأیید نشده و نباید منوی
        # دائمی پایین صفحه زودتر از موعد فعال شود.
        await show_menu_with_sticker(
            message.bot, message.chat.id, "start_welcome",
            t("start_join_required"),
            reply_markup=join_channels_keyboard(not_joined),
            show_main_keyboard=False,
        )
        if debug:
            await message.answer("🔎 دیباگ عضویت (فقط ادمین می‌بیند):\n" + "\n".join(debug))
        return

    if db.is_user_blocked(user_id):
        await message.answer(t("start_blocked"))
        return

    data = await state.get_data()
    referrer_code = referrer_code or data.get("pending_referrer_code")
    existed_before = db.get_user(user_id) is not None
    user = _ensure_user(user_id, message.from_user.full_name, referrer_code)
    await state.update_data(pending_referrer_code=None)

    if not existed_before:
        await _notify_referrer_of_new_join(message.bot, user)

    if _is_admin(user_id):
        await message.answer(
            t("start_admin_welcome"),
            reply_markup=_admin_reply_kb_for(user_id),
        )
        return

    await show_menu_with_sticker(
        message.bot, message.chat.id, "start_welcome",
        _welcome_text(message.from_user.first_name), reply_markup=get_main_keyboard(message.from_user.id),
    )


@router.callback_query(F.data == "check_join")
async def check_join(callback: types.CallbackQuery, state: FSMContext):
    """🆕 فیکس نهایی: مشابه /start، طبق درخواست کاربر باید «به هر نحو» کار کند. هر خطای غیرمنتظره لاگ و در جدول لاگ خطاها ثبت می‌شود، و کاربر به‌جای سکوت کامل، یک پیام حداقلی با کیبورد اصلی دریافت می‌کند."""
    try:
        await _check_join_impl(callback, state)
    except Exception:
        logger.exception("خطای غیرمنتظره و مدیریت‌نشده در پردازش تأیید عضویت (check_join)")
        _log_unhandled_error("CheckJoinCallbackFailure", "check_join")
        try:
            await callback.answer("⚠️ خطای موقتی پیش آمد؛ دوباره تلاش کنید.", show_alert=True)
        except Exception:
            logger.exception("ارسال پاسخ سریع callback.answer هم در check_join شکست خورد")
        await _send_guaranteed_start_fallback(callback.bot, callback.message.chat.id, callback.from_user.id)


async def _check_join_impl(callback: types.CallbackQuery, state: FSMContext):
    # 🐛 دیباگ موقت: وقتی خود ادمین تست می‌کند، وضعیت/خطای دقیق هر کانال را به‌صورت پیام جدا برایش می‌فرستیم تا بدون دسترسی به لاگ سرور، دلیل رد‌شدن مشخص شود.
    debug = [] if _is_admin(callback.from_user.id) else None
    not_joined = await check_membership(callback.bot, callback.from_user.id, debug=debug)
    if not_joined:
        await callback.answer(t("start_join_not_done"), show_alert=True)
        if debug:
            await callback.message.answer("🔎 دیباگ عضویت (فقط ادمین می‌بیند):\n" + "\n".join(debug))
        return

    if db.is_user_blocked(callback.from_user.id):
        await callback.message.edit_text(t("start_blocked_short"))
        await callback.answer()
        return

    # فقط همین‌جا (بعد از تأیید واقعی عضویت) کاربر رسماً ثبت و پاداش معرف قفل می‌شود.
    data = await state.get_data()
    referrer_code = data.get("pending_referrer_code")
    existed_before = db.get_user(callback.from_user.id) is not None
    user = _ensure_user(callback.from_user.id, callback.from_user.full_name, referrer_code)
    await state.update_data(pending_referrer_code=None)

    if not existed_before:
        await _notify_referrer_of_new_join(callback.bot, user)

    if _is_admin(callback.from_user.id):
        await callback.message.edit_text("👨‍💻 به پنل مدیریت خوش آمدید! همه‌ی امکانات مدیریتی از منوی پایین صفحه قابل دسترسی است ✅")
        await callback.message.answer("منوی مدیریتی فعال شد:", reply_markup=_admin_reply_kb_for(callback.from_user.id))
    else:
        # 🆕 فیکس: این مسیر (تأیید عضویت پس از عضو کانال‌ها) مستقیماً با edit_text فرستاده می‌شد و از محافظتی که در show_menu_with_sticker اضافه شده بود عبور نمی‌کرد، پس اگر متن خوش‌آمدگویی (welcome_text) توسط ادمین طولانی ذخیره می‌شد، همینجا هم تلگرام خطای «MESSAGE_TOO_LONG» برمی‌گرداند و کاربر بعد از تأیید عضویت هم با ارور مواجه می‌شد (دقیقاً همان اروری که گزارش شد). حالا اگر این خطا رخ بدهد، متن کوتاه‌شده دوباره فرستاده می‌شود.
        welcome_text = _welcome_text(callback.from_user.first_name)
        try:
            await callback.message.edit_text(welcome_text)
        except TelegramBadRequest as e:
            if is_message_too_long_error(e):
                logger.error("متن خوش‌آمدگویی (پیش‌نمایش %d کاراکتر) در check_join از سقف تلگرام بیشتر بود؛ کوتاه شد و دوباره فرستاده شد.", len(welcome_text))
                await callback.message.edit_text(truncate_for_telegram(welcome_text))
            else:
                raise
        await show_menu_with_sticker(
            callback.bot, callback.message.chat.id, "join_confirmed",
            t("start_join_confirmed"), reply_markup=get_main_keyboard(callback.from_user.id),
        )
    await callback.answer()


@router.callback_query(F.data == "back")
async def go_back(callback: types.CallbackQuery):
    """بازگشت از زیرمنوهای اینلاین؛ دیگر منوی اصلی اینلاین دوباره ارسال نمی‌شود؛
    تمام مسیرها از طریق همین منوی دائمی پایین صفحه در دسترس است.

    🐛 فیکس: قبلاً اینجا فقط متن پیام فعلی ویرایش می‌شد، پس اگر بالای همان منو یک
    استیکر وجود داشت، روی صفحه باقی می‌ماند. حالا از show_menu_with_sticker استفاده
    می‌شود تا همزمان با بستن منو، استیکرش هم حذف شود و منوی دائمی پایین صفحه
    هم دوباره تازه/فعال شود."""
    if _is_admin(callback.from_user.id):
        await callback.message.edit_text(t("start_back_admin"))
    else:
        # ✅ تنها مسیر مجاز برای بازگرداندن منوی دائمی پایین صفحه پس از مخفی‌شدن موقت (بعد از تحویل سرویس): همین‌جا صریحاً flag را پاک می‌کنیم و بدون وابستگی به get_main_keyboard مستقیماً main_reply_keyboard() را می‌فرستیم.
        try:
            db.set_keyboard_hidden(callback.from_user.id, False)
        except Exception:
            logging.getLogger(__name__).exception("خطا در پاک‌کردن وضعیت مخفی‌بودن منوی پایین صفحه")
        await show_menu_with_sticker(
            callback.bot, callback.message.chat.id, None,
            t("start_back_user"),
            reply_markup=main_reply_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "free_test_soon")
async def free_test_soon(callback: types.CallbackQuery):
    await callback.answer(t("free_test_soon"), show_alert=True)

