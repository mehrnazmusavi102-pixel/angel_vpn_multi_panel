"""
handlers/wallet.py
نمایش کیف پول (موجودی آزاد / موجودی در انتظار)، تاریخچه تراکنش‌ها،
و فرایند شارژ کیف پول (انتخاب مبلغ یا مبلغ دلخواه + ارسال رسید).
"""

import html
import logging

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext

import database as db
from text_catalog import text as t
import uniquepay
import payments
import alerts
from utils import send_admin_task_message, forward_admin_task_message, is_duplicate_action, format_deadline_time, progress_bar, clean_numeric_id
from utils import send_admin_task_message, forward_admin_task_message, show_menu_with_sticker, get_main_keyboard
from states import UserStates
import bot_info


WALLET_CARD_INVOICE_DEFAULT = (
    "🟩⬜️ مرحله 2 از 2\n\n"
    "💳 شارژ کیف پول\n\n"
    "💰 مبلغ قابل پرداخت: {amount:,} تومان\n\n"
    "💳 شماره کارت:\n{card_number}\n\n"
    "👤 به نام: {card_holder}\n\n"
    "📸 پس از واریز، عکس رسید پرداخت را همینجا ارسال کنید."
)


def _render_wallet_card_invoice_text(deadline_str: str, amount: int):
    from text_catalog import text as rich_text
    import re as _re
    body = rich_text(
        "invoice_wallet_card",
        WALLET_CARD_INVOICE_DEFAULT,
        amount=amount,
        card_number=bot_info.get("card_number") or "",
        card_holder=bot_info.get("card_holder") or "",
    )
    expiry = (
        f"⏱ این شماره کارت و مبلغ تا ساعت {deadline_str} (۳۰ دقیقه) معتبر است. "
        "لطفاً تا این ساعت رسید پرداخت را ارسال کنید، وگرنه این فاکتور به‌طور خودکار منقضی و حذف می‌شود."
    )
    return body + "\n\n" + expiry


from config import (
    ADMIN_ID,
    REFERRAL_MIN_VOLUME_GB,
    UNIQUEPAY_ENABLED,
    ONLINE_PAYMENT_MIN_AMOUNT,
)
from keyboards import (
    main_reply_keyboard,
    wallet_menu,
    charge_amount_keyboard,
    charge_payment_method_keyboard,
    online_payment_wallet_keyboard,
    back_button,
    admin_charge_approval_keyboard,
)

logger = logging.getLogger(__name__)

router = Router(name="wallet")


def _get_user_row(telegram_id):
    """کاربر را برمی‌گرداند؛ اگر هنوز ساخته نشده، می‌سازد (محافظتی)."""
    user = db.get_user(telegram_id)
    if user is None:
        return None
    return user


@router.callback_query(F.data == "wallet")
async def wallet_overview(callback: types.CallbackQuery):
    user = _get_user_row(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return

    text = t("wallet_overview", wallet=user["wallet"], locked=user["locked_wallet"], min_gb=REFERRAL_MIN_VOLUME_GB)
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "wallet", text, reply_markup=wallet_menu())
    await callback.answer()


@router.callback_query(F.data == "wallet_free")
async def wallet_free(callback: types.CallbackQuery):
    user = _get_user_row(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return
    text = t("wallet_free_overview", wallet=user["wallet"])
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "wallet_free", text, reply_markup=back_button("profile"))
    await callback.answer()


@router.callback_query(F.data == "wallet_locked")
async def wallet_locked(callback: types.CallbackQuery):
    user = _get_user_row(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return
    text = t("wallet_locked_overview", locked=user["locked_wallet"], min_gb=REFERRAL_MIN_VOLUME_GB)
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "wallet_locked", text, reply_markup=back_button("profile"))
    await callback.answer()


