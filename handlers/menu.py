"""
handlers/menu.py
هندلرهای منوی پایین صفحه (Reply Keyboard) که همیشه در دسترس کاربر/ادمین است.

این روتر باید قبل از همه‌ی روترهای دیگر در bot.py ثبت شود تا دکمه‌های این
منو در هر حالتی (حتی وسط یک مکالمه‌ی FSM مثل ارسال رسید یا تیکت) همیشه کار
کنند و کاربر هیچ‌وقت در یک مرحله گیر نکند.
"""

import logging
import unicodedata
from aiogram import Router, F, types
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile

import database as db
from text_catalog import text as t
import crypto
from subscription import is_config_expired
from utils import show_menu_with_sticker, get_main_keyboard
from states import UserStates, AdminStates
import bot_info
from config import ADMIN_ID, PLANS_INTRO_TEXT, DATABASE_PATH, REFERRAL_LOCK_AMOUNT, REFERRAL_MIN_VOLUME_GB
from keyboards import (
    plans_menu,
    my_configs_menu,
    wallet_menu,
    profile_menu,
    referral_menu,
    support_menu,
    back_button,
    admin_panel_menu,
    admin_back_button,
    admin_discount_menu,
    admin_userlist_menu,
    purchase_payment_keyboard,
    main_reply_keyboard,
    admin_reply_keyboard,
    admin_referrers_page_keyboard,
    user_guides_menu,
    user_guide_detail_keyboard,
    admin_guides_menu,
)

router = Router(name="menu")


class _MenuButtonText(BaseFilter):
    """فیلتر دکمه‌های ثابت منوی پایین صفحه، بر اساس کلید متن قابل‌ویرایش در «📝 مدیریت متن‌های کاربر».

    🐛 فیکس دکمه‌هایی که بعد از تغییر متن از پنل ادمین از کار می‌افتادند:
    قبلاً این دکمه‌ها با F.text.in_([متن پیش‌فرض, t(key)]) فیلتر می‌شدند. چون
    t(key) فقط همان یک‌بار، لحظه‌ی import شدن این فایل (یعنی موقع بالا آمدن
    ربات)، مقدار متن را از دیتابیس می‌خواند و در یک لیست ثابت ذخیره می‌کرد،
    اگر ادمین بعداً متن دکمه را از پنل عوض می‌کرد، این فیلتر همچنان دنبال متن
    قدیمی (همان لحظه‌ی بالا آمدن ربات) می‌گشت؛ متن جدیدِ نمایش داده‌شده روی
    دکمه هیچ‌وقت با آن مچ نمی‌شد و در نتیجه با فشردن دکمه هیچ اتفاقی نمی‌افتاد.
    این فیلتر به‌جای لیست ثابت، هر بار مستقیماً متن فعلی را از دیتابیس می‌خواند؛
    بنابراین با هر تغییر متن، بدون نیاز به ری‌استارت ربات، بلافاصله کار می‌کند.
    """

    def __init__(self, key: str, default: str):
        self.key = key
        self.default = default

    @staticmethod
    def _normalize(value: str) -> str:
        # متن دکمه از سمت Telegram به‌صورت Unicode معمولی برمی‌گردد؛
        # NFC/NFKC جلوی اختلاف‌های بی‌خطر Unicode (مثلاً شکل‌های ترکیبی) را می‌گیرد،
        # بدون اینکه کاراکترهای Custom Emoji را حذف کند.
        return unicodedata.normalize("NFC", value).strip()

    async def __call__(self, message: types.Message) -> bool:
        if not message.text:
            return False
        current = db.get_text_override(self.key, self.default)
        incoming = self._normalize(message.text)
        return incoming in {self._normalize(self.default), self._normalize(current)}


def _current_admin_permissions(user_id: int) -> set[str] | None:
    if user_id == ADMIN_ID:
        return None
    adm = db.get_sub_admin(str(user_id)) or {}
    return set(adm.get("permissions") or [])

