"""
handlers/plans.py
نمایش دسته‌بندی سرویس‌های VIP، اعمال کد تخفیف، خرید سرویس
(با دو روش پرداخت: کیف پول و کارت‌به‌کارت)، و نمایش سرویس‌های خریداری‌شده کاربر.

نکته مهم: بعد از یک خرید موفق (چه با کیف پول چه با کارت‌به‌کارت)، اگر حجم آن
پلن حداقل REFERRAL_MIN_VOLUME_GB گیگ باشد، db.complete_referral فراخوانی
می‌شود تا اگر معرفی داشته، مبلغ قفل‌شده‌ی معرفش آزاد شود (تست رایگان و
پلن‌های زیر این حجم پاداش را آزاد نمی‌کنند).
"""

import html
import json
import logging
import re
from datetime import datetime, timedelta

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext

import database as db
from text_catalog import text as t
import crypto
import vpn_panel
import uniquepay
import payments
import alerts
from subscription import fetch_subscription_info, extract_configs, format_bytes, format_expire, usage_bar, days_remaining, is_config_expired
from utils import send_admin_task_message, forward_admin_task_message, parse_int_in_range, is_duplicate_action, format_deadline_time, progress_bar, now_tehran_naive, show_menu_with_sticker, get_main_keyboard
from states import UserStates
from handlers.marzban_admin import auto_fulfill_vip_via_marzban, auto_fulfill_custom_via_marzban
import bot_info


def _render_card_invoice_text(key: str, default: str, values: dict, deadline_str: str):
    """متن فاکتور قابل شخصی‌سازی را همراه با Telegram MessageEntityها رندر می‌کند.
    در صورت استفاده‌ی ادمین از Premium/Custom Emoji، entityها حفظ می‌شوند و جمله‌ی
    انقضا که توسط سیستم اضافه می‌شود، بعد از متن قرار می‌گیرد. برای جلوگیری از
    تداخل parse mode، شماره کارت و نام صاحب کارت به‌صورت متن ساده وارد می‌شوند."""
    from text_catalog import text as rich_text
    safe_values = dict(values)
    for key_name in ("card_number", "card_holder"):
        if key_name in safe_values:
            import re as _re
            safe_values[key_name] = _re.sub(r"</?[^>]+>", "", str(safe_values[key_name]))
    body = rich_text(key, default, **safe_values)
    expiry = (
        f"⏱ این شماره کارت و قیمت تا ساعت {deadline_str} (۳۰ دقیقه) معتبر است. "
        "لطفاً تا این ساعت رسید پرداخت را ارسال کنید، وگرنه این فاکتور به‌طور خودکار منقضی و حذف می‌شود."
    )
    return body + "\n\n" + expiry


PLAN_CARD_INVOICE_DEFAULT = (
    "🟩🟩⬜️ مرحله 2 از 3\n\n"
    "💳 پرداخت کارت به کارت\n\n"
    "🛒 {plan_name}\n"
    "💰 مبلغ قابل پرداخت: {amount:,} تومان\n\n"
    "💳 شماره کارت:\n{card_number}\n\n"
    "👤 به نام: {card_holder}\n\n"
    "📸 پس از واریز، عکس رسید پرداخت را همینجا ارسال کنید."
)


from config import (
    ADMIN_ID,
    PLANS_INTRO_TEXT,
    REFERRAL_MIN_VOLUME_GB,
    UNIQUEPAY_ENABLED,
    FREE_TEST_PLAN_KEY,
)
from keyboards import (
    main_reply_keyboard,
    plans_menu,
    vip_categories_keyboard,
    vip_category_plans_keyboard,
    purchase_payment_keyboard,
    insufficient_balance_keyboard,
    back_button,
    admin_purchase_notify_keyboard,
    admin_purchase_card_approval_keyboard,
    my_configs_menu,
    my_configs_list_keyboard,
    config_detail_keyboard,
    confirm_delete_config_keyboard,
    confirm_disable_service_keyboard,
    confirm_revoke_sub_keyboard,
    online_payment_keyboard,
    custom_build_payment_keyboard, custom_build_cancel_keyboard,
    admin_custom_order_card_approval_keyboard, admin_custom_order_notify_keyboard,
)

logger = logging.getLogger(__name__)

ORDERS_CLOSED_TEXT = (
    "🔴 ربات به دلیل حجم سفارشات بالا موقتاً بسته می‌باشد.\n\nروشن شدن دوباره‌ی آن اطلاع‌رسانی خواهد شد."
)

plan_type = db.plan_type  # نسخه‌ی DB-aware (دسته‌بندی‌های VIP را هم می‌شناسد)


router = Router(name="plans")




@router.callback_query(F.data == "noop")
async def noop(callback: types.CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "plans")
async def show_services(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if not db.is_orders_enabled():
        await callback.answer(ORDERS_CLOSED_TEXT, show_alert=True)
        return
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "buy_plans", t("plans_intro"), reply_markup=plans_menu(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "plans_vip")
async def show_vip_plans(callback: types.CallbackQuery, state: FSMContext):
    # 🧪 تست: استیکر plan.webm درست بالای منوی دسته‌های VIP
    await show_menu_with_sticker(
        callback.bot, callback.message.chat.id, "plan_select",
        t("vip_intro"), reply_markup=vip_categories_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("vipcat_"))
async def show_vip_category_plans(callback: types.CallbackQuery, state: FSMContext):
    category_key = callback.data.replace("vipcat_", "")
    cat = db.get_vip_category(category_key)
    if cat is None:
        await callback.answer(t("category_not_found"), show_alert=True)
        return
    data = await state.get_data()
    discount = data.get("discount_percent", 0)
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "vip_category_list", 
        f"🚀 {cat['name']}:", reply_markup=vip_category_plans_keyboard(category_key, discount)
    )
    await callback.answer()






def _compute_final_price(plan_key: str, plan: dict, telegram_id, data: dict) -> tuple[int, str, str | None]:
    """قیمت نهایی یک پلن را با درنظرگرفتن کد تخفیف کاربر (اگر برای این پلن معتبر باشد)
    و تخفیف خودکار نمایندگی (فقط روی VIP) محاسبه می‌کند و بهترین (کمترین) قیمت را برمی‌گرداند.
    خروجی سوم، کد تخفیفی است که واقعاً «برنده» شده (باید مصرفش ثبت شود) یا None اگر
    تخفیف نمایندگی برنده شده باشد یا هیچ تخفیفی اعمال نشده باشد."""
    price = plan["price"]

    code_price = price
    code = data.get("discount_code")
    valid_code = False
    if code:
        discount = db.get_discount(code)
        if (
            discount
            and discount["uses"] > 0
            and not db.discount_is_expired(discount)
            and db.discount_applies_to_plan(discount, plan_key)
            and db.discount_allowed_for_user(discount, telegram_id)
        ):
            user = db.get_user(telegram_id)
            over_cap = (
                discount.get("max_uses_per_user")
                and user is not None
                and db.user_discount_uses(discount["id"], user["id"]) >= discount["max_uses_per_user"]
            )
            under_min = discount.get("min_order_amount") and price < discount["min_order_amount"]
            if not over_cap and not under_min:
                code_price = db.compute_discount(discount, price)
                valid_code = True

    agent_price = price
    if plan_type(plan_key) == "vip":
        agent = db.get_agent(telegram_id)
        if agent:
            agent_price = int(round(price * (1 - agent["vip_discount_percent"] / 100)))

    final_price = min(code_price, agent_price)
    note = ""
    winning_code = None
    if final_price < price:
        if valid_code and code_price <= agent_price:
            note = " (کد تخفیف اعمال شد)"
            winning_code = code
        else:
            note = " (تخفیف نمایندگی اعمال شد)"
    return final_price, note, winning_code


# ---------------------------------------------------------------------------
# کد تخفیف عمومی (از طریق کیف پول وارد می‌شود و روی خرید بعدی اعمال می‌شود)
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "use_discount")
async def use_discount(callback: types.CallbackQuery, state: FSMContext):
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "discount_code_entry", t("discount_prompt"), reply_markup=back_button("wallet", t("discount_cancel")))
    await state.set_state(UserStates.waiting_discount_code)
    await callback.answer()


@router.message(UserStates.waiting_discount_code)
async def check_discount(message: types.Message, state: FSMContext):
    code = message.text.strip().upper()
    discount = db.get_discount(code)

    if discount is None or discount["uses"] <= 0 or db.discount_is_expired(discount):
        await message.answer(
            t("discount_invalid"),
            reply_markup=back_button("plans"),
        )
        await state.clear()
        return

    if not db.discount_allowed_for_user(discount, message.from_user.id):
        await message.answer(
            t("discount_forbidden"),
            reply_markup=back_button("plans"),
        )
        await state.clear()
        return

    if discount.get("max_uses_per_user"):
        user = db.get_user(message.from_user.id)
        if user and db.user_discount_uses(discount["id"], user["id"]) >= discount["max_uses_per_user"]:
            await message.answer(
                t("discount_limit"),
                reply_markup=back_button("plans"),
            )
            await state.clear()
            return

    if discount.get("discount_type") == "amount":
        await message.answer(
            t("discount_fixed_note"),
            reply_markup=plans_menu(),
        )
        await state.clear()
        return

    await state.update_data(discount_code=code, discount_percent=discount["percent"])
    plans_note = "" if not db.discount_is_expired(discount) and not db.discount_plans(discount) else \
        " (فقط روی پلن‌های خاص قابل استفاده است)"
    await message.answer(
        f"✅ کد تخفیف {discount['percent']}٪ با موفقیت ثبت شد و در خرید بعدی شما (در صورت تطابق پلن) اعمال می‌شود.{plans_note}",
        reply_markup=plans_menu(),
    )
    await state.set_state(None)