@router.callback_query(F.data == "transactions")
async def transactions(callback: types.CallbackQuery):
    user = _get_user_row(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return

    txs = db.get_transactions(user["id"], limit=10)
    if not txs:
        text = t("transactions_empty")
    else:
        icon_map = {
            "charge": "✅",
            "purchase": "🛒",
            "referral_locked": "🔒",
            "referral_release": "🔓",
        }
        # نوع‌هایی که واقعاً واریز به حساب هستند (سبز/+)؛ بقیه خروج از حساب یا
        # در انتظار محسوب می‌شوند (قرمز/بدون علامت). این دقیقاً همان منطقی است
        # که Mini App (منطق جداگانه) استفاده می‌کند تا نمایش
        # تراکنش‌ها بین ربات و Mini App یکسان باشد.
        positive_types = ("charge", "referral_release")
        text = t("transactions_title")
        for tx in txs:
            icon = icon_map.get(tx["type"], "•")
            sign = "+" if tx["type"] in positive_types else "-"
            text += f"{icon} {tx['description']} | {sign}{tx['amount']:,} تومان | {tx['created_at']}\n"

    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "wallet_transactions", text, reply_markup=back_button("wallet"))
    await callback.answer()


# ---------------------------------------------------------------------------
# فرایند شارژ کیف پول
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "charge")
async def charge(callback: types.CallbackQuery):
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "wallet_charge", 
        t("charge_choose_amount"), reply_markup=charge_amount_keyboard()
    )
    await callback.answer()


async def _offer_charge_payment_method(target, amount: int, state: FSMContext):
    """پس از مشخص‌شدن مبلغ شارژ (چه از دکمه‌های سریع، چه مبلغ دلخواه)، اگر
    درگاه آنلاین فعال باشد و مبلغ بیشتر از ONLINE_PAYMENT_MIN_AMOUNT باشد،
    روش پرداخت را از کاربر می‌پرسد (آنلاین یا کارت‌به‌کارت)؛ در غیر این
    صورت دقیقاً مثل قبل مستقیم به مرحله‌ی کارت‌به‌کارت می‌رود."""
    if UNIQUEPAY_ENABLED and amount > ONLINE_PAYMENT_MIN_AMOUNT:
        text = f"💳 مبلغ: {amount:,} تومان\n\n💰 روش پرداخت را انتخاب کنید:"
        markup = charge_payment_method_keyboard(amount)
        if isinstance(target, types.CallbackQuery):
            await show_menu_with_sticker(target.bot, target.message.chat.id, "walletcharge_method", text, reply_markup=markup)
        else:
            await show_menu_with_sticker(target.bot, target.chat.id, "walletcharge_method", text, reply_markup=markup)
        return

    # 🐛 فیکس: این مسیر (مبلغ‌های کوچک یا وقتی درگاه آنلاین خاموش است) قبلاً هیچ‌وقت
    # فاکتور واقعی نمی‌ساخت و wallet_card_invoice_id را در state ذخیره نمی‌کرد؛ در نتیجه
    # در receive_receipt همیشه wallet_card_invoice_id خالی بود و کاربر به‌جای تأیید رسید،
    # همیشه پیام «مهلت ۳۰ دقیقه‌ای... منقضی شد» را می‌دید، حتی اگر همان لحظه رسید را می‌فرستاد.
    invoicing_user = _get_user_row(target.from_user.id)
    invoice = db.create_invoice(
        user_id=invoicing_user["id"] if invoicing_user else None,
        telegram_id=str(target.from_user.id),
        kind="wallet_card",
        label="شارژ کیف پول",
        price=amount,
    )
    deadline_str = format_deadline_time(invoice["expires_at"])
    await state.update_data(amount=amount, wallet_card_invoice_id=invoice["id"])
    await state.set_state(UserStates.waiting_charge_receipt)
    text = _render_wallet_card_invoice_text(deadline_str, amount)
    if isinstance(target, types.CallbackQuery):
        await show_menu_with_sticker(target.bot, target.message.chat.id, "walletcharge_pay_card", text, parse_mode="HTML")
    else:
        await show_menu_with_sticker(target.bot, target.chat.id, "walletcharge_pay_card", text, parse_mode="HTML")