def _admin_panel_kb_for(user_id: int):
    return admin_panel_menu(db.is_orders_enabled(), permissions=_current_admin_permissions(user_id), is_main_admin=(user_id == ADMIN_ID))


def _is_admin(user_id: int) -> bool:
    # 🐛 فیکس: قبلاً فقط ADMIN_ID ادمین حساب می‌شد و همه‌ی دکمه‌های این فایل برای
    # ادمین‌های فرعی بی‌اثر بود (کلیک می‌کردند و هیچ پاسخی نمی‌آمد). الان ادمین‌های
    # فرعی هم شناخته می‌شوند (بررسی دسترسی دقیق هر بخش با _admin_perm انجام می‌شود).
    return user_id == ADMIN_ID or db.is_sub_admin(str(user_id))


def _admin_perm(user_id: int, permission: str) -> bool:
    # بررسی دسترسی دقیق به یک قابلیت مشخص برای ادمین فرعی (ادمین اصلی همیشه مجاز است).
    return user_id == ADMIN_ID or db.sub_admin_has_permission(str(user_id), permission)


# ---------------------------------------------------------------------------
# منوی کاربر عادی
# ---------------------------------------------------------------------------
@router.message(_MenuButtonText("main_buy", "🛒 خرید اشتراک"))
async def menu_plans(message: types.Message, state: FSMContext):
    await state.clear()
    if not db.is_orders_enabled():
        await message.answer(
            "🔴 ربات به دلیل حجم سفارشات بالا موقتاً بسته می‌باشد.\n\nروشن شدن دوباره‌ی آن اطلاع‌رسانی خواهد شد."
        )
        return
    # 🧪 تست: استیکر service.webm درست بالای منوی خرید اشتراک
    await show_menu_with_sticker(
        message.bot, message.chat.id, "buy_plans",
        PLANS_INTRO_TEXT, reply_markup=plans_menu(), parse_mode="Markdown",
    )
    
    
@router.message(_MenuButtonText("main_free_test", "🎁 تست رایگان"))
async def menu_free_test(message: types.Message, state: FSMContext):
    from config import FREE_TEST_PLAN_KEY

    if not db.is_orders_enabled():
        await message.answer(t("orders_closed"))
        return

    plan = db.get_effective_plan(FREE_TEST_PLAN_KEY)
    user = db.get_user(message.from_user.id)
    if user is None:
        await message.answer(t("common_start_required"))
        return

    await state.clear()

    # 🎁 اگر ادمین قیمت پلن «تست رایگان» را صفر گذاشته باشد واقعاً رایگان است)،
    # هیچ صفحه‌ای انتخاب روش پرداخت نشان داده نمی‌شود و سرویس همینجا مستقیماً ساخته/ارسال می‌شود.
    if plan["price"] == 0:
        from handlers.plans import fulfill_free_test_directly

        await fulfill_free_test_directly(message.bot, message, user, plan, FREE_TEST_PLAN_KEY)
        return

    price_label = f"{plan['price']:,} تومان"
    text = (
        f"🎁 {plan['name']}\n💰 قیمت: {price_label}\n"
        f"👛 موجودی کیف پول شما: {user['wallet']:,} تومان\n\nروش پرداخت را انتخاب کنید:"
    )
    # 🧪 تست: استیکر test.webm درست بالای منوی تست رایگان
    await show_menu_with_sticker(
        message.bot, message.chat.id, "free_test",
        text, reply_markup=purchase_payment_keyboard(FREE_TEST_PLAN_KEY, show_discount=True),
    )


@router.message(_MenuButtonText("main_configs", "📱 سرویس‌های من"))
async def menu_my_configs(message: types.Message, state: FSMContext):
    await state.clear()
    user = db.get_user(message.from_user.id)
    if user is None:
        await message.answer(t("common_start_required"))
        return

    configs = [c for c in db.get_configs(user["id"]) if not is_config_expired(c)]
    if not configs:
        await show_menu_with_sticker(
            message.bot, message.chat.id, "my_configs_empty",
            "📱 شما هنوز هیچ سرویسی خریداری نکرده‌اید.\n\nبرای خرید، از «🛒 خرید اشتراک» اقدام کنید.",
            reply_markup=back_button("back", "🏠 بازگشت به منوی اصلی"),
        )
    else:
        await show_menu_with_sticker(
            message.bot, message.chat.id, "my_configs_has",
            "📱 سرویس‌های شما\n\nکدوم دسته رو می‌خوای ببینی؟ 👇",
            reply_markup=my_configs_menu(),
        )


