"""
handlers/profile.py
پروفایل حرفه‌ای کاربر (👤 کاربران) و تاریخچه خرید.
زیرمنوهای کیف پول آزاد/مسدود، تاریخچه تراکنش، و لینک دعوت
در فایل‌های wallet.py و referral.py پیاده شده‌اند (روی همون callback_dataها).
"""

from aiogram import Router, F, types

import database as db
from text_catalog import text as t
from utils import show_menu_with_sticker
from keyboards import profile_menu, back_button

router = Router(name="profile")


@router.callback_query(F.data == "profile")
async def profile(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return

    configs_count = len(db.get_configs(user["id"]))

    text = t("profile_overview", name=user["name"], telegram_id=user["telegram_id"], wallet=user["wallet"], locked=user["locked_wallet"], configs_count=configs_count, total_purchase=user["total_purchase"], joined=user["joined"], invited_count=user["invited_count"], successful_invites=user["successful_invites"])
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "profile", text, reply_markup=profile_menu())
    await callback.answer()


@router.callback_query(F.data == "purchase_history")
async def purchase_history(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return

    txs = db.get_transactions(user["id"], limit=50)
    purchases = [tx for tx in txs if tx["type"] == "purchase"]

    if not purchases:
        text = t("purchase_history_empty")
    else:
        text = t("purchase_history_title")
        for tx in purchases[:15]:
            text += f"📦 {tx['description']} | {tx['amount']:,} تومان | {tx['created_at']}\n"

    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "purchase_history", text, reply_markup=back_button("profile"))
    await callback.answer()