@router.callback_query(F.data.startswith("charge_"))
async def charge_amount(callback: types.CallbackQuery, state: FSMContext):
    action = callback.data.replace("charge_", "")

    if action == "custom":
        await state.set_state(UserStates.waiting_custom_charge)
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "wallet_charge", t("charge_custom_prompt"))
    else:
        amount = int(action)
        await _offer_charge_payment_method(callback, amount, state)
    await callback.answer()


@router.message(UserStates.waiting_custom_charge)
async def custom_charge_amount(message: types.Message, state: FSMContext):
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await message.answer(t("only_number"))
        return

    amount = int(clean_numeric_id(message.text))
    await _offer_charge_payment_method(message, amount, state)


@router.callback_query(F.data.startswith("chargepay_card_"))
async def charge_pay_with_card(callback: types.CallbackQuery, state: FSMContext):
    try:
        amount = int(callback.data.replace("chargepay_card_", ""))
    except ValueError:
        await callback.answer(t("invalid_request"), show_alert=True)
        return

    invoicing_user = db.get_user(callback.from_user.id)
    invoice = db.create_invoice(
        user_id=invoicing_user["id"] if invoicing_user else None,
        telegram_id=str(callback.from_user.id),
        kind="wallet_card",
        label="شارژ کیف پول",
        price=amount,
    )
    deadline_str = format_deadline_time(invoice["expires_at"])
    await state.update_data(amount=amount, wallet_card_invoice_id=invoice["id"])
    await state.set_state(UserStates.waiting_charge_receipt)
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "walletcharge_pay_card",
        _render_wallet_card_invoice_text(deadline_str, amount),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("chargepay_online_"))
async def charge_pay_online(callback: types.CallbackQuery):
    if not UNIQUEPAY_ENABLED:
        await callback.answer(t("payment_not_active"), show_alert=True)
        return

    try:
        amount = int(callback.data.replace("chargepay_online_", ""))
    except ValueError:
        await callback.answer(t("invalid_request"), show_alert=True)
        return

    if amount <= ONLINE_PAYMENT_MIN_AMOUNT:
        await callback.answer(
            t("online_min_amount"),
            show_alert=True,
        )
        return

    if is_duplicate_action(f"onlinecharge_{callback.from_user.id}_{amount}"):
        await callback.answer(t("processing_request"), show_alert=True)
        return

    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return

    await callback.answer(t("building_payment"))

    hash_id = uniquepay.new_hash_id("charge")
    invoice = await payments.create_invoice(hash_id, amount)
    if invoice is None or not invoice.get("paymentLink"):
        await alerts.report_uniquepay_create_failure(callback.bot, ADMIN_ID)
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "walletcharge_pay_online", 
            t("online_min_amount"),
            reply_markup=charge_payment_method_keyboard(amount),
        )
        return

    alerts.report_uniquepay_create_success()

    payment_link = invoice.get("paymentLink")
    payment_id = db.create_online_payment(
        user_id=user["id"],
        telegram_id=str(callback.from_user.id),
        hash_id=hash_id,
        plan_name="شارژ کیف پول",
        price=amount,
        order_type="wallet_charge",
        kind="wallet_charge",
        payment_link=payment_link,
        ref_id=str(invoice.get("refId")),
        provider=invoice.get("provider", "uniquepay"),
    )

    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "walletcharge_pay_online", 
        progress_bar(1, 2) +
        f"🌐 پرداخت آنلاین (کارت‌به‌کارت خودکار)\n\n"
        f"💰 مبلغ قابل پرداخت: {amount:,} تومان\n\n"
        f"روی دکمه‌ی «پرداخت» بزنید، مبلغ را واریز کنید، سپس همینجا روی «بررسی کن» بزنید.\n"
        f"⏱ به‌محض تأیید بانک، کیف پول شما به‌طور خودکار شارژ می‌شود.\n\n"
        f"⚠️ این ��اکتور تا ۳۰ دقیقه دیگر معتبر است. اگر تا این مهلت پرداخت تایید نشود، به‌طور خودکار منقضی و حذف خواهد شد.",
        reply_markup=online_payment_wallet_keyboard(payment_link, payment_id),
    )