# ---------------------------------------------------------------------------
# کد تخفیف اختصاصیِ یک پلن (در مرحله‌ی پرداخت)
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("discount_plan_"))
async def discount_for_plan(callback: types.CallbackQuery, state: FSMContext):
    plan_key = callback.data.replace("discount_plan_", "")
    if db.get_effective_plan(plan_key) is None:
        await callback.answer(t("plan_not_found"), show_alert=True)
        return
    await state.update_data(discount_target_plan=plan_key)
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "discount_code_entry", t("discount_prompt"))
    await state.set_state(UserStates.waiting_discount_plan)
    await callback.answer()


@router.message(UserStates.waiting_discount_plan)
async def check_discount_for_plan(message: types.Message, state: FSMContext):
    code = message.text.strip().upper()
    discount = db.get_discount(code)
    data = await state.get_data()
    plan_key = data.get("discount_target_plan")
    plan = db.get_effective_plan(plan_key)

    if plan is None:
        await message.answer(t("plan_action_error"), reply_markup=back_button("plans"))
        await state.clear()
        return

    if discount is None or discount["uses"] <= 0 or db.discount_is_expired(discount):
        await message.answer(
            t("discount_invalid"),
            reply_markup=purchase_payment_keyboard(plan_key, show_discount=True),
        )
        await state.set_state(None)
        return

    if not db.discount_applies_to_plan(discount, plan_key):
        await message.answer(
            "❌ این کد تخفیف روی این پلن قابل استفاده نیست.",
            reply_markup=purchase_payment_keyboard(plan_key, show_discount=True),
        )
        await state.set_state(None)
        return

    if not db.discount_allowed_for_user(discount, message.from_user.id):
        await message.answer(
            t("discount_forbidden"),
            reply_markup=purchase_payment_keyboard(plan_key, show_discount=True),
        )
        await state.set_state(None)
        return

    if discount.get("max_uses_per_user"):
        user = db.get_user(message.from_user.id)
        if user and db.user_discount_uses(discount["id"], user["id"]) >= discount["max_uses_per_user"]:
            await message.answer(
                t("discount_limit"),
                reply_markup=purchase_payment_keyboard(plan_key, show_discount=True),
            )
            await state.set_state(None)
            return

    if discount.get("min_order_amount") and plan["price"] < discount["min_order_amount"]:
        await message.answer(
            f"❌ این کد فقط برای خریدهای بالای {discount['min_order_amount']:,} تومان قابل استفاده است.",
            reply_markup=purchase_payment_keyboard(plan_key, show_discount=True),
        )
        await state.set_state(None)
        return

    await state.update_data(discount_code=code, discount_percent=discount["percent"])
    final_price = db.compute_discount(discount, plan["price"])
    value_text = f"{discount['percent']}٪" if discount.get("discount_type") != "amount" else f"{discount['amount']:,} تومانی"
    text = (
        f"✅ کد تخفیف {value_text} اعمال شد!\n\n"
        f"🛒 {plan['name']}\n💰 قیمت نهایی: {final_price:,} تومان\n\n"
        f"روش پرداخت را انتخاب کنید:"
    )
    await message.answer(text, reply_markup=purchase_payment_keyboard(plan_key, show_discount=False))
    await state.set_state(None)


# ---------------------------------------------------------------------------
# انتخاب پلن → نمایش روش‌های پرداخت
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("buy_"))
async def buy_plan(callback: types.CallbackQuery, state: FSMContext):
    if not db.is_orders_enabled():
        await callback.answer(ORDERS_CLOSED_TEXT, show_alert=True)
        return

    plan_key = callback.data.replace("buy_", "")
    plan = db.get_effective_plan(plan_key)
    if plan is None:
        await callback.answer(t("plan_not_found"), show_alert=True)
        return

    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return

    if plan_key == FREE_TEST_PLAN_KEY and db.has_used_free_test(user["id"]):
        await callback.answer(
            t("free_test_used"),
            show_alert=True,
        )
        return

    data = await state.get_data()
    final_price, note, _winning_code = _compute_final_price(plan_key, plan, callback.from_user.id, data)

    text = progress_bar(1, 3) + t("plan_payment_page", plan_name=plan["name"], price=final_price, note=note, wallet=user["wallet"])

    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "plan_payment_method", text, reply_markup=purchase_payment_keyboard(plan_key, show_discount=not note))
    await callback.answer()


# ---------------------------------------------------------------------------
# پرداخت از کیف پول
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("pay_wallet_"))
async def pay_with_wallet(callback: types.CallbackQuery, state: FSMContext):
    plan_key = callback.data.replace("pay_wallet_", "")
    if is_duplicate_action(f"walletbuy_{callback.from_user.id}_{plan_key}"):
        await callback.answer(t("processing_request"), show_alert=True)
        return

    plan = db.get_effective_plan(plan_key)
    if plan is None:
        await callback.answer(t("plan_not_found"), show_alert=True)
        return

    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return

    if plan_key == FREE_TEST_PLAN_KEY and db.has_used_free_test(user["id"]):
        await callback.answer(
            t("free_test_used"),
            show_alert=True,
        )
        return

    data = await state.get_data()
    final_price, _note, winning_code = _compute_final_price(plan_key, plan, callback.from_user.id, data)

    if user["wallet"] < final_price:
        needed = final_price - user["wallet"]
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "plan_pay_wallet", 
            t("wallet_insufficient", price=final_price, wallet=user["wallet"], needed=needed),
            reply_markup=insufficient_balance_keyboard(),
        )
        await callback.answer()
        return

    success = db.deduct_from_wallet(user["id"], final_price, f"خرید {plan['name']}")
    if not success:
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "plan_pay_wallet", 
            t("wallet_not_enough"),
            reply_markup=insufficient_balance_keyboard(),
        )
        await callback.answer()
        return

    if winning_code:
        db.use_discount(winning_code, user["id"])

    if plan.get("volume_gb", 0) >= REFERRAL_MIN_VOLUME_GB:
        try:
            db.complete_referral(user["id"])
        except ValueError:
            pass

    order_id = db.create_order(user["id"], plan_key, plan["name"], plan_type(plan_key), final_price)

    # 🐛 فیکس: پیام «پرداخت شما ثبت شد» را زودتر از ارسال خودکار سرویس می‌فرستیم تا کاربر قبل از دریافت سرویس، این پیام را ببیند.
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "plan_pay_wallet", 
        t("wallet_purchase_success"),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[]),
    )
    await state.update_data(discount_percent=0, discount_code="")

    # VIP و «تست رایگان»: اگر پنل متصل و برای این پلن (یا برای تست رایگان،
    # نگاشت سراسری‌اش) بسته‌ای نگاشت شده باشد، همین‌جا و بدون نیاز به هیچ
    # انتخابی از ادمین، سرویس ساخته و مستقیم ارسال می‌شود.  هرگز وارد
    # این مسیر نمی‌شود (auto_fulfill_vip_via_marzban فقط روی
    # get_marzban_plan_map_for_plan_key کار می‌کند که خودش  را نادیده
    # می‌گیرد)، و اگر نگاشتی نباشد هم دقیقاً رفتار قبلی حفظ می‌شود.
    handled = False
    if plan_type(plan_key) in ("vip", "test"):
        handled = await auto_fulfill_vip_via_marzban(callback.bot, str(callback.from_user.id), plan_key, order_id)

    if handled:
        await send_admin_task_message(
            callback.bot, ADMIN_ID, "requests",
            f"🛒 خرید جدید (کیف پول) — به‌صورت خودکار از پنل متصل ساخته و ارسال شد ✅\n\n"
            f"👤 {callback.from_user.full_name}\n"
            f"🆔 {callback.from_user.id}\n"
            f"📦 {plan['name']}\n"
            f"💰 {final_price:,} تومان",
        )
    else:
        await send_admin_task_message(
            callback.bot, ADMIN_ID, "requests",
            f"🛒 خرید جدید (کیف پول)!\n\n"
            f"👤 {callback.from_user.full_name}\n"
            f"🆔 {callback.from_user.id}\n"
            f"📦 {plan['name']}\n"
            f"💰 {final_price:,} تومان",
            reply_markup=admin_purchase_notify_keyboard(str(callback.from_user.id), plan_key, order_id),
        )
    await callback.answer()