@router.message(_MenuButtonText("main_wallet", "💰 کیف پول"))
async def menu_wallet(message: types.Message, state: FSMContext):
    await state.clear()
    user = db.get_user(message.from_user.id)
    if user is None:
        await message.answer(t("common_start_required"))
        return

    text = t("wallet_overview", wallet=user["wallet"], locked=user["locked_wallet"], min_gb=REFERRAL_MIN_VOLUME_GB)
    await show_menu_with_sticker(message.bot, message.chat.id, "wallet", text, reply_markup=wallet_menu())


@router.message(_MenuButtonText("main_referral", "👥 دعوت دوستان"))
async def menu_referral(message: types.Message, state: FSMContext):
    await state.clear()
    user = db.get_user(message.from_user.id)
    if user is None:
        await message.answer(t("common_start_required"))
        return

    stats = db.get_referral_stats(user["id"])
    invite_link = f"https://t.me/{bot_info.get('bot_username')}?start={stats['invite_code']}"

    text = t("referral_overview", reward=REFERRAL_LOCK_AMOUNT, min_gb=REFERRAL_MIN_VOLUME_GB, invite_link=invite_link, invite_code=stats["invite_code"], invited_count=stats["invited_count"], successful_invites=stats["successful_invites"], released=stats["released_amount"], locked=user["locked_wallet"])
    await show_menu_with_sticker(message.bot, message.chat.id, "referral", text, reply_markup=referral_menu())


@router.message(_MenuButtonText("main_profile", "👤 پروفایل من"))
async def menu_profile(message: types.Message, state: FSMContext):
    await state.clear()
    user = db.get_user(message.from_user.id)
    if user is None:
        await message.answer(t("common_start_required"))
        return

    configs_count = len(db.get_configs(user["id"]))

    text = t("profile_overview", name=user["name"], telegram_id=user["telegram_id"], wallet=user["wallet"], locked=user["locked_wallet"], configs_count=configs_count, total_purchase=user["total_purchase"], joined=user["joined"], invited_count=user["invited_count"], successful_invites=user["successful_invites"])
    await show_menu_with_sticker(message.bot, message.chat.id, "profile", text, reply_markup=profile_menu())


@router.message(_MenuButtonText("main_guides", "📚 راهنما"))
async def menu_user_guides(message: types.Message, state: FSMContext):
    await state.clear()
    guides = db.get_guides()
    if not guides:
        await show_menu_with_sticker(
            message.bot, message.chat.id, "guides_empty",
            t("guides_empty"),
            reply_markup=back_button("back", "🏠 بازگشت به منوی اصلی"),
        )
        return
    await show_menu_with_sticker(
        message.bot, message.chat.id, "guides_has",
        t("guides_intro"),
        reply_markup=user_guides_menu(guides),
    )


@router.callback_query(F.data == "user_guides")
async def menu_user_guides_callback(callback: types.CallbackQuery):
    guides = db.get_guides()
    if not guides:
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "guides_empty", 
            "📚 راهنما و اموزش‌ها\n\nهنوز هیچ راهنمایی ثبت نشده.", reply_markup=back_button("back", "🏠 بازگشت به منوی اصلی")
        )
        await callback.answer()
        return
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "guides_has", 
        t("guides_intro"),
        reply_markup=user_guides_menu(guides),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("guideopen_"))