async def finalize_wallet_charge_online_payment(bot, payment: dict) -> int | None:
    """اینوویس پرداخت‌شده‌ی یونیک‌پی برای «شارژ کیف پول» را نهایی می‌کند.
    معادل finalize_online_payment در handlers/plans.py، با این تفاوت که به‌جای
    ساخت سفارش/سرویس، مستقیماً مبلغ به کیف پول کاربر اضافه می‌شود. از همان
    قفل اتمیک claim_online_payment_for_finalize استفاده می‌شود تا اگر پولر
    پس‌زمینه‌ی ربات (bot.py) و دکمه‌ی «بررسی کن» هم‌زمان صدا زده شوند، کیف
    پول فقط یک‌بار شارژ شود."""
    if payment["status"] == "paid":
        return payment["id"]

    if not db.claim_online_payment_for_finalize(payment["id"]):
        fresh = db.get_online_payment(payment["id"])
        if fresh and fresh["status"] == "paid":
            return fresh["id"]
        return None

    try:
        db.add_to_wallet(payment["user_id"], payment["price"], "شارژ کیف پول (پرداخت آنلاین)")
        db.mark_online_payment_paid(payment["id"], None)
    except Exception:
        db.set_online_payment_status(payment["id"], "pending")
        raise

    try:
        await bot.send_message(
            ADMIN_ID,
            f"💳 شارژ کیف پول (پرداخت آنلاین - یونیک‌پی)!\n\n"
            f"🆔 {payment['telegram_id']}\n"
            f"💰 {payment['price']:,} تومان",
        )
    except Exception:
        logger.exception("ارسال پیام اطلاع‌رسانی شارژ آنلاین به ادمین ناموفق بود")

    return payment["id"]


@router.message(UserStates.waiting_charge_receipt, F.photo)
async def receive_receipt(message: types.Message, state: FSMContext):
    uid = str(message.from_user.id)
    data = await state.get_data()
    amount = data.get("amount")
    wallet_card_invoice_id = data.get("wallet_card_invoice_id")

    if amount is None:
        await message.answer(progress_bar(2, 2) + t("charge_problem"), reply_markup=get_main_keyboard(message.from_user.id))
        await state.clear()
        return

    if not wallet_card_invoice_id or db.consume_invoice(wallet_card_invoice_id) is None:
        await message.answer(
            t("charge_receipt_expired"),
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        await state.clear()
        return

    user = db.get_user(uid)
    # 🐛 فیکس: receipt_id را حتماً نگه میداریم تا دکمه‌های تأیید/رد زیر همین رسید را در callback_data حمل کنند
    # (وگرنه دو رسید با همان مبلغ با هم تداخل می‌کنند و پیام «قبلاً پردازش شده» اشتباه نشان می‌دهد).
    receipt_id = None
    try:
        receipt_id = db.create_pending_receipt("charge", uid, user["id"] if user else None, "شارژ کیف پول", amount)
    except Exception:
        receipt_id = None

    if wallet_card_invoice_id:
        db.delete_invoice(wallet_card_invoice_id)

    await forward_admin_task_message(message.bot, ADMIN_ID, "receipts", message.chat.id, message.message_id)
    await send_admin_task_message(
        message.bot, ADMIN_ID, "receipts",
        f"📩 رسید شارژ\n👤 {message.from_user.full_name}\n🆔 {uid}\n💰 {amount:,} تومان",
        reply_markup=admin_charge_approval_keyboard(uid, amount, receipt_id or 0),
    )
    # 🐛 فیکس: منوی دائمی پایین صفحه‌ی کاربر را صریحاً روی همین پیام تازه می‌کنیم تا بعد از ارسال رسید شارژ
    # منوی کاربر هیچ‌وقت گم نشود.
    await message.answer(t("charge_receipt_registered"), reply_markup=get_main_keyboard(message.from_user.id))
    await state.clear()


@router.message(UserStates.waiting_charge_receipt)
async def receipt_wrong_format(message: types.Message):
    # اگر کاربر به‌جای عکس، متن فرستاد
    await message.answer(t("receipt_photo_only"))
