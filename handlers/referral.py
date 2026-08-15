"""
handlers/referral.py
نمایش لینک دعوت اختصاصی، کد اختصاصی، و آمار دعوت دوستان
(تعداد دعوت، دعوت‌های موفق، مبلغ آزاد شده، مبلغ در انتظار).
"""

from aiogram import Router, F, types

import database as db
from text_catalog import text as t
from utils import show_menu_with_sticker
import bot_info
from config import REFERRAL_LOCK_AMOUNT, REFERRAL_MIN_VOLUME_GB
from keyboards import referral_menu

router = Router(name="referral")


@router.callback_query(F.data == "referral")
async def referral(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer(t("common_start_required"), show_alert=True)
        return

    stats = db.get_referral_stats(user["id"])
    invite_link = f"https://t.me/{bot_info.get('bot_username')}?start={stats['invite_code']}"

    text = t("referral_overview", reward=REFERRAL_LOCK_AMOUNT, min_gb=REFERRAL_MIN_VOLUME_GB, invite_link=invite_link, invite_code=stats["invite_code"], invited_count=stats["invited_count"], successful_invites=stats["successful_invites"], released=stats["released_amount"], locked=user["locked_wallet"])
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "referral", text, reply_markup=referral_menu())
    await callback.answer()