# ---------------------------------------------------------------------------
# 🎁 تست رایگان با قیمت صفر (رایگان) — بدون هیچ مرحله‌ای انتخاب روش پرداخت
# ---------------------------------------------------------------------------
async def fulfill_free_test_directly(bot, message: types.Message, user: dict, plan: dict, plan_key: str) -> None:
    """وقتی قیمت پلن «تست رایگان» (از پنل ادمین) صفر باشد، هیچ صفحه‌ی
    انتخاب روش پرداخت (کیف پول/کارت‌به‌کارت/آنلاین) نشان داده نمی‌شود و
    همینجا (دقیقاً معادل مسیر موفق پرداخت کیف پول ولی بدون هیچ کسری از موجودی) سرویس
    ساخته و ارسال می‌شود."""
    if is_duplicate_action(f"freetestdirect_{message.from_user.id}"):
        await message.answer("\u26a0\ufe0f \u0627\u06cc\u0646 \u062f\u0631\u062e\u0648\u0627\u0633\u062a \u062f\u0631 \u062d\u0627\u0644 \u067e\u0631\u062f\u0627\u0632\u0634/\u062b\u0628\u062a\u200c\u0634\u062f\u0647 \u0627\u0633\u062a.")
        return

    if db.has_used_free_test(user["id"]):
        await message.answer(
            t("free_test_used"),
        )
        return

    if plan.get("volume_gb", 0) >= REFERRAL_MIN_VOLUME_GB:
        try:
            db.complete_referral(user["id"])
        except ValueError:
            pass

    order_id = db.create_order(user["id"], plan_key, plan["name"], plan_type(plan_key), 0)

    # 🐛 فیکس: قبلاً این پیام «ثبت درخواست» بعد از ارسال خودکار سرویس (auto_fulfill_vip_via_marzban)
    # فرستاده می‌شد و کاربر بعد از دریافت سرویس می‌دیدش که «درخواستت ثبت شد» که گیج‌کننده بود. الان این پیام زودتر از ارسال سرویس فرستاده می‌شود.
    await show_menu_with_sticker(
        bot, message.chat.id, "free_test",
        "\u2705 \u062f\u0631\u062e\u0648\u0627\u0633\u062a \u062a\u0633\u062a \u0631\u0627\u06cc\u06af\u0627\u0646 \u0634\u0645\u0627 \u062b\u0628\u062a \u0634\u062f! \u0633\u0631\u0648\u06cc\u0633 \u0634\u0645\u0627 \u0628\u0647\u200c\u0632\u0648\u062f\u06cc \u0622\u0645\u0627\u062f\u0647 \u0645\u06cc\u200c\u0634\u0648\u062f.",
    )

    handled = False
    if plan_type(plan_key) in ("vip", "test"):
        handled = await auto_fulfill_vip_via_marzban(bot, str(message.from_user.id), plan_key, order_id)

    if handled:
        await send_admin_task_message(
            bot, ADMIN_ID, "requests",
            f"\U0001F381 \u062a\u0633\u062a \u0631\u0627\u06cc\u06af\u0627\u0646 \u062c\u062f\u06cc\u062f (\u0642\u06cc\u0645\u062a: \u0631\u0627\u06cc\u06af\u0627\u0646) \u2014 \u0628\u0647\u200c\u0635\u0648\u0631\u062a \u062e\u0648\u062f\u06a9\u0627\u0631 \u0627\u0632 \u067e\u0646\u0644 \u0645\u0631\u0632\u0628\u0627\u0646 \u0633\u0627\u062e\u062a\u0647 \u0648 \u0627\u0631\u0633\u0627\u0644 \u0634\u062f \u2705\n\n"
            f"\U0001F464 {message.from_user.full_name}\n"
            f"\U0001F194 {message.from_user.id}\n"
            f"\U0001F4E6 {plan['name']}",
        )
    else:
        await send_admin_task_message(
            bot, ADMIN_ID, "requests",
            f"\U0001F381 \u062a\u0633\u062a \u0631\u0627\u06cc\u06af\u0627\u0646 \u062c\u062f\u06cc\u062f (\u0642\u06cc\u0645\u062a: \u0631\u0627\u06cc\u06af\u0627\u0646)!\n\n"
            f"\U0001F464 {message.from_user.full_name}\n"
            f"\U0001F194 {message.from_user.id}\n"
            f"\U0001F4E6 {plan['name']}",
            reply_markup=admin_purchase_notify_keyboard(str(message.from_user.id), plan_key, order_id),
        )


# ---------------------------------------------------------------------------
# پرداخت آنلاین (درگاه یونیک‌پی — کارت‌به‌کارت با تایید خودکار)
# ---------------------------------------------------------------------------
async def finalize_online_payment(bot, payment: dict) -> int | None:
    """اینوویس پرداخت‌شده‌ی یونیک‌پی را به یک سفارش واقعی تبدیل می‌کند و به
    ادمین اطلاع می‌دهد تا کانفیگ را ارسال کند.

    🐛 فیکس ریس‌کاندیشن: قبلاً idempotency فقط با یک if ساده روی payment
    ورودی چک می‌شد که چون هم پولر پس‌زمینه‌ی ربات و هم دکمه‌ی «بررسی پرداخت»
    (و در دیپلوی مینی‌اپ، endpoint جدای سرویس جداگانه) می‌توانند هم‌زمان این
    را صدا بزنند، امکان ساخت سفارش/سرویس تکراری برای یک پرداخت وجود داشت.
    حالا با db.claim_online_payment_for_finalize یک قفل اتمیک روی ردیف
    گرفته می‌شود؛ اگر فراخوانی دیگری برنده شده باشد، اینجا فقط None برمی‌گردد
    (کاری تکراری انجام نمی‌شود). اگر وسط کار خطا بیفتد، وضعیت به pending
    برمی‌گردد تا پولر بعدی دوباره تلاش کند."""
    if payment["status"] == "paid" and payment.get("order_id"):
        return payment["order_id"]

    if not db.claim_online_payment_for_finalize(payment["id"]):
        # یعنی یک فراخوانی هم‌زمان دیگر (یا پولر، یا دکمه‌ی کاربر، یا مینی‌اپ)
        # همین الان دارد/داشت همین پرداخت را پردازش می‌کند؛ برای جلوگیری از
        # سفارش تکراری اینجا هیچ کاری نمی‌کنیم.
        fresh = db.get_online_payment(payment["id"])
        if fresh and fresh["status"] == "paid" and fresh.get("order_id"):
            return fresh["order_id"]
        return None

    try:
        order_id = db.create_order(
            payment["user_id"], payment["plan_key"], payment["plan_name"],
            payment["order_type"], payment["price"],
        )
        db.mark_online_payment_paid(payment["id"], order_id)

        if payment.get("discount_code"):
            try:
                db.use_discount(payment["discount_code"], payment["user_id"])
            except Exception:
                logger.exception("خطا در مصرف کد تخفیف پس از پرداخت آنلاین")

        plan = db.get_effective_plan(payment["plan_key"]) if payment["plan_key"] else None
        if plan and plan.get("volume_gb", 0) >= REFERRAL_MIN_VOLUME_GB:
            try:
                db.complete_referral(payment["user_id"])
            except ValueError:
                pass
    except Exception:
        # اگر وسط ساخت سفارش خطا بیفتد، claim را آزاد می‌کنیم تا دفعه‌ی بعد
        # (پولر یا کلیک مجدد کاربر) بتواند دوباره تلاش کند، نه اینکه پرداخت
        # برای همیشه در حالت processing گیر کند.
        db.set_online_payment_status(payment["id"], "pending")
        raise

    handled = False
    if payment["plan_key"] and plan_type(payment["plan_key"]) in ("vip", "test"):
        handled = await auto_fulfill_vip_via_marzban(bot, payment["telegram_id"], payment["plan_key"], order_id)

    if handled:
        await send_admin_task_message(
            bot, ADMIN_ID, "requests",
            f"🛒 خرید جدید (پرداخت آنلاین - یونیک‌پی) — به‌صورت خودکار از پنل متصل ساخته و ارسال شد ✅\n\n"
            f"🆔 {payment['telegram_id']}\n"
            f"📦 {payment['plan_name']}\n"
            f"💰 {payment['price']:,} تومان",
        )
    else:
        await send_admin_task_message(
            bot, ADMIN_ID, "requests",
            f"🛒 خرید جدید (پرداخت آنلاین - یونیک‌پی)!\n\n"
            f"🆔 {payment['telegram_id']}\n"
            f"📦 {payment['plan_name']}\n"
            f"💰 {payment['price']:,} تومان",
            reply_markup=admin_purchase_notify_keyboard(payment["telegram_id"], payment["plan_key"], order_id),
        )
    return order_id




@router.callback_query(F.data.startswith("pay_online_"))
async def pay_with_online(callback: types.CallbackQuery, state: FSMContext):
    if not UNIQUEPAY_ENABLED:
        await callback.answer(t("payment_not_active"), show_alert=True)
        return

    plan_key = callback.data.replace("pay_online_", "")
    if is_duplicate_action(f"onlinebuy_{callback.from_user.id}_{plan_key}"):
        await callback.answer(t("processing_request"), show_alert=True)
        return

    plan = db.get_effective_plan(plan_key)
    if plan is None:
        await callback.answer(t("plan_not_found"), show_alert=True)
        return

    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return

    if plan_key == FREE_TEST_PLAN_KEY and db.has_used_free_test(user["id"]):
        await callback.answer(
            t("free_test_used"),
            show_alert=True,
        )
        return

    data = await state.get_data()
    final_price, _note, winning_code = _compute_final_price(plan_key, plan, callback.from_user.id, data)

    await callback.answer(t("building_payment"))

    hash_id = uniquepay.new_hash_id("plan")
    invoice = await payments.create_invoice(hash_id, final_price)
    if invoice is None:
        await alerts.report_uniquepay_create_failure(callback.bot, ADMIN_ID)
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "plan_pay_online", 
            "❌ برای مبالغ ۵۰ هزار تومان و کمتر امکان استفاده از درگاه پرداخت آنلاین نیست. لطفاً از کارت‌به‌کارت یا کیف پول استفاده کنید.",
            reply_markup=purchase_payment_keyboard(plan_key, show_discount=False),
        )
        return

    payment_link = invoice.get("paymentLink")
    if not payment_link:
        await alerts.report_uniquepay_create_failure(callback.bot, ADMIN_ID)
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "plan_pay_online", 
            "❌ برای مبالِ ۵۰ هزار تومان و کمتر امکان استفاده از درگاه پرداخت آنلاین نیست. لطفاً از کارت‌به‌کارت یا کیف پول استفاده کنید.",
            reply_markup=purchase_payment_keyboard(plan_key, show_discount=False),
        )
        return

    alerts.report_uniquepay_create_success()

    payment_id = db.create_online_payment(
        user_id=user["id"],
        telegram_id=str(callback.from_user.id),
        hash_id=hash_id,
        plan_name=plan["name"],
        price=final_price,
        order_type=plan_type(plan_key),
        plan_key=plan_key,
        discount_code=winning_code,
        payment_link=payment_link,
        ref_id=str(invoice.get("refId")),
        provider=invoice.get("provider", "uniquepay"),
    )

    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "plan_pay_online", 
        progress_bar(2, 3) + t("online_plan_invoice", plan_name=plan["name"], amount=final_price),
        reply_markup=online_payment_keyboard(payment_link, payment_id),
    )