async def menu_user_guide_open(callback: types.CallbackQuery):
    guide_id = int(callback.data.replace("guideopen_", ""))
    guide = db.get_guide(guide_id)
    if guide is None:
        await callback.answer(t("guide_missing"), show_alert=True)
        return

    caption = f"📚 {guide['title']}"
    if guide.get("body_text"):
        caption += f"\n\n{guide['body_text']}"

    try:
        if guide["content_type"] == "photo" and guide.get("file_id"):
            await callback.message.answer_photo(guide["file_id"], caption=caption, reply_markup=user_guide_detail_keyboard())
        elif guide["content_type"] == "video" and guide.get("file_id"):
            await callback.message.answer_video(guide["file_id"], caption=caption, reply_markup=user_guide_detail_keyboard())
        else:
            await callback.message.answer(caption, reply_markup=user_guide_detail_keyboard())
    except Exception:
        await callback.message.answer(caption, reply_markup=user_guide_detail_keyboard())
    await callback.answer()


@router.message(_MenuButtonText("main_support", "👨‍💻 پشتیبانی"))
async def menu_ticket(message: types.Message, state: FSMContext):
    await state.clear()
    try:
        await show_menu_with_sticker(
            message.bot, message.chat.id, "support",
            t("support_intro"),
            reply_markup=support_menu(),
        )
    except Exception:
        logging.getLogger(__name__).exception("خطا در نمایش منوی پشتیبانی")
        await message.answer(
            t("support_error"),
            reply_markup=get_main_keyboard(message.from_user.id),
        )


@router.message(_MenuButtonText("main_agency", "🤝 درخواست نمایندگی"))
async def menu_agency_request_start(message: types.Message, state: FSMContext):
    await state.clear()
    await show_menu_with_sticker(
        message.bot, message.chat.id, "agency_request",
        t("agency_intro"),
        reply_markup=back_button("back", "🔙 انصراف"),
    )
    await state.set_state(UserStates.waiting_agency_request_message)


@router.message(UserStates.waiting_agency_request_message)
async def menu_agency_request_send(message: types.Message, state: FSMContext):
    uid = str(message.from_user.id)
    text = (message.text or "").strip()
    if not text:
        await message.answer(t("agency_invalid"))
        return

    await message.bot.send_message(
        ADMIN_ID,
        f"🤝 درخواست نمایندگی جدید\n👤 {message.from_user.full_name}\n🆔 {uid}\n\n💬 {text}",
    )
    await message.answer(
        t("agency_sent"),
        reply_markup=get_main_keyboard(message.from_user.id),
    )
    await state.clear()


# ---------------------------------------------------------------------------
# منوی ادمین
# ---------------------------------------------------------------------------
@router.message(F.text == "📊 آمار")
async def menu_admin_stats(message: types.Message, state: FSMContext):
    if not _admin_perm(message.from_user.id, "stats"):
        return
    await state.clear()
    text = (
        f"📊 آمار ربات\n\n"
        f"💰 فروش امروز: {db.sales_since(1):,} تومان\n"
        f"💰 فروش هفته: {db.sales_since(7):,} تومان\n"
        f"💰 فروش ماه: {db.sales_since(30):,} تومان\n"
        f"💰 کل فروش: {db.total_sales():,} تومان\n\n"
        f"👥 تعداد کاربران: {db.count_users()}\n"
        f"🟢 کاربران فعال (۳۰ روز اخیر): {db.count_active_users(30)}"
    )
    await message.answer(text, reply_markup=admin_back_button())


@router.message(F.text == "👥 لیست کاربران")
async def menu_admin_userlist(message: types.Message, state: FSMContext):
    if not _admin_perm(message.from_user.id, "users"):
        return
    await state.clear()
    text = (
        f"👥 مدیریت کاربران\n\n"
        f"👥 کل کاربران ثبت‌نامی: {db.count_users()}\n"
        f"🟢 مشتریانی که خرید داشته‌اند: {db.count_customers()}\n\n"
        f"یکی از گزینه‌های زیر را انتخاب کنید 👇"
    )
    await message.answer(text, reply_markup=admin_userlist_menu())


