"""
states.py
تمام Stateهای FSM ربات اینجا تعریف می‌شوند تا در همه‌ی handlerها
قابل import و استفاده باشند.
"""

from aiogram.fsm.state import State, StatesGroup


class UserStates(StatesGroup):
    waiting_custom_charge = State()
    waiting_charge_receipt = State()
    waiting_ticket_message = State()
    waiting_agency_request_message = State()  # درخواست نمایندگی (دکمه‌ی منوی پایین)
    waiting_broadcast = State()
    waiting_config = State()
    waiting_discount_code = State()
    waiting_discount_plan = State()          # کد تخفیف وارد شده هنگام خرید یک پلن خاص
    waiting_card_purchase_receipt = State()  # رسید پرداخت کارت‌به‌کارت برای خرید سرویس
    # 🛠 ساخت سرویس سفارشی / تمدید با حجم و مدت دلخواه
    waiting_custom_volume = State()
    waiting_custom_days = State()
    waiting_custom_name = State()
    waiting_custom_card_receipt = State()


class AdminStates(StatesGroup):
    waiting_custom_amount = State()
    # ℹ️ اطلاعات ربات — مقدار جدید یک فیلد (کدام فیلد در FSM data ذخیره می‌شود)
    waiting_botinfo_value = State()
    waiting_botinfo_channel_add = State()  # افزودن یک کانال اجباری جدید
    waiting_free_test_settings = State()  # تنظیم حجم (مگابایت)/روز پلن «تست رایگان» از پنل ادمین
    waiting_custom_build_settings = State()  # تنظیم قیمت/محدوده ساخت سرویس سفارشی

    waiting_search_user = State()
    waiting_text_override_value = State()
    waiting_config_text = State()
    waiting_discount_code_step = State()
    waiting_discount_percent_step = State()
    waiting_discount_uses_step = State()
    waiting_discount_value_step = State()   # مقدار تخفیف (درصد یا مبلغ ثابت)
    waiting_discount_plans_step = State()   # پلن‌های قابل‌اعمال (all یا لیست plan_key با کاما)
    waiting_discount_users_step = State()   # آیدی‌های عددی مجاز به استفاده (خالی/۰ یعنی همه)

    # ✏️ ویرایش یک کد تخفیف موجود (از صفحه‌ی جزئیات کد)
    waiting_discount_edit_value = State()   # ویرایش درصد/مبلغ
    waiting_discount_edit_uses = State()    # ویرایش تعداد استفاده‌ی باقی‌مانده
    waiting_discount_edit_users = State()   # ویرایش آیدی‌های مجاز
    waiting_discount_edit_min_order = State()   # ویرایش حداقل مبلغ سفارش
    waiting_discount_edit_max_per_user = State()  # ویرایش سقف استفاده‌ی هر کاربر
    waiting_discount_edit_expiry = State()  # ویرایش تاریخ انقضا

    # نمایندگی (تخفیف خودکار روی VIP برای یک آیدی عددی خاص)
    waiting_agent_id_step = State()
    waiting_agent_percent_step = State()
    waiting_agent_edit_percent = State()  # تغییر درصد تخفیف یک نماینده‌ی موجود (از داخل صفحه‌ی نماینده)

    # ویرایش نام/قیمت پلن‌های VIP از پنل ادمین
    waiting_plan_edit_name = State()
    waiting_plan_edit_price = State()

    # 🗂 دسته‌بندی‌های VIP (بخش ۶) — افزودن دسته‌ی جدید و افزودن/ویرایش پلن داخل هر دسته
    waiting_vip_category_name = State()
    waiting_vip_plan_name = State()   # مرحله‌ی ۱ از ۵ افزودن پلن جدید
    waiting_vip_plan_price = State()  # مرحله‌ی ۲ از ۵
    waiting_vip_plan_gb = State()     # مرحله‌ی ۳ از ۵
    waiting_vip_plan_days = State()   # مرحله‌ی ۴ از ۵
    waiting_vip_plan_userlimit = State()  # مرحله‌ی ۵ از ۵ — سقف کاربر همزمان (HWID Limit)، ۰ تا ۱۰ (0=نامحدود)
    waiting_vip_plan_edit_name = State()
    waiting_vip_plan_edit_price = State()
    waiting_vip_plan_edit_gb = State()
    waiting_vip_plan_edit_days = State()
    waiting_vip_plan_edit_userlimit = State()

    # ارسال کانفیگ VIP با کیوآرکد + لینک ساب (mirroring خودکار)
    waiting_send_qr_photo = State()
    waiting_send_qr_link = State()
    waiting_send_qr_manual = State()  # فقط اگر تشخیص خودکار از روی لینک شکست بخورد

    # مدیریت سرویس‌های کاربران توسط ادمین (ادیت لینک ساب / کیوآرکد / افزودن فایل )
    waiting_edit_sublink = State()
    waiting_edit_qr = State()

    # 🔗 اتصال پنل مرزبان — فقط برای وقتی که تشخیص خودکار لینک ساب از پاسخ
    # create/renew ممکن نشود و لازم باشد ادمین یک‌بار دستی لینک را وارد کند.
    waiting_marzban_manual_link = State()

    # 🆕 تمدید داینامیک یک سرویس (بدون انتخاب تمپلیت): ادمین حجم (گیگ) و بعد تعداد روز را مستقیماً تایپ می‌کند
    waiting_marzban_renew_volume = State()
    waiting_marzban_renew_days = State()

    # ✉️ پیام خصوصی ادمین به یک کاربر خاص (از بخش مدیریت کاربر/جستجوی)
    waiting_pm_message = State()

    # ↩️ پاسخ ادمین به یک تیکت پشتیبانی (state جدا از UserStates.waiting_ticket_message
    # تا اگر ادمین خودش هم یک تیکت عادی بزند، با این حالت قاطی نشود)
    waiting_ticket_reply = State()

    # 📚 مدیریت راهنما — افزودن/ویرایش محتوای هر قطعه راهنما (متن/عکس/فیلم)
    waiting_guide_title = State()
    waiting_guide_content = State()
    waiting_guide_edit_title = State()
    waiting_guide_edit_content = State()

    # 🎬 مدیریت استیکر/ویدیوی تستی هر بخش از منو (تست رایگان/خرید اشتراک/
    # انتخاب پلن/بساز کانفیگ)
    waiting_sticker_upload = State()

    # 🖥️ مدیریت چندنمونه‌ای پنل‌های VPN
    waiting_panel_name = State()
    waiting_panel_base_url = State()
    waiting_panel_username = State()
    waiting_panel_password = State()
    waiting_panel_api_key = State()
    waiting_panel_edit_value = State()
    # 👮 مدیریت ادمین‌های فرعی
    waiting_sub_admin_id = State()