@router.callback_query(F.data.startswith("checkpay_"))
async def check_online_payment(callback: types.CallbackQuery):
    try:
        payment_id = int(callback.data.replace("checkpay_", ""))
    except ValueError:
        await callback.answer(t("invalid_request"), show_alert=True)
        return

    payment = db.get_online_payment(payment_id)
    if payment is None:
        await callback.answer(
            t("invoice_expired_wait"),
            show_alert=True,
        )
        return

    if str(callback.from_user.id) != payment["telegram_id"]:
        await callback.answer(t("payment_owned"), show_alert=True)
        return

    if payment["status"] == "paid":
        await callback.answer(t("payment_already_confirmed"), show_alert=True)
        return

    await callback.answer(t("checking_payment"))

    invoice = await payments.check_invoice(payment)
    if not invoice or not invoice.get("isPaid"):
        await callback.answer(
            "⏳ هنوز پرداختی برای این اینوویس ثبت نشده. اگر همین الان پرداخت کردید،"
            " چند لحظه صبر کنید و دوباره بزنید.",
            show_alert=True,
        )
        return

    payment_kind = payment.get("kind")

    # 🐛 فیکس: قبلاً پیام موفقیت/تأییدیه بعد از finalize_* فرستاده می‌شد، در حالی که خود finalize_online_payment/
    # finalize_custom_online_payment در داخلشان (برای VIP/تست) سرویس را مستقیم برای کاربر ارسال می‌کنند (auto_fulfill_vip_via_marzban)؛
    # یعنی همین باگ ترتیب پیام‌های تست رایگان/بساز سرویس خودت اینجا هم وجود داشت. الان این پیام را
    # همین که پرداخت تأیید شد (invoice.isPaid) می‌فرستیم، زودتر از ارسال خودکار سرویس.
    success_text = (
        "✅ کیف پول شما شارژ شد."
        if payment_kind == "wallet_charge"
        else "✅ پرداخت شما تأیید شد و سفارش شما در صف ارسال سرویس قرار گرفت."
    )
    if payment_kind == "wallet_charge":
        _sticker_key = "walletcharge_pay_online"
    else:
        _sticker_key = "plan_pay_online"
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, _sticker_key, 
        success_text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[]),
    )

    # 🐛 فیکس: شاخهٔ "custom" (بساز-خودت) به‌همراه تابع finalize_custom_online_payment حذف شده بود (دیگر هیچ پرداختی با kind="custom" ساخته نمی‌شود)؛ فراخوانیش اینجا جا مانده بود و باعث ImportError در استارت می‌شد. حذف شد.
    if payment_kind == "wallet_charge":
        from handlers.wallet import finalize_wallet_charge_online_payment
        await finalize_wallet_charge_online_payment(callback.bot, payment)
    else:
        await finalize_online_payment(callback.bot, payment)


# ---------------------------------------------------------------------------
# پرداخت کارت به کارت
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("pay_card_"))
async def pay_with_card(callback: types.CallbackQuery, state: FSMContext):
    plan_key = callback.data.replace("pay_card_", "")
    plan = db.get_effective_plan(plan_key)
    if plan is None:
        await callback.answer(t("plan_not_found"), show_alert=True)
        return

    if plan_key == FREE_TEST_PLAN_KEY:
        user = db.get_user(callback.from_user.id)
        if user and db.has_used_free_test(user["id"]):
            await callback.answer(
                t("free_test_used"),
                show_alert=True,
            )
            return

    data = await state.get_data()
    final_price, _note, winning_code = _compute_final_price(plan_key, plan, callback.from_user.id, data)

    invoicing_user = db.get_user(callback.from_user.id)
    invoice = db.create_invoice(
        user_id=invoicing_user["id"] if invoicing_user else None,
        telegram_id=str(callback.from_user.id),
        kind="plan_card",
        label=plan["name"],
        price=final_price,
    )
    deadline_str = format_deadline_time(invoice["expires_at"])

    await state.update_data(
        card_purchase_plan=plan_key, card_purchase_price=final_price, card_purchase_discount_code=winning_code,
        card_invoice_id=invoice["id"],
    )
    await state.set_state(UserStates.waiting_card_purchase_receipt)

    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "plan_pay_card", 
        _render_card_invoice_text(
            "invoice_plan_card",
            PLAN_CARD_INVOICE_DEFAULT,
            {
                "plan_name": html.escape(plan["name"]),
                "amount": final_price,
                # 🆕 فیکس: شماره کارت داخل تگ <code> قرار می‌گیرد تا در تلگرام به‌صورت مونواسپیس
                # نمایش داده شود و با یک لمس ساده قابل کپی باشد (همراه با parse_mode="HTML" پایین).
                "card_number": bot_info.get('card_number') or '',
                "card_holder": bot_info.get("card_holder") or "",
            },
            deadline_str,
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(UserStates.waiting_card_purchase_receipt, F.photo)
async def receive_purchase_receipt(message: types.Message, state: FSMContext):
    uid = str(message.from_user.id)
    data = await state.get_data()
    plan_key = data.get("card_purchase_plan")
    final_price = data.get("card_purchase_price")
    winning_code = data.get("card_purchase_discount_code")
    invoice_id = data.get("card_invoice_id")
    plan = db.get_effective_plan(plan_key)

    if plan is None or final_price is None:
        await message.answer(t("purchase_receipt_error"), reply_markup=get_main_keyboard(message.from_user.id))
        await state.clear()
        return

    if not invoice_id or db.consume_invoice(invoice_id) is None:
        await message.answer(
            t("invoice_expired_wait"),
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        await state.clear()
        return

    user = db.get_user(uid)
    # 🐛 فیکس: کد تخفیف قبلاً همین‌جا (قبل از تأیید ادمین) مصرف می‌شد؛ یعنی اگر ادمین رسید
    # را رد می‌کرد، سهم کد تخفیف به کاربر برنمی‌گردد. حالا مانند مینی‌اپ، کد تخفیف
    # فقط همراه رسید ذخیره می‌شود و در approve_purchase (handlers/admin.py) مصرف خواهد شد.

    # 🐛 فیکس: receipt_id را حتماً نگه میداریم تا دکمه‌های تأیید/رد زیر همین رسید را در callback_data حمل کنند
    # (وگرنه دو رسید با همان پلن/قیمت با هم تداخل می‌کنند و پیام «قبلاً پردازش شده» اشتباه می‌دهد).
    receipt_id = None
    try:
        receipt_id = db.create_pending_receipt(
            "plan_card", uid, user["id"] if user else None, plan["name"], final_price,
            extra=plan_key, plan_key=plan_key, discount_code=winning_code,
        )
    except Exception:
        receipt_id = None

    if invoice_id:
        db.delete_invoice(invoice_id)

    await forward_admin_task_message(message.bot, ADMIN_ID, "receipts", message.chat.id, message.message_id)
    await send_admin_task_message(
        message.bot, ADMIN_ID, "receipts",
        f"💳 رسید خرید کارت‌به‌کارت\n\n"
        f"👤 {message.from_user.full_name}\n"
        f"🆔 {uid}\n"
        f"📦 {plan['name']}\n"
        f"💰 {final_price:,} تومان",
        reply_markup=admin_purchase_card_approval_keyboard(uid, plan_key, final_price, receipt_id or 0),
    )
    # 🐛 فیکس: منوی دائمی پایین صفحه‌ی کاربر را صریحاً روی همین پیام تازه می‌کنیم؛ قبلاً این
    # پیام بدون reply_markup فرستاده می‌شد و برای کاربری که منوی پایین صفحه‌اشرا جمعشده
    # بود (مثلاً پس از یک پیام دارای دکمه‌ی inline)، منو تا زدن /start دوباره باز
    # نمی‌شد و کاربر/مشتری فکر می‌کرد منو کاملاً گم شده.
    await message.answer(
        progress_bar(3, 3) + t("card_receipt_registered"),
        reply_markup=get_main_keyboard(message.from_user.id),
    )
    await state.update_data(
        discount_percent=0, discount_code="", card_purchase_plan=None,
        card_purchase_price=None, card_purchase_discount_code=None, card_invoice_id=None,
    )
    await state.set_state(None)


@router.message(UserStates.waiting_card_purchase_receipt)
async def purchase_receipt_wrong_format(message: types.Message):
    await message.answer(t("receipt_photo_only"))


# ---------------------------------------------------------------------------
# سرویس‌های من
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "my_configs")
async def my_configs(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return

    configs = [c for c in db.get_configs(user["id"]) if not is_config_expired(c)]
    if not configs:
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "my_configs_empty", 
            t("configs_empty"),
            reply_markup=back_button("back", "🏠 بازگشت به منوی اصلی"),
        )
    else:
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "my_configs_has", 
            t("configs_has"),
            reply_markup=my_configs_menu(),
        )
    await callback.answer()


@router.callback_query(F.data == "my_configs_vip")
async def my_configs_vip(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return

    configs = [c for c in db.get_configs_by_type(user["id"], "vip") if not is_config_expired(c)]
    if not configs:
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "my_configs_list_empty", 
            t("vip_configs_empty"),
            reply_markup=back_button("my_configs", "🔙 بازگشت"),
        )
    else:
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "my_configs_list_has", 
            t("vip_configs_has"),
            reply_markup=my_configs_list_keyboard(configs, "🚀", "my_configs"),
        )
    await callback.answer()