@router.message(F.text.in_({"🔍 جستجوی کاربر", "🔍 جستجوی حرفه‌ای"}))
async def menu_admin_search(message: types.Message, state: FSMContext):
    if not _admin_perm(message.from_user.id, "users"):
        return
    await message.answer("🔍 آیدی عددی یا کد دعوت کاربر را ارسال کنید:", reply_markup=admin_back_button())
    await state.set_state(AdminStates.waiting_search_user)


@router.message(F.text == "📢 پیام همگانی")
async def menu_admin_broadcast(message: types.Message, state: FSMContext):
    if not _admin_perm(message.from_user.id, "broadcast"):
        return
    await message.answer(
        "📢 پیامی که می‌خواهید برای همه کاربران ارسال شود را بنویسید:",
        reply_markup=admin_back_button(),
    )
    await state.set_state(UserStates.waiting_broadcast)


@router.message(F.text == "🎟 مدیریت تخفیف")
async def menu_admin_discount(message: types.Message, state: FSMContext):
    if not _admin_perm(message.from_user.id, "discounts"):
        return
    await state.clear()
    discounts = db.get_all_discounts()
    if not discounts:
        text = "🎟 هیچ کد تخفیفی هنوز ثبت نشده.\n\nبرای ساخت کد جدید، دکمه‌ی زیر را بزنید 👇"
    else:
        text = "🎟 کدهای تخفیف فعال:\n\nبرای مشاهده و ویرایش جزئیات هر کد، روی آن بزنید 👇"
    await message.answer(text, reply_markup=admin_discount_menu(discounts))


REFERRERS_PER_PAGE = 10


@router.message(F.text == "🤝 مدیریت دعوت‌ها")
async def menu_admin_referrals(message: types.Message, state: FSMContext):
    if not _admin_perm(message.from_user.id, "referrals"):
        return
    await state.clear()
    total = db.count_referrers()
    if total == 0:
        text = "🤝 هنوز هیچ دعوتی ثبت نشده."
    else:
        text = (
            f"🤝 مدیریت دعوت‌شده‌ها — مرتب‌شده بر اساس بیشترین دعوت\n\n"
            f"👥 تعداد کل دعوت‌کننده‌ها: {total}\n\n"
            f"روی هر کدام بزنید تا لیست دعوت‌شده‌هایش و وضعیت کیف پولش رو ببینید 👇"
        )
    users = db.get_referrers_page(0, REFERRERS_PER_PAGE)
    has_next = total > REFERRERS_PER_PAGE
    await message.answer(text, reply_markup=admin_referrers_page_keyboard(users, 0, has_next))


@router.message(F.text == "📚 مدیریت راهنما")
async def menu_admin_guides(message: types.Message, state: FSMContext):
    if not _admin_perm(message.from_user.id, "guides"):
        return
    await state.clear()
    guides = db.get_guides()
    await message.answer(
        f"📚 مدیریت راهنما و اموزش‌ها\n\nتعداد: {len(guides)}\n\n"
        "از اینجا می‌تونید راهنما/آموزش جدید اضافه کنید یا موردهای موجود را ویرایش کنید:",
        reply_markup=admin_guides_menu(guides),
    )


@router.message(F.text == "🧭 همه‌ی بخش‌ها")
async def menu_admin_all_sections(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        "🧭 همه‌ی بخش‌های مدیریتی 👇",
        reply_markup=_admin_panel_kb_for(message.from_user.id),
    )


@router.message(F.text == "💾 بکاپ")
async def menu_admin_backup(message: types.Message, state: FSMContext):
    if not _admin_perm(message.from_user.id, "backup"):
        return
    await state.clear()
    try:
        if db.USE_TURSO:
            export_path = "/tmp/backup_export.json"
            db.export_backup_json(export_path)
            backup_file = FSInputFile(export_path, filename="backup.json")
            caption = "💾 بکاپ دیتابیس (Turso — JSON)"
        else:
            backup_file = FSInputFile(DATABASE_PATH)
            caption = "💾 بکاپ دیتابیس"
        await message.answer_document(backup_file, caption=caption)
    except Exception:
        await message.answer("❌ خطا در ساخت بکاپ.")