@router.callback_query(F.data.startswith("viewconfig_"))
async def view_config(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return

    try:
        cfg_id = int(callback.data.replace("viewconfig_", ""))
    except ValueError:
        await callback.answer(t("service_not_found"), show_alert=True)
        return

    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"] or cfg.get("deleted"):
        await callback.answer(t("service_not_owned"), show_alert=True)
        return

    try:
        decrypted = crypto.decrypt_config(cfg["config"])
    except Exception:
        decrypted = t("config_decode_error")

    sub_url = decrypted if decrypted.lower().startswith(("http://", "https://")) else None

    # نشون بده داریم اطلاعات مصرف رو زنده می‌خونیم (ممکنه چند ثانیه طول بکشه)
    await callback.answer(t("service_loading"))

    # -----------------------------------------------------------------
    # نکته‌ی مهم: اگر هر خطای غیرمنتظره‌ای (نه فقط در دریافت اطلاعات مصرف،
    # بلکه حتی در ساخت متن یا کیبورد جزئیات) رخ می‌داد، چون callback.answer بالا از
    # قبل صدا زده شده بود، هندلر سراسری خطا نمی‌توانست دوباره پیامی
    # بدهد و کاربر هیچ نتیجه‌ای نمی‌دید: پیام لیست قبلی همینطور روی صفحه می‌ماند و
    # تپ‌کردن روی سرویس بی‌اثر به نظر می‌رسید (برخلاف مینی‌اپ که این بخش را
    # در یک درخواست جدا و بدون این ریسک نشان می‌داد). به همین دلیل
    # کل این بخش الان با همون الگوی محافظتی mirror_configs احاطه شده تا کاربر همیشه
    # یک نتیجه (جزئیات سرویس یا پیام خطای واضح) ببیند.
    # -----------------------------------------------------------------
    try:

        text = f"📦 {cfg['plan']}\n\n"

        try:
            usage = await fetch_subscription_info(decrypted)
        except Exception:
            logger.exception("خطای غیرمنتظره در fetch_subscription_info برای cfg_id=%s", cfg_id)
            usage = None
        if usage:
            total = usage.get("total")
            used = (usage.get("upload") or 0) + (usage.get("download") or 0)
            remaining = (total - used) if total else None

            text += t("config_status_title") + "\n"
            if total:
                text += t("config_total", value=format_bytes(total)) + "\n"
            text += t("config_used", value=format_bytes(used)) + "\n"
            if remaining is not None:
                text += t("config_remaining", value=format_bytes(remaining)) + "\n"
            if total:
                percent = min(100, round(used / total * 100))
                text += "\n" + t("config_percent", bar=usage_bar(percent), percent=percent) + "\n"
            text += "\n" + t("config_expiry", value=format_expire(usage.get("expire"))) + "\n"
            remaining = days_remaining(usage.get("expire"))
            if remaining is not None:
                text += (t("config_expired") + "\n\n" if remaining <= 0 else t("config_days_left", days=remaining) + "\n\n")
            else:
                text += "\n"
        elif cfg["expiry"]:
            text += t("config_expiry", value=cfg["expiry"]) + "\n"
            try:
                _exp_dt = datetime.strptime(str(cfg["expiry"])[:10], "%Y-%m-%d")
                _remaining_days = (_exp_dt - now_tehran_naive()).days
                text += (t("config_expired") + "\n\n" if _remaining_days <= 0 else t("config_days_left", days=_remaining_days) + "\n\n")
            except Exception:
                text += "\n"

        text += t("config_subscription_help") + "\n\n" + f"`{decrypted}`" + "\n\n" + t("config_purchase_date", date=cfg["created_at"])

        kb = config_detail_keyboard(cfg_id, sub_link_url=sub_url, has_qr=bool(cfg.get("qr_file_id")), service_id=cfg.get("service_id"), disabled=bool(cfg.get("disabled")))
        try:
            await show_menu_with_sticker(callback.bot, callback.message.chat.id, "config_detail", text, parse_mode="Markdown", reply_markup=kb)
        except Exception as e:
            # اگر مارک‌داون به هر دلیلی (مثلاً کاراکتر خاص داخل لینک ساب) شکست
            # بخورد، کاربر نباید بدون هیچ نتیجه‌ای رها شود؛ متن رو بدون فرمت دوباره
            # امتحان می‌کنیم. "message is not modified" را هم بی‌خطر نادید�� می‌گیریم
            # (یعنی محتوای جدید دقیقاً همون محتوای قبلی بود؛ کاربر همون اطلاعات رو
            # روی صفحه می‌بیند، پس نیازی به هشدار نیست).
            if "message is not modified" in str(e).lower():
                pass
            else:
                logger.exception("خطا در ویرایش پیام جزئیات سرویس برای cfg_id=%s", cfg_id)
                try:
                    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "config_detail", text, parse_mode=None, reply_markup=kb)
                except Exception:
                    logger.exception("خطا در ارسال fallback بدون فرمت برای cfg_id=%s", cfg_id)
                    await callback.message.answer(
                        t("config_detail_error")
                    )
    except Exception:
        # هر خطای غیرمنتظره‌ی دیگری (خارج از مسیرهای بالا، مثلاً در ساخت متن یا کیبورد) هم اینجا گرفته می‌شود
        # تا کاربر هرگز روی همون پیام لیست قبلی «گیر» نکند و همیشه پیام یا خطای واضحی ببیند.
        logger.exception("خطای کلی غیرمنتظره در نمایش جزئیات سرویس برای cfg_id=%s", cfg_id)
        try:
            await callback.message.answer(
                t("config_detail_error"),
                reply_markup=back_button("my_configs_vip"),
            )
        except Exception:
            logger.exception("خطا در ارسال پیام خطای fallback برای cfg_id=%s", cfg_id)


# حداکثر واقعی تلگرام برای متن یک پیام ۴۰۹۶ کاراکتر است؛ برای امنیت بیشتر
# (کدهای یونیکد چندبایتی و فاصله‌ی احتیاطی) عدد کمتری در نظر گرفته می‌شود.
_TELEGRAM_MSG_SAFE_LIMIT = 3500


async def _send_configs_safely(callback: types.CallbackQuery, configs: list[str], plan_name: str):
    """کانفیگ‌های تکی استخراج‌شده را در چند پیام (هرکدام زیر سقف امن تلگرام)
    برای کاربر ارسال می‌کند. برای هر پیام:
    ۱) هر کانفیگ تکی که به‌تنهایی طولانی‌تر از سقف امن باشد را در پیام
       جداگانه‌ی خودش می‌فرستد تا هرگز یک پیام بیش از حد مجاز تلگرام نشود.
    ۲) اگر ارسال با فرمت مارک‌داون (برای امکان تپ-کپی راحت‌تر) به هر دلیلی
       (مثلاً کاراکتر خاص داخل یک کانفیگ) با خطا مواجه شد، بدون فرمت و به‌صورت
       متن ساده دوباره ارسال می‌کند تا کاربر حتماً کانفیگ را دریافت کند.
    """

    async def _send(text: str, parse_mode: str | None):
        try:
            await callback.message.answer(text, parse_mode=parse_mode)
            return True
        except Exception:
            logger.exception("خطا در ارسال پیام کانفیگ (parse_mode=%s)", parse_mode)
            return False

    async def _send_with_fallback(text_md: str, text_plain: str):
        if await _send(text_md, "Markdown"):
            return
        # اگر مارک‌داون شکست خورد، همون متن رو بدون فرمت دوباره امتحان کن
        if not await _send(text_plain, None):
            await callback.message.answer(
                t("config_delivery_error")
            )

    header = f"📥 {len(configs)} کانفیگ از سرویس {plan_name} پیدا شد:\n\n"
    chunk_md = header
    chunk_plain = header

    for conf in configs:
        # اگر یک کانفیگ به‌تنهایی از سقف امن بزرگ‌تر باشد (مثلاً کانفیگ‌های
        # reality/hysteria2 با پارامترهای زیاد)، نمی‌توان آن را با بقیه در یک
        # پیام جا داد؛ باید تنها و مستقیماً ارسال شود.
        conf_md_line = f"`{conf}`\n\n"
        if len(conf_md_line) > _TELEGRAM_MSG_SAFE_LIMIT:
            if chunk_md != header:
                await _send_with_fallback(chunk_md, chunk_plain)
                chunk_md, chunk_plain = header, header
            await _send_with_fallback(f"`{conf}`", conf)
            continue

        if len(chunk_md) + len(conf_md_line) > _TELEGRAM_MSG_SAFE_LIMIT:
            await _send_with_fallback(chunk_md, chunk_plain)
            chunk_md, chunk_plain = header, header

        chunk_md += conf_md_line
        chunk_plain += f"{conf}\n\n"

    if chunk_md != header:
        await _send_with_fallback(chunk_md, chunk_plain)


@router.callback_query(F.data.startswith("mirrorconfigs_"))
async def mirror_configs(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return

    try:
        cfg_id = int(callback.data.replace("mirrorconfigs_", ""))
    except ValueError:
        await callback.answer(t("service_not_found"), show_alert=True)
        return

    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"] or cfg.get("deleted"):
        await callback.answer(t("service_not_owned"), show_alert=True)
        return

    try:
        decrypted = crypto.decrypt_config(cfg["config"])
    except Exception:
        await callback.answer(t("config_decode_error"), show_alert=True)
        return

    if not decrypted or not decrypted.lower().startswith(("http://", "https://")):
        await callback.answer(t("subscription_missing"), show_alert=True)
        return

    await callback.answer(t("subscription_fetching"))

    # -----------------------------------------------------------------
    # نکته‌ی مهم: قبلاً اگر یک خطای غیرمنتظره (مثلاً پیام خیلی طولانی برای
    # تلگرام یا کاراکتر خاصی که پارس مارک‌داون را خراب می‌کرد) در این بخش رخ
    # می‌داد، چون callback.answer بالا از قبل صدا زده شده بود، هندلر سراسری
    # خطا (bot.py) نمی‌توانست دوباره callback را answer کند و کاربر هیچ
    # پیام/خطایی نمی‌دید؛ دکمه فقط "در حال دریافت..." نشان می‌داد و بعد هیچ
    # اتفاقی نمی‌افتاد. حالا کل این بخش try/except دارد تا در هر حالتی
    # کاربر حتماً یک نتیجه (موفق یا پیام خطای واضح) ببیند.
    # -----------------------------------------------------------------
    try:
        configs = await extract_configs(decrypted)
    except Exception:
        logger.exception("خطای غیرمنتظره در extract_configs برای cfg_id=%s", cfg_id)
        configs = None

    if configs is None:
        await callback.message.answer(t("subscription_unavailable"))
        return
    if not configs:
        await callback.message.answer(
            "⚠️ لینک ساب باز شد ولی هیچ کانفیگ تکی‌ای داخلش پیدا نشد.\n"
            "برای استفاده، همون لینک ساب رو مستقیم داخل اپ V2Ray/Clash وارد کنید."
        )
        return

    await _send_configs_safely(callback, configs, cfg["plan"])


@router.callback_query(F.data.startswith("viewqr_"))
async def view_config_qr(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return

    try:
        cfg_id = int(callback.data.replace("viewqr_", ""))
    except ValueError:
        await callback.answer(t("service_not_found"), show_alert=True)
        return

    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"] or cfg.get("deleted"):
        await callback.answer(t("service_not_owned"), show_alert=True)
        return
    if not cfg.get("qr_file_id"):
        await callback.answer(t("qr_missing"), show_alert=True)
        return

    await callback.answer()
    try:
        await callback.bot.send_photo(callback.from_user.id, cfg["qr_file_id"], caption=f"🖼 کیوآرکد {cfg['plan']}")
    except Exception:
        await callback.answer(t("qr_failed"), show_alert=True)


@router.callback_query(F.data.startswith("delconfig_"))
async def delete_config_confirm(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return

    try:
        cfg_id = int(callback.data.replace("delconfig_", ""))
    except ValueError:
        await callback.answer(t("service_not_found"), show_alert=True)
        return

    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"] or cfg.get("deleted"):
        await callback.answer(t("service_not_owned"), show_alert=True)
        return

    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "config_delete_confirm", 
        t("service_delete_confirm", plan=cfg["plan"]),
        reply_markup=confirm_delete_config_keyboard(cfg_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delconfirm_"))
async def delete_config_apply(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return

    try:
        cfg_id = int(callback.data.replace("delconfirm_", ""))
    except ValueError:
        await callback.answer(t("service_not_found"), show_alert=True)
        return

    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"]:
        await callback.answer(t("service_not_owned"), show_alert=True)
        return

    db.set_config_deleted(cfg_id, True)
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, None, 
        "✅ سرویس حذف شد.",
        reply_markup=back_button("my_configs_vip"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cfgdisable_"))
async def user_service_disable_confirm(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return
    try:
        cfg_id = int(callback.data.replace("cfgdisable_", ""))
    except ValueError:
        await callback.answer(t("service_not_found"), show_alert=True)
        return
    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"] or cfg.get("deleted") or not cfg.get("service_id"):
        await callback.answer(t("service_disable_not_available"), show_alert=True)
        return
    await callback.message.answer(
        t("service_disable_confirm"),
        reply_markup=confirm_disable_service_keyboard(cfg_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cfgdisabledo_"))
async def user_service_disable_apply(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return
    try:
        cfg_id = int(callback.data.replace("cfgdisabledo_", ""))
    except ValueError:
        await callback.answer(t("service_not_found"), show_alert=True)
        return
    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"] or cfg.get("deleted") or not cfg.get("service_id"):
        await callback.answer(t("service_not_owned"), show_alert=True)
        return
    await callback.answer(t("config_disabling"))
    ok, data, msg = await vpn_panel.disable_user(cfg["service_id"])
    if not ok:
        await callback.message.answer(t("service_disable_failed", msg=msg))
        return
    db.set_config_disabled(cfg_id, True)
    await callback.message.answer(
        t("service_disabled"),
        reply_markup=back_button(f"viewconfig_{cfg_id}", t("config_back_service")),
    )


@router.callback_query(F.data.startswith("cfgenable_"))
async def user_service_enable_apply(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return
    try:
        cfg_id = int(callback.data.replace("cfgenable_", ""))
    except ValueError:
        await callback.answer(t("service_not_found"), show_alert=True)
        return
    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"] or cfg.get("deleted") or not cfg.get("service_id"):
        await callback.answer(t("service_not_owned"), show_alert=True)
        return
    await callback.answer(t("config_enabling"))
    ok, data, msg = await vpn_panel.enable_user(cfg["service_id"])
    if not ok:
        await callback.message.answer(t("service_enable_failed", msg=msg))
        return
    db.set_config_disabled(cfg_id, False)
    await callback.message.answer(
        t("service_enabled"),
        reply_markup=back_button(f"viewconfig_{cfg_id}", t("config_back_service")),
    )


@router.callback_query(F.data.startswith("cfgrevokesub_"))
async def user_service_revoke_sub_confirm(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return
    try:
        cfg_id = int(callback.data.replace("cfgrevokesub_", ""))
    except ValueError:
        await callback.answer(t("service_not_found"), show_alert=True)
        return
    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"] or cfg.get("deleted") or not cfg.get("service_id"):
        await callback.answer(t("service_revoke_not_available"), show_alert=True)
        return
    await callback.message.answer(
        t("service_revoke_confirm"),
        reply_markup=confirm_revoke_sub_keyboard(cfg_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cfgrevokesubdo_"))
async def user_service_revoke_sub_apply(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return
    try:
        cfg_id = int(callback.data.replace("cfgrevokesubdo_", ""))
    except ValueError:
        await callback.answer(t("service_not_found"), show_alert=True)
        return
    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"] or cfg.get("deleted") or not cfg.get("service_id"):
        await callback.answer(t("service_not_owned"), show_alert=True)
        return
    await callback.answer(t("config_revoking"))
    ok, data, msg = await vpn_panel.revoke_sub(cfg["service_id"])
    if not ok:
        await callback.message.answer(t("service_revoke_failed", msg=msg))
        return
    link, _slug = vpn_panel.extract_link_and_username(data)
    if not link:
        await callback.message.answer(t("service_revoke_missing"))
        return
    db.update_config_link(cfg_id, crypto.encrypt_config(link))
    await callback.message.answer(
        t("service_revoke_done"),
        reply_markup=back_button(f"viewconfig_{cfg_id}", t("config_back_service")),
    )




# ---------------------------------------------------------------------------
# 🛠 سرویس خودت رو بساز (و همچنین 🔁 تمدید سرویس از همین مسیر مشترک رد می‌شود)
# ---------------------------------------------------------------------------
_LATIN_NAME_RE = re.compile(r"^[A-Za-z0-9]+$")
























# ===========================================================================
# 🛠 بساز سرویس خودت — VIP custom build (بدون )
# ===========================================================================
def _calc_custom_price(volume_gb: int, days: int, telegram_id=None) -> tuple[int, bool]:
    """قیمت «بساز سرویس خودت» را حساب می‌کند و اگر کاربر نماینده باشد، تخفیف
    نمایندگی‌اش (مثل پلن‌های VIP) روی همین قیمت هم اعمال می‌شود.
    خروجی دوم True است اگر تخفیف نمایندگی اعمال شده باشد.
    🧩 قیمت هر گیگ/هر ۳۰ روز اینجا دیگر دایم از config.py خوانده نمی‌شود، بلکه از
    db.get_effective_custom_build_settings() تا تنظیم اختصاصی ادمین (اگر وجود دارد) هم لحاز شود."""
    settings = db.get_effective_custom_build_settings()
    price = volume_gb * settings["price_per_gb"] + (days / 30) * settings["price_per_30_days"]
    discount_applied = False
    if telegram_id is not None:
        agent = db.get_agent(telegram_id)
        if agent:
            price = price * (1 - agent["vip_discount_percent"] / 100)
            discount_applied = True
    return int(round(price)), discount_applied


async def finalize_custom_online_payment(bot, payment: dict) -> int | None:
    """معادل finalize_online_payment، برای سفارش‌های «بساز سرویس خودت» که با
    یونیک‌پی پرداخت شده‌اند (حجم/مدت/نام در فیلد extra به‌صورت JSON ذخیره شده).
    🐛 همان فیکس ریس‌کاندیشن finalize_online_payment اینجا هم اعمال شده."""
    if payment["status"] == "paid" and payment.get("order_id"):
        return payment["order_id"]

    if not db.claim_online_payment_for_finalize(payment["id"]):
        fresh = db.get_online_payment(payment["id"])
        if fresh and fresh["status"] == "paid" and fresh.get("order_id"):
            return fresh["order_id"]
        return None

    extra = json.loads(payment.get("extra") or "{}")
    volume = extra.get("volume")
    days = extra.get("days")
    custom_name = extra.get("custom_name")
    order_type = extra.get("order_type", "new")
    target_config_id = extra.get("target_config_id")

    try:
        order_id = db.create_custom_order(
            payment["user_id"], volume, days, custom_name, payment["price"], order_type, target_config_id
        )
        db.set_custom_order_status(order_id, "paid")
        db.mark_online_payment_paid(payment["id"], order_id)

        if volume and volume >= REFERRAL_MIN_VOLUME_GB:
            try:
                db.complete_referral(payment["user_id"])
            except ValueError:
                pass
    except Exception:
        db.set_online_payment_status(payment["id"], "pending")
        raise

    user = db.get_user_by_id(payment["user_id"])
    label = "تمدید سرویس" if order_type == "renew" else "سرویس سفارشی جدید (بساز سرویس خودت)"

    handled = False
    if user:
        handled = await auto_fulfill_custom_via_marzban(bot, user, order_id, volume, days, custom_name)

    if handled:
        await send_admin_task_message(
            bot, ADMIN_ID, "requests",
            f"🛠 {label} (پرداخت آنلاین - یونیک‌پی) — به‌صورت خودکار از پنل متصل ساخته و ارسال شد ✅\n\n"
            f"🆔 {payment['telegram_id']}\n"
            f"📦 حجم: {volume} گیگ\n⏳ مدت: {days} روز\n"
            + (f"🔤 نام: {custom_name}\n" if custom_name else "")
            + f"💰 {payment['price']:,} تومان\n🔢 شماره سفارش: {order_id}",
        )
    else:
        await send_admin_task_message(
            bot, ADMIN_ID, "requests",
            f"🛠 {label} (پرداخت آنلاین - یونیک‌پی)!\n\n"
            f"🆔 {payment['telegram_id']}\n"
            f"📦 حجم: {volume} گیگ\n⏳ مدت: {days} روز\n"
            + (f"🔤 نام: {custom_name}\n" if custom_name else "")
            + f"💰 {payment['price']:,} تومان\n🔢 شماره سفارش: {order_id}",
            reply_markup=admin_custom_order_notify_keyboard(order_id),
        )
    return order_id

@router.callback_query(F.data == "cbuild_start")
async def cbuild_start(callback: types.CallbackQuery, state: FSMContext):
    if not db.is_orders_enabled():
        await callback.answer(ORDERS_CLOSED_TEXT, show_alert=True)
        return

    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer("ابتدا دستور /start را بزنید.", show_alert=True)
        return

    cb_settings = db.get_effective_custom_build_settings()
    await state.clear()
    await state.update_data(custom_order_type="new", custom_target_config_id=None)
    await state.set_state(UserStates.waiting_custom_volume)
    # 🧪 تست: استیکر make.webm درست بالای منوی «کانفیگ خودتو بساز»
    await show_menu_with_sticker(
        callback.bot, callback.message.chat.id, "custom_build",
        progress_bar(1, 3) +
        t("custom_build_title", "🛠 سرویس خودت رو بساز") + "\n\n"
        f"💡 نحوه محاسبه قیمت: هر گیگابایت حجم {cb_settings['price_per_gb']:,} تومان + "
        f"هر ۳۰ روز {cb_settings['price_per_30_days']:,} تومان (متناسب با تعداد روزها محاسبه می‌شه).\n\n"
        f"📦 حجم سرویس مورد نظرت رو به گیگابایت وارد کن (بین {cb_settings['min_gb']} تا {cb_settings['max_gb']}):",
        reply_markup=custom_build_cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renew_"))
async def renew_start(callback: types.CallbackQuery, state: FSMContext):
    if not db.is_orders_enabled():
        await callback.answer(ORDERS_CLOSED_TEXT, show_alert=True)
        return

    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer("ابتدا دستور /start را بزنید.", show_alert=True)
        return

    try:
        cfg_id = int(callback.data.replace("renew_", ""))
    except ValueError:
        await callback.answer("❌ سرویس یافت نشد.", show_alert=True)
        return

    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"]:
        await callback.answer("❌ این سرویس متعلق به شما نیست یا یافت نشد.", show_alert=True)
        return

    cb_settings = db.get_effective_custom_build_settings()
    await state.clear()
    await state.update_data(custom_order_type="renew", custom_target_config_id=cfg_id)
    await state.set_state(UserStates.waiting_custom_volume)
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "custom_build", 
        f"🔁 تمدید سرویس «{cfg['plan']}»\n\n"
        f"💡 نحوه محاسبه قیمت: هر گیگابایت حجم {cb_settings['price_per_gb']:,} تومان + "
        f"هر ۳۰ روز {cb_settings['price_per_30_days']:,} تومان (متناسب با تعداد روزها محاسبه می‌شه).\n\n"
        f"📦 حجمی که می‌خوای برای تمدید اضافه بشه رو به گیگابایت وارد کن "
        f"(بین {cb_settings['min_gb']} تا {cb_settings['max_gb']}):",
        reply_markup=custom_build_cancel_keyboard(),
    )
    await callback.answer()


@router.message(UserStates.waiting_custom_volume)
async def custom_volume_input(message: types.Message, state: FSMContext):
    cb_settings = db.get_effective_custom_build_settings()
    volume = parse_int_in_range(message.text, cb_settings["min_gb"], cb_settings["max_gb"])
    if volume is None:
        await message.answer(
            f"❌ لطفاً فقط یک عدد بین {cb_settings['min_gb']} تا {cb_settings['max_gb']} وارد کن:"
        )
        return

    await state.update_data(custom_volume=volume)
    await state.set_state(UserStates.waiting_custom_days)
    data = await state.get_data()
    text = f"⏳ مدت اعتبار سرویس رو به روز وارد کن (بین {cb_settings['min_days']} تا {cb_settings['max_days']}):"
    if data.get("custom_order_type") == "new":
        # 🧪 تست: ادامه‌ی استیکر make.webm در مرحله‌ی بعدی مسیر «بساز سرویس خودت»
        await show_menu_with_sticker(message.bot, message.chat.id, "custom_build", text)
    else:
        await show_menu_with_sticker(message.bot, message.chat.id, "custom_build", text)


@router.message(UserStates.waiting_custom_days)
async def custom_days_input(message: types.Message, state: FSMContext):
    cb_settings = db.get_effective_custom_build_settings()
    days = parse_int_in_range(message.text, cb_settings["min_days"], cb_settings["max_days"])
    if days is None:
        await message.answer(
            f"❌ لطفاً فقط یک عدد بین {cb_settings['min_days']} تا {cb_settings['max_days']} وارد کن:"
        )
        return

    await state.update_data(custom_days=days)
    data = await state.get_data()

    if data.get("custom_order_type") == "renew":
        await state.set_state(None)
        await _show_custom_summary(message, state)
        return

    await state.set_state(UserStates.waiting_custom_name)
    # 🧪 تست: ادامه‌ی استیکر make.webm در مرحله‌ی وارد کردن نام سرویس
    await show_menu_with_sticker(
        message.bot, message.chat.id, "custom_build",
        "🔤 یک نام (به لاتین، بدون فاصله و کاراکتر اضافه) برای سرویست وارد کن؛ فقط حروف انگلیسی و عدد:\nمثال: aminvpn1",
    )


@router.message(UserStates.waiting_custom_name)
async def custom_name_input(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name or not _LATIN_NAME_RE.match(name):
        await message.answer("❌ فقط حروف انگلیسی و عدد، بدون فاصله و بدون کاراکتر اضافه؛ دوباره وارد کن:")
        return

    await state.update_data(custom_name=name)
    await state.set_state(None)
    await _show_custom_summary(message, state)


async def _show_custom_summary(message: types.Message, state: FSMContext):
    data = await state.get_data()
    volume = data["custom_volume"]
    days = data["custom_days"]
    price, agent_discount_applied = _calc_custom_price(volume, days, message.from_user.id)
    await state.update_data(custom_price=price)

    is_renew = data.get("custom_order_type") == "renew"
    title = "🔁 خلاصه‌ی تمدید سرویس" if is_renew else "🛠 خلاصه‌ی سرویس سفارشی"

    text = (
        f"{title}\n\n"
        f"📦 حجم: {volume} گیگابایت\n"
        f"⏳ مدت: {days} روز\n"
    )
    if not is_renew:
        text += f"🔤 نام سرویس: {data['custom_name']}\n"
    text += f"\n💰 قیمت نهایی: {price:,} تومان"
    if agent_discount_applied:
        text += " (تخفیف نمایندگی اعمال شد)"
    text += "\n\nروش پرداخت را انتخاب کنید:"

    # 🧪 تست: از اینجا به بعد (مرحله‌ی انتخاب روش پرداخت و بعدش) دیگر هیچ استیکری
    # نشان داده نمی‌شود؛ این فراخوانی همچنین آخرین استیکر/منوی make.webm را پاک می‌کند.
    await show_menu_with_sticker(message.bot, message.chat.id, "cbuild_payment_method", text, reply_markup=custom_build_payment_keyboard())


@router.callback_query(F.data == "cbuild_pay_wallet")
async def cbuild_pay_wallet(callback: types.CallbackQuery, state: FSMContext):
    if is_duplicate_action(f"cbuildwalletbuy_{callback.from_user.id}"):
        await callback.answer("⚠️ این درخواست در حال پردازش/ثبت‌شده است.", show_alert=True)
        return

    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer("ابتدا دستور /start را بزنید.", show_alert=True)
        return

    data = await state.get_data()
    volume, days, price = data.get("custom_volume"), data.get("custom_days"), data.get("custom_price")
    if volume is None or days is None or price is None:
        await callback.answer("❌ مشکلی پیش آمد، لطفاً دوباره از منوی سرویس‌ها شروع کنید.", show_alert=True)
        return

    if user["wallet"] < price:
        needed = price - user["wallet"]
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "cbuild_pay_wallet", 
            f"❌ موجودی کیف پول کافی نیست!\n\n💰 قیمت: {price:,} تومان\n"
            f"👛 موجودی: {user['wallet']:,} تومان\n⚠️ کمبود: {needed:,} تومان",
            reply_markup=insufficient_balance_keyboard(),
        )
        await callback.answer()
        return

    order_type = data.get("custom_order_type", "new")
    target_config_id = data.get("custom_target_config_id")
    custom_name = data.get("custom_name")

    success = db.deduct_from_wallet(user["id"], price, "خرید سرویس سفارشی" if order_type == "new" else "تمدید سرویس")
    if not success:
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "cbuild_pay_wallet", 
            "❌ موجودی کافی نیست. ممکن است موجودی شما تغییر کرده باشد.",
            reply_markup=insufficient_balance_keyboard(),
        )
        await callback.answer()
        return

    order_id = db.create_custom_order(user["id"], volume, days, custom_name, price, order_type, target_config_id)
    db.set_custom_order_status(order_id, "paid")

    if volume >= REFERRAL_MIN_VOLUME_GB:
        try:
            db.complete_referral(user["id"])
        except ValueError:
            pass

    label = "تمدید سرویس" if order_type == "renew" else "سرویس سفارشی جدید (بساز سرویس خودت)"

    # 🐛 فیکس: پیام «پرداخت شما ثبت شد» را زودتر از ارسال خودکار سرویس می‌فرستیم تا کاربر قبل از دریافت سرویس، این پیام را ببیند.
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "cbuild_pay_wallet", "✅ پرداخت شما ثبت شد. سفارش شما در صف ارسال سرویس قرار گرفت.")
    await state.clear()

    # همون منطق VIP: با کیف‌پول/آنلاین، اگه مرزبان یک نگاشت پیش‌فرض برای این
    # بخش داشته باشه، بدون نیاز به انتخاب ادمین خودکار ساخته و ارسال می‌شه.
    handled = await auto_fulfill_custom_via_marzban(callback.bot, user, order_id, volume, days, custom_name)

    if handled:
        await send_admin_task_message(
            callback.bot, ADMIN_ID, "requests",
            f"🛠 {label} — به‌صورت خودکار از پنل متصل ساخته و ارسال شد ✅\n\n"
            f"👤 {callback.from_user.full_name}\n🆔 {callback.from_user.id}\n"
            f"📦 حجم: {volume} گیگ\n⏳ مدت: {days} روز\n"
            + (f"🔤 نام: {custom_name}\n" if custom_name else "")
            + f"💰 {price:,} تومان (پرداخت‌شده از کیف پول)\n🔢 شماره سفارش: {order_id}",
        )
    else:
        await send_admin_task_message(
            callback.bot, ADMIN_ID, "requests",
            f"🛠 {label}!\n\n"
            f"👤 {callback.from_user.full_name}\n🆔 {callback.from_user.id}\n"
            f"📦 حجم: {volume} گیگ\n⏳ مدت: {days} روز\n"
            + (f"🔤 نام: {custom_name}\n" if custom_name else "")
            + f"💰 {price:,} تومان (پرداخت‌شده از کیف پول)\n🔢 شماره سفارش: {order_id}",
            reply_markup=admin_custom_order_notify_keyboard(order_id),
        )
    await callback.answer()


@router.callback_query(F.data == "cbuild_pay_online")
async def cbuild_pay_online(callback: types.CallbackQuery, state: FSMContext):
    if not UNIQUEPAY_ENABLED:
        await callback.answer("این روش پرداخت در حال حاضر فعال نیست.", show_alert=True)
        return
    if is_duplicate_action(f"cbuildonlinebuy_{callback.from_user.id}"):
        await callback.answer("⚠️ این درخواست در حال پردازش/ثبت‌شده است.", show_alert=True)
        return

    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer("ابتدا دستور /start را بزنید.", show_alert=True)
        return

    data = await state.get_data()
    volume, days, price = data.get("custom_volume"), data.get("custom_days"), data.get("custom_price")
    if volume is None or days is None or price is None:
        await callback.answer("❌ مشکلی پیش آمد، لطفاً دوباره از منوی سرویس‌ها شروع کنید.", show_alert=True)
        return

    order_type = data.get("custom_order_type", "new")
    target_config_id = data.get("custom_target_config_id")
    custom_name = data.get("custom_name")

    await callback.answer("⏳ در حال ساخت لینک پرداخت...")

    hash_id = uniquepay.new_hash_id("cbuild")
    invoice = await payments.create_invoice(hash_id, price)
    if invoice is None or not invoice.get("paymentLink"):
        await alerts.report_uniquepay_create_failure(callback.bot, ADMIN_ID)
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "cbuild_pay_online", 
            "❌ برای مبالغ ۵۰ هزار تومان و کمتر امکان استفاده از درگاه پرداخت آنلاین نیست. لطفاً از کارت‌به‌کارت یا کیف پول استفاده کنید.",
            reply_markup=custom_build_payment_keyboard(),
        )
        return

    payment_link = invoice.get("paymentLink")
    alerts.report_uniquepay_create_success()

    extra_payload = json.dumps({
        "volume": volume, "days": days, "custom_name": custom_name,
        "order_type": order_type, "target_config_id": target_config_id,
    })
    payment_id = db.create_online_payment(
        user_id=user["id"],
        telegram_id=str(callback.from_user.id),
        hash_id=hash_id,
        plan_name=custom_name or "سرویس سفارشی (بساز سرویس خودت)",
        price=price,
        order_type="custom",
        plan_key=None,
        payment_link=payment_link,
        ref_id=str(invoice.get("refId")),
        kind="custom",
        extra=extra_payload,
        provider=invoice.get("provider", "uniquepay"),
    )

    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "cbuild_pay_online", 
        progress_bar(2, 3) +
        f"🌐 پرداخت آنلاین (کارت‌به‌کارت خودکار)\n\n"
        f"🧩 سرویس سفارشی — {volume} گیگ / {days} روز\n"
        f"💰 مبلغ قابل پرداخت: {price:,} تومان\n\n"
        f"روی دکمه‌ی «پرداخت» بزنید، مبلغ را واریز کنید، سپس همینجا روی «بررسی کنید» بزنید.\n"
        f"⏱ به‌محض تأیید بانک، سفارش شما به‌طور خودکار ثبت می‌شود.\n\n"
        f"⚠️ این فاکتور تا ۳۰ دقیقه دیگر معتبر است. اگر تا این مهلت پرداخت تایید نشود، به‌طور خودکار منقضی و حذف خواهد شد.",
        reply_markup=online_payment_keyboard(payment_link, payment_id),
    )


@router.callback_query(F.data == "cbuild_pay_card")
async def cbuild_pay_card(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    price = data.get("custom_price")
    if price is None:
        await callback.answer("❌ مشکلی پیش آمد، لطفاً دوباره از منوی سرویس‌ها شروع کنید.", show_alert=True)
        return

    invoicing_user = db.get_user(callback.from_user.id)
    invoice = db.create_invoice(
        user_id=invoicing_user["id"] if invoicing_user else None,
        telegram_id=str(callback.from_user.id),
        kind="custom_card",
        label="سرویس سفارشی",
        price=price,
    )
    deadline_str = format_deadline_time(invoice["expires_at"])
    await state.update_data(custom_card_invoice_id=invoice["id"])
    await state.set_state(UserStates.waiting_custom_card_receipt)
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "cbuild_pay_card", 
        progress_bar(2, 3) +
        f"💳 پرداخت کارت به کارت\n\n"
        f"💰 مبلغ قابل پرداخت: {price:,} تومان\n\n"
        f"💳 شماره کارت:\n{bot_info.get('card_number')}\n\n"
        f"👤 به نام: {bot_info.get('card_holder')}\n\n"
        f"📸 پس از واریز، عکس رسید پرداخت را همینجا ارسال کنید.\n\n"
        f"⏱ این شماره کارت و قیمت تا ساعت {deadline_str} (۳۰ دقیقه) معتبر است. لطفاً تا این ساعت رسید پرداخت را ارسال کنید، وگرنه این فاکتور به‌طور خودکار منقضی و حذف می‌شود."
    )
    await callback.answer()


@router.message(UserStates.waiting_custom_card_receipt, F.photo)
async def custom_receive_receipt(message: types.Message, state: FSMContext):
    uid = str(message.from_user.id)
    user = db.get_user(uid)
    data = await state.get_data()
    volume, days, price = data.get("custom_volume"), data.get("custom_days"), data.get("custom_price")
    order_type = data.get("custom_order_type", "new")
    target_config_id = data.get("custom_target_config_id")
    custom_card_invoice_id = data.get("custom_card_invoice_id")
    custom_name = data.get("custom_name")

    if user is None or volume is None or days is None or price is None:
        await message.answer("❌ مشکلی پیش آمد، لطفاً دوباره از منوی سرویس‌ها شروع کنید.", reply_markup=main_reply_keyboard())
        await state.clear()
        return

    if not custom_card_invoice_id or db.consume_invoice(custom_card_invoice_id) is None:
        await message.answer(
            "⏰ مهلت ۳۰ دقیقه‌ای پرداخت این فاکتور به پایان رسیده و به‌طور خودکار منقضی شد. لطفاً دوباره از منوی سرویس‌ها سفارش تان را ثبت کنید.",
            reply_markup=main_reply_keyboard(),
        )
        await state.clear()
        return

    order_id = db.create_custom_order(user["id"], volume, days, custom_name, price, order_type, target_config_id)
    if custom_card_invoice_id:
        db.delete_invoice(custom_card_invoice_id)


    label = "تمدید سرویس" if order_type == "renew" else "سرویس سفارشی جدید (بساز سرویس خودت)"
    await forward_admin_task_message(message.bot, ADMIN_ID, "receipts", message.chat.id, message.message_id)
    await send_admin_task_message(
        message.bot, ADMIN_ID, "receipts",
        f"💳 رسید {label}\n\n"
        f"👤 {message.from_user.full_name}\n🆔 {uid}\n"
        f"📦 حجم: {volume} گیگ\n⏳ مدت: {days} روز\n"
        + (f"🔤 نام: {custom_name}\n" if custom_name else "")
        + f"💰 {price:,} تومان\n🔢 شماره سفارش: {order_id}",
        reply_markup=admin_custom_order_card_approval_keyboard(order_id),
    )
    # 🐛 فیکس: منوی دائمی پایین صفحه‌ی کاربر را صریحاً روی همین پیام تازه می‌کنیم تا بعد از ارسال رسید
    # منوی کاربر هیچ‌وقت گم نشود.
    await message.answer(
        progress_bar(3, 3) + "✅ رسید شما ثبت شد. پس از تأیید، سفارش شما در صف ساخت و ارسال سرویس قرار می‌گیرد.",
        reply_markup=main_reply_keyboard(),
    )
    await state.clear()


@router.message(UserStates.waiting_custom_card_receipt)
async def custom_receipt_wrong_format(message: types.Message):
    await message.answer("📸 لطفاً عکس رسید پرداخت را ارسال کنید (نه متن).")

