from collections import OrderedDict
import database as db

# All user-facing editable texts. Values containing {placeholders} are templates.
TEXT_CATEGORIES = OrderedDict({
    '🏠 منوی اصلی': [
        ('main_buy', '🛒 خرید اشتراک'),
        ('main_free_test', '🎁 تست رایگان'),
        ('main_configs', '📱 سرویس\u200cهای من'),
        ('main_wallet', '💰 کیف پول'),
        ('main_referral', '👥 دعوت دوستان و کسب درآمد'),
        ('main_profile', '👤 پروفایل من'),
        ('main_support', '👨\u200d💻 پشتیبانی'),
        ('main_guides', '📚 راهنما'),
        ('main_agency', '🤝 درخواست نمایندگی'),
    ],
    '👤 پروفایل من': [
        ('profile_overview', '👤 پروفایل حرفه\u200cای شما\n\n📛 نام: {name}\n🆔 آیدی: {telegram_id}\n\n👛 موجودی قابل استفاده: {wallet:,} تومان\n🔒 موجودی در انتظار: {locked:,} تومان\n\n📦 تعداد سرویس: {configs_count}\n🛒 کل خرید: {total_purchase:,} تومان\n📅 تاریخ عضویت: {joined}\n\n👥 تعداد دعوت: {invited_count} | دعوت موفق: {successful_invites}'),
        ('profile_free_wallet', '💰 کیف پول آزاد'),
        ('profile_locked_wallet', '🔒 کیف پول مسدود'),
        ('profile_history', '🛒 تاریخچه خرید'),
        ('profile_transactions', '📋 تاریخچه تراکنش'),
        ('profile_referral', '🔗 لینک دعوت اختصاصی'),
        ('profile_back', '🏠 بازگشت به منوی اصلی'),
        ('purchase_history_empty', '🛒 شما هنوز خریدی انجام نداده\u200cاید.'),
        ('purchase_history_title', '🛒 تاریخچه خرید شما:\n\n'),
    ],
    '💰 کیف پول': [
        ('wallet_overview', '💰 کیف پول شما\n\n👛 موجودی قابل استفاده: {wallet:,} تومان\n🔒 موجودی در انتظار: {locked:,} تومان\n\nℹ️ موجودی در انتظار، پس از خرید حجم {min_gb} گیگ یا بیشتر توسط فردی که با لینک شما عضو شده، به\u200cصورت خودکار آزاد می\u200cشود.'),
        ('wallet_free_overview', '💰 موجودی قابل استفاده شما\n\n{wallet:,} تومان\n\nاین مبلغ را می\u200cتوانید برای خرید سرویس استفاده کنید.'),
        ('wallet_locked_overview', '🔒 موجودی در انتظار شما\n\n{locked:,} تومان\n\nاین مبلغ از دعوت دوستان به\u200cدست آمده و پس از خرید حجم {min_gb} گیگ یا بیشتر توسط آن\u200cها، به\u200cصورت خودکار به موجودی قابل\u200cاستفاده شما اضافه می\u200cشود.'),
        ('wallet_charge', '💳 شارژ کیف پول'),
        ('wallet_discount', '🎟 ثبت کد تخفیف'),
        ('wallet_transactions', '📋 تراکنش\u200cهای من'),
        ('wallet_back', '🏠 بازگشت به منوی اصلی'),
        ('transactions_empty', '📋 هنوز تراکنشی ندارید.'),
        ('transactions_title', '📋 تراکنش\u200cهای اخیر:\n\n'),
        ('back', '🔙 بازگشت'),
        ('main_back', '🏠 بازگشت به منوی اصلی'),
    ],
    '💳 شارژ کیف پول': [
        ('charge_choose_amount', '💳 مبلغ شارژ را انتخاب کنید:'),
        ('charge_50000', '💰 ۵۰,۰۰۰ تومان'),
        ('charge_100000', '💰 ۱۰۰,۰۰۰ تومان'),
        ('charge_200000', '💰 ۲۰۰,۰۰۰ تومان'),
        ('charge_custom', '💵 مبلغ دلخواه'),
        ('charge_custom_prompt', '💵 مبلغ دلخواه را به تومان ارسال کنید:'),
        ('only_number', '❌ فقط عدد ارسال کنید.'),
        ('online_min_amount', '❌ برای مبالغ ۵۰ هزار تومان و کمتر امکان استفاده از درگاه پرداخت آنلاین نیست. لطفاً از کارت\u200cبه\u200cکارت یا کیف پول استفاده کنید.'),
        ('wallet_pay_online', '🌐 پرداخت آنلاین (تایید خودکار)'),
        ('wallet_pay_card', '💳 پرداخت کارت به کارت'),
        ('wallet_online_pay', '💳 پرداخت (کارت به کارت خودکار)'),
        ('wallet_check_pay', '✅ پرداخت را انجام دادم / بررسی کن'),
        ('wallet_cancel', '🔙 انصراف'),
        ('charge_problem', '❌ مشکلی پیش آمد، لطفاً دوباره از منوی شارژ شروع کنید.'),
        ('charge_receipt_expired', '⏰ مهلت ۳۰ دقیقه\u200cای پرداخت این فاکتور به پایان رسیده و به\u200cطور خودکار منقضی شد. لطفاً دوباره از منوی شارژ شروع کنید.'),
        ('charge_receipt_registered', '✅ رسید ثبت شد. پس از تأیید ادمین، کیف پول شما شارژ می\u200cشود.'),
        ('invoice_wallet_card', '🟩⬜️ مرحله 2 از 2\n\n💳 شارژ کیف پول\n\n💰 مبلغ قابل پرداخت: {amount:,} تومان\n\n💳 شماره کارت:\n{card_number}\n\n👤 به نام: {card_holder}\n\n📸 پس از واریز، عکس رسید پرداخت را همینجا ارسال کنید.'),
    ],
    '🛒 خرید اشتراک': [
        ('plans_intro', '🛒 *خرید اشتراک*\n\n🚀 *سرور VIP (V2Ray)*\nفیلترشکن پرسرعت و پایدار؛ مناسب وب\u200cگردی با IP ثابت.\n✅ حتی در «اینترنت ملی» بدون قطعی\n\nلطفاً سرویس مورد نظر خود را از منوی زیر انتخاب کنید 👇'),
        ('vip_intro', '🚀 سرویس\u200cهای VIP (V2Ray)\n\nیکی از دسته\u200cها را انتخاب کنید 👇'),
        ('free_test_page', '🎁 {plan_name}\n💰 قیمت: {price:,} تومان\n👛 موجودی کیف پول شما: {wallet:,} تومان\n\nروش پرداخت را انتخاب کنید:'),
        ('plan_payment_page', '🛒 {plan_name}\n💰 قیمت: {price:,} تومان{note}\n👛 موجودی کیف پول شما: {wallet:,} تومان\n\nروش پرداخت را انتخاب کنید:'),
        ('wallet_purchase_success', '✅ پرداخت شما ثبت شد. سفارش شما در صف ارسال سرویس قرار گرفت.'),
        ('online_plan_invoice', '🌐 پرداخت آنلاین (کارت\u200cبه\u200cکارت خودکار)\n\n🛒 {plan_name}\n💰 مبلغ قابل پرداخت: {amount:,} تومان\n\nروی دکمه\u200cی «پرداخت» بزنید، مبلغ را واریز کنید، سپس همینجا روی «بررسی کن» بزنید.\n⏱ به\u200cمحض تأیید بانک، سفارش شما به\u200cطور خودکار ثبت می\u200cشود.\n\n⚠️ این فاکتور تا ۳۰ دقیقه دیگر معتبر است. اگر تا این مهلت پرداخت تایید نشود، به\u200cطور خودکار منقضی و حذف خواهد شد.'),
        ('card_receipt_registered', '✅ رسید شما ثبت شد. پس از تأیید، سفارش شما در صف ارسال سرویس قرار می\u200cگیرد.'),
        ('plans_back', '🔙 بازگشت'),
        ('vip_category_empty', '😔 فعلاً هیچ دسته\u200cای موجود نیست'),
        ('vip_category_back', '🔙 بازگشت'),
        ('vip_plans_empty', '😔 فعلاً هیچ پلنی در این دسته نیست'),
        ('vip_plans_back', '🔙 بازگشت به دسته\u200cبندی\u200cها'),
        ('pay_wallet', '👛 پرداخت از کیف پول'),
        ('pay_online', '🌐 پرداخت آنلاین (تایید خودکار)'),
        ('pay_card', '💳 پرداخت کارت به کارت'),
        ('pay_discount', '🎟 ثبت کد تخفیف'),
        ('pay_back', '🔙 بازگشت'),
        ('online_pay', '💳 پرداخت (کارت به کارت خودکار)'),
        ('online_check', '✅ پرداخت را انجام دادم / بررسی کن'),
        ('online_cancel', '🔙 انصراف'),
        ('insufficient_charge', '💵 شارژ کیف پول'),
        ('insufficient_back', '🔙 بازگشت'),
        ('common_start_required', 'ابتدا دستور /start را بزنید.'),
        ('orders_closed', '🔴 ربات به دلیل حجم سفارشات بالا موقتاً بسته می\u200cباشد.'),
        ('plan_not_found', '❌ این پلن یافت نشد.'),
        ('category_not_found', '❌ این دسته یافت نشد.'),
        ('processing_request', '⚠️ این درخواست در حال پردازش/ثبت\u200cشده است.'),
        ('invalid_request', '❌ درخواست نامعتبر است.'),
        ('payment_not_active', 'این روش پرداخت در حال حاضر فعال نیست.'),
        ('building_payment', '⏳ در حال ساخت لینک پرداخت...'),
        ('checking_payment', '⏳ در حال بررسی وضعیت پرداخت...'),
        ('payment_owned', '⛔️ این پرداخت متعلق به شما نیست.'),
        ('payment_already_confirmed', '✅ این پرداخت قبلاً تأیید شده است.'),
        ('payment_problem', '❌ مشکلی پیش آمد، لطفاً دوباره از منوی سرویس\u200cها شروع کنید.'),
        ('invoice_expired_wait', '⏰ مهلت ۳۰ دقیقه\u200cای پرداخت این فاکتور به پایان رسیده و به\u200cطور خودکار منقضی شد. لطفاً دوباره از منوی سرویس\u200cها سفارش تان را ثبت کنید.'),
        ('receipt_photo_only', '📸 لطفاً عکس رسید پرداخت را ارسال کنید (نه متن).'),
        ('receipt_registered', '✅ رسید ثبت شد. پس از تأیید ادمین، نتیجه به شما اطلاع داده می\u200cشود.'),
        ('invoice_plan_card', '🟩🟩⬜️ مرحله 2 از 3\n\n💳 پرداخت کارت به کارت\n\n🛒 {plan_name}\n💰 مبلغ قابل پرداخت: {amount:,} تومان\n\n💳 شماره کارت:\n{card_number}\n\n👤 به نام: {card_holder}\n\n📸 پس از واریز، عکس رسید پرداخت را همینجا ارسال کنید.'),
        ('free_test_processing', '⚠️ این درخواست در حال پردازش/ثبت\u200cشده است.'),
        ('free_test_payment', 'روش پرداخت را انتخاب کنید:'),
        ('free_test_soon', '🎁 تست رایگان به\u200cزودی فعال می\u200cشود! منتظر باشید.'),
    ],
    '🎟 کد تخفیف': [
        ('discount_enter', '🎟 کد تخفیف خود را وارد کنید:'),
        ('discount_cancel', '🔙 انصراف'),
        ('discount_prompt', '🎟 کد تخفیف خود را وارد کنید:'),
        ('discount_invalid', '❌ کد تخفیف نامعتبر یا تمام شده.'),
        ('discount_forbidden', '❌ شما مجاز به استفاده از این کد تخفیف نیستید.'),
        ('discount_limit', '❌ سهمیه\u200cی استفاده\u200cی شما از این کد تمام شده.'),
        ('discount_fixed_note', '💡 این کد یک کد تخفیف با مبلغ ثابت است؛ لطفاً از منوی «🛒 خرید اشتراک» پلن مورد نظرتان را انتخاب کنید و در صفحه\u200cی پرداخت همان پلن، کد را وارد کنید.'),
        ('discount_success', '✅ کد تخفیف {percent}٪ با موفقیت ثبت شد و در خرید بعدی شما (در صورت تطابق پلن) اعمال می\u200cشود.{plans_note}'),
        ('invalid_discount', '❌ کد تخفیف نامعتبر است.'),
        ('free_test_used', '⚠️ شما قبلاً از «تست رایگان» استفاده کرده\u200cاید. هر کاربر فقط یک\u200cبار می\u200cتواند این پلن را دریافت کند.'),
        ('wallet_insufficient', '❌ موجودی کیف پول کافی نیست!\n\n💰 قیمت: {price:,} تومان\n👛 موجودی: {wallet:,} تومان\n⚠️ کمبود: {needed:,} تومان'),
        ('wallet_not_enough', '❌ موجودی کافی نیست. ممکن است موجودی شما تغییر کرده باشد.'),
        ('purchase_receipt_error', '❌ مشکلی پیش آمد، لطفاً دوباره از منوی سرویس\u200cها شروع کنید.'),
        ('plan_action_error', '❌ مشکلی پیش آمد، دوباره از منوی سرویس\u200cها شروع کنید.'),
        ('request_processing_short', '⚠️ این درخواست در حال پردازش/ثبت\u200cشده است.'),
    ],
    '📦 سرویس\u200cهای من': [
        ('configs_empty', '📱 شما هنوز هیچ سرویسی خریداری نکرده\u200cاید.\n\nبرای خرید، از «🛒 خرید اشتراک» اقدام کنید.'),
        ('configs_has', '📱 سرویس\u200cهای شما\n\nکدوم دسته رو می\u200cخوای ببینی؟ 👇'),
        ('my_configs_empty', '📱 شما هنوز هیچ سرویسی خریداری نکرده\u200cاید.\n\nبرای خرید، از «🛒 خرید اشتراک» اقدام کنید.'),
        ('my_configs_has', '📱 سرویس\u200cهای شما\n\nکدوم دسته رو می\u200cخوای ببینی؟ 👇'),
        ('vip_configs_empty', '🚀 شما هنوز هیچ سرویس VIPی خریداری نکرده\u200cاید.'),
        ('vip_configs_has', '🚀 سرویس\u200cهای VIP شما\n\nبرای مشاهده\u200cی لینک سابسکریپشن و مدیریت هرکدام، روی نام آن بزنید 👇'),
        ('config_detail_error', '❌ خطا در نمایش جزئیات سرویس. لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.'),
        ('config_status_title', '📊 وضعیت مصرف (لحظه\u200cای):'),
        ('config_total', '   • حجم کل: {value}'),
        ('config_used', '   • مصرف\u200cشده: {value}'),
        ('config_remaining', '   • باقی\u200cمانده: {value}'),
        ('config_percent', '{bar} {percent}٪ مصرف شده'),
        ('config_expiry', '⏰ تاریخ انقضا: {value}'),
        ('config_expired', '⛔️ منقضی شده'),
        ('config_days_left', '⌛️ زمان باقی\u200cمانده: {days} روز'),
        ('config_subscription_help', '🔗 این لینک ساب (Subscription) شماست؛ می\u200cتوانید کانفیگ\u200cهای خودتان را از داخل آن بردارید و حجم مصرفی\u200cتان را مدیریت کنید:'),
        ('config_purchase_date', '📆 تاریخ خرید: {date}'),
        ('config_delivery_error', '❌ ارسال یکی از کانفیگ\u200cها با خطا مواجه شد. لطفاً با پشتیبانی تماس بگیرید.'),
        ('configs_vip', '🚀 سرویس\u200cهای VIP من'),
        ('configs_back', '🏠 بازگشت به منوی اصلی'),
        ('config_qr', '🖼 مشاهده کیوآرکد'),
        ('config_sub', '🔗 باز کردن لینک ساب'),
        ('config_mirror', '🔗 دریافت کانفیگ\u200cهای تکی'),
        ('config_back', '🔙 بازگشت به سرویس\u200cهای VIP من'),
        ('service_not_found', '❌ سرویس یافت نشد.'),
        ('service_not_owned', '❌ این سرویس متعلق به شما نیست یا یافت نشد.'),
        ('config_decode_error', '❌ خطا در رمزگشایی کانفیگ.'),
        ('subscription_missing', '❌ لینک ساب معتبری برای این سرویس ثبت نشده.'),
        ('service_loading', '⏳ در حال دریافت اطلاعات مصرف...'),
        ('subscription_fetching', '⏳ در حال دریافت کانفیگ\u200cها از روی لینک ساب...'),
        ('subscription_unavailable', '❌ لینک ساب در حال حاضر در دسترس نیست. کمی بعد دوباره امتحان کنید.'),
        ('qr_missing', '❌ کیوآرکدی برای این سرویس ثبت نشده.'),
        ('qr_failed', '❌ ارسال کیوآرکد ناموفق بود.'),
    ],
    '⚙️ عملیات سرویس': [
        ('config_enable', '▶️ فعال\u200cسازی سرویس'),
        ('config_disable', '⏸ غیرفعال\u200cسازی سرویس'),
        ('config_revoke', '🔄 ساخت لینک ساب جدید'),
        ('config_delete', '🗑 حذف سرویس'),
        ('confirm_delete_yes', '✅ بله، حذف کن'),
        ('confirm_delete_no', '❌ انصراف'),
        ('confirm_disable_yes', '✅ بله، غیرفعال کن'),
        ('confirm_disable_no', '❌ انصراف'),
        ('confirm_revoke_yes', '✅ بله، لینک جدید بساز'),
        ('confirm_revoke_no', '❌ انصراف'),
        ('config_disabled', '❌ این سرویس متعلق به شما نیست یا امکان غیرفعال\u200cسازی خودکار ندارد.'),
        ('config_disabling', '⏳ در حال غیرفعال\u200cسازی...'),
        ('config_enabling', '⏳ در حال فعال\u200cسازی...'),
        ('config_revoking', '⏳ در حال ساخت لینک ساب جدید...'),
        ('service_delete_confirm', '⚠️ مطمئنی می\u200cخوای «{plan}» رو حذف کنی؟\n\nاین سرویس از لیست «سرویس\u200cهای من» شما پاک می\u200cشه (ولی اطلاعاتش نزد پشتیبانی می\u200cمونه).'),
        ('service_deleted', '✅ سرویس حذف شد.'),
        ('service_disable_not_available', '❌ این سرویس متعلق به شما نیست یا امکان غیرفعال\u200cسازی خودکار ندارد.'),
        ('service_disable_confirm', '⚠️ مطمئنی می\u200cخوای سرویس رو غیرفعال کنی؟\n\nبعد از غیرفعال\u200cسازی، این سرویس دیگه وصل نمی\u200cشه تا دوباره فعالش کنی.'),
        ('service_disable_failed', '❌ غیرفعال\u200cسازی ناموفق بود: {msg}'),
        ('service_disabled', '🚫 سرویس غیرفعال شد.'),
        ('service_enable_failed', '❌ فعال\u200cسازی ناموفق بود: {msg}'),
        ('service_enabled', '✅ سرویس دوباره فعال شد.'),
        ('service_revoke_not_available', '❌ این سرویس متعلق به شما نیست یا امکان تعویض خودکار لینک ندارد.'),
        ('service_revoke_confirm', '⚠️ مطمئنی می\u200cخوای لینک ساب عوض شه؟\n\nبعد از تعویض، لینک قبلی دیگه کار نمی\u200cکنه و باید لینک جدید رو دوباره داخل اپ خودت وارد کنی.'),
        ('service_revoke_failed', '❌ ساخت لینک ساب جدید ناموفق بود: {msg}'),
        ('service_revoke_missing', '⚠️ لینک ساب جدید در پاسخ پنل پیدا نشد. با پشتیبانی تماس بگیر.'),
        ('service_revoke_done', '✅ لینک ساب جدید ساخته شد؛ برای دیدنش وارد جزئیات سرویس شو.'),
        ('config_back_service', '🔙 بازگشت به سرویس'),
    ],
    '👥 دعوت دوستان': [
        ('referral_overview', '👥 دعوت دوستان و کسب درآمد 💸\n\nدوستانتو دعوت کن و به\u200cازای هر دعوت موفق، {reward:,} تومان پاداش نقدی بگیر! 🎁\nکافیه لینک اختصاصی\u200cت رو برای دوستات، گروه\u200cها یا کانال\u200cهایی که توشون عضوی بفرستی.\n\n🔗 لینک اختصاصی شما:\n{invite_link}\n\n🔑 کد اختصاصی: {invite_code}\n\n👤 تعداد دعوت: {invited_count}\n✅ دعوت\u200cهای موفق: {successful_invites}\n🔓 مبلغ آزاد شده: {released:,} تومان\n🔒 مبلغ در انتظار: {locked:,} تومان\n\nℹ️ به\u200cازای هر دوستی که با لینک شما عضو شود و یک خرید حجم {min_gb} گیگ یا بیشتر انجام دهد، {reward:,} تومان به\u200cصورت خودکار و بدون نیاز به هیچ اقدام دیگری به کیف پول شما آزاد می\u200cشود. (تست رایگان و خریدهای کمتر از {min_gb} گیگ پاداش را آزاد نمی\u200cکنند)\n\n⚠️ لطفاً فقط لینک را برای افراد واقعی ارسال کنید؛ استفاده از اکانت\u200cهای فیک تقلب محسوب شده و جایزه شما لغو می\u200cشود.'),
        ('referral_back', '🏠 بازگشت به منوی اصلی'),
    ],
    '👨\u200d💻 پشتیبانی و نمایندگی': [
        ('support_intro', '👨\u200d💻 پشتیبانی\n\nمی\u200cتونی مستقیم تیکت بزنی یا از کانال اصلی و پشتیبان استفاده کنی 👇'),
        ('support_ticket', '🎫 ارسال تیکت'),
        ('support_channels', '📢 کانال اصلی و پشتیبان'),
        ('support_back', '🏠 بازگشت به منوی اصلی'),
        ('support_error', '❌ خطایی در نمایش منوی پشتیبانی پیش آمد (احتمالاً لینک پشتیبانی در تنظیمات نامعتبر است). لطفاً دوباره تلاش کنید یا به ادمین اطلاع بدهید.'),
        ('ticket_write', '✍️ پیام خود را برای پشتیبانی بنویسید:'),
        ('ticket_sent', '✅ پیام شما برای پشتیبانی ارسال شد. به\u200cزودی پاسخ داده می\u200cشود.'),
        ('ticket_reply_sent', '✅ پاسخ ارسال شد.'),
        ('ticket_reply_failed', '❌ ارسال پاسخ ناموفق بود (شاید کاربر ربات را بلاک کرده).'),
        ('agency_intro', '🤝 درخواست نمایندگی\n\nدرخواست و مشخصات خودتون (اسم، شماره تماس، میزان فعالیت/تعداد مشتری تقریبی و توضیحات) رو در یک پیام بنویسید و ارسال کنید؛ مستقیم برای پشتیبانی فرستاده می\u200cشه و به\u200cزودی بررسی و پاسخ داده می\u200cشه 👇'),
        ('agency_cancel', '🔙 انصراف'),
        ('agency_invalid', '❌ لطفاً درخواستتون رو به\u200cصورت متن ارسال کنید:'),
        ('agency_sent', '✅ درخواست شما برای پشتیبانی ارسال شد. به\u200cزودی بررسی و باهاتون تماس گرفته می\u200cشه.'),
    ],
    '📚 راهنما': [
        ('guides_back', '🏠 بازگشت به منوی اصلی'),
        ('guide_detail_back', '🔙 بازگشت به لیست راهنما'),
        ('guide_missing', '❌ این راهنما دیگر موجود نیست.'),
        ('guides_empty', '📚 راهنما و اموزش\u200cها\n\nهنوز هیچ راهنمایی ثبت نشده. به\u200cزودی محتوای آموزشی اینجا قرار می\u200cگیرد.'),
        ('guides_intro', '📚 راهنما و اموزش\u200cها\n\nیکی از موارد زیر را برای مشاهده انتخاب کنید 👇'),
    ],
    '🚀 شروع و عضویت اجباری': [
        ('join_confirm', '✅ عضو شدم'),
        ('start_join_required', '⚠️ برای استفاده از ربات ابتدا در کانال\u200cهای زیر عضو شوید:'),
        ('start_blocked', '🚫 دسترسی شما به ربات مسدود شده است. در صورت وجود ابهام با پشتیبانی در ارتباط باشید.'),
        ('start_admin_welcome', '👨\u200d💻 به پنل مدیریت خوش آمدید!\n\nهمه\u200cی امکانات مدیریتی از منوی پایین صفحه قابل دسترسی است ✅'),
        ('start_join_not_done', '❌ هنوز در همه کانال\u200cها عضو نشدید!'),
        ('start_blocked_short', '🚫 دسترسی شما به ربات مسدود شده است.'),
        ('start_join_confirmed', 'منوی اصلی در پایین صفحه فعال شد ✅'),
        ('start_back_admin', '👨\u200d💻 بازگشت به منوی اصلی — از منوی پایین صفحه ادامه دهید ✅'),
        ('start_back_user', '👋 بازگشت به منوی اصلی — از منوی پایین صفحه ادامه دهید ✅'),
    ],
    '🔔 اعلان\u200cها': [
        ('notif_wallet_charge_approved', '✅ شارژ {amount:,} تومانی شما تأیید شد.'),
        ('notif_wallet_charged', '✅ کیف پول شما {amount:,} تومان شارژ شد.'),
        ('notif_purchase_approved', '✅ پرداخت شما تأیید شد!\n\n📦 {plan_name}\nسرویس شما به\u200cزودی ارسال می\u200cشود.{discount_note}'),
        ('notif_receipt_rejected_short', '❌ متأسفانه رسید شما تأیید نشد. با پشتیبانی تماس بگیرید.'),
        ('notif_free_test_used_admin', '⚠️ این کاربر قبلاً از «تست رایگان» استفاده کرده؛ هر کاربر فقط یک\u200cبار می\u200cتواند این پلن را بگیرد.'),
        ('notif_service_delivery', '📦 سرویس شما آماده شد ⬇️'),
        ('notif_receipt_approved', '✅ رسید پرداخت شما تأیید شد.'),
        ('notif_receipt_rejected', '❌ متأسفانه رسید پرداخت شما تأیید نشد. با پشتیبانی تماس بگیرید.'),
        ('notif_usage_80', '🔔 هشدار حجم مصرفی سرویس\n\n📦 {plan}\n\n{bar}\n✅ شما تا الان {percent}٪ از حجم سرویستون رو مصرف کردید.\n\nبرای جلوگیری از قطعی سرویس، پیشنهاد می\u200cکنیم همین الان تمدید کنید 🔁'),
        ('notif_usage_90', '🔔 هشدار حجم مصرفی سرویس\n\n📦 {plan}\n\n{bar}\n⚠️ شما تا الان {percent}٪ از حجم سرویستون رو مصرف کردید.\n\nبرای جلوگیری از قطعی سرویس، پیشنهاد می\u200cکنیم همین الان تمدید کنید 🔁'),
        ('notif_expiry', '⏰ هشدار پایان سرویس\n\n📦 {plan}\n\n🟨 سرویس شما {days_text}!\n\nبرای جلوگیری از قطعی، همین الان تمدید کنید 🔁'),
        ('notif_orders_closed_suffix', 'روشن شدن دوباره\u200cی آن اطلاع\u200cرسانی خواهد شد.'),
        ('notif_orders_opened_suffix', 'با زدن /start می\u200cتوانید دوباره سفارش ثبت کنید.'),
        ('notif_view_service', '📦 مشاهده سرویس'),
    ],
})


# 🛠 متن‌های بخش «بساز سرویس خودت» — قابل ویرایش از همان پنل مدیریت متن
TEXT_CATEGORIES.setdefault("custom_build", []).extend([
    ("plans_vip_button", "🚀 سرور VIP (V2Ray)"),
    ("plans_custom_button", "🛠 سرویس خودت رو بساز"),
    ("custom_pay_wallet", "👛 پرداخت از کیف پول"),
    ("custom_pay_online", "🌐 پرداخت آنلاین (تایید خودکار)"),
    ("custom_pay_card", "💳 پرداخت کارت به کارت"),
    ("custom_cancel", "🔙 انصراف"),
    ("admin_custom_approve", "✅ تأیید پرداخت"),
    ("admin_custom_reject", "❌ رد رسید"),
    ("admin_custom_send_manual", "📤 شروع ارسال کانفیگ — دستی"),
    ("custom_build_title", "🛠 سرویس خودت رو بساز"),
    ("custom_build_volume_prompt", "📦 حجم موردنظر را به گیگابایت ارسال کنید:"),
    ("custom_build_days_prompt", "⏳ مدت سرویس را به روز ارسال کنید:"),
    ("custom_build_name_prompt", "🔤 یک نام انگلیسی برای سرویس ارسال کنید:"),
    ("custom_build_summary", "🧾 خلاصه سفارش"),
    ("custom_payment_approved", "✅ پرداخت شما تأیید شد!\nسرویس شما به‌زودی ساخته و ارسال می‌شود."),
])

TEXTS = {key: default for items in TEXT_CATEGORIES.values() for key, default in items}
CATEGORY_BY_KEY = {key: category for category, items in TEXT_CATEGORIES.items() for key, _ in items}
_CACHE = {}


class RichText(str):
    """رشته‌ای که entityهای واقعی تلگرام را همراه خودش حمل می‌کند.

    برای متن‌های قابل شخصی‌سازی، این امکان باعث می‌شود Custom/Premium Emoji،
    Bold، Italic، لینک و سایر entityها بعد از ذخیره در پنل ادمین هنگام ارسال
    دوباره به Telegram تحویل داده شوند؛ بدون اینکه parse_mode به متن تحمیل شود.
    """
    def __new__(cls, value: str, entities: list[dict] | None = None):
        obj = super().__new__(cls, value)
        obj.entities = [dict(e) for e in (entities or [])]
        return obj

    @staticmethod
    def _units(value: str) -> int:
        return len(str(value).encode("utf-16-le")) // 2

    def __add__(self, other):
        if isinstance(other, RichText):
            return RichText(str(self) + str(other), self.entities + other.entities_shifted(self._units(str(self))))
        return RichText(str(self) + str(other), self.entities)

    def __radd__(self, other):
        shift = self._units(str(other))
        return RichText(str(other) + str(self), self.entities_shifted(shift))

    def entities_shifted(self, shift: int) -> list[dict]:
        result = []
        for e in self.entities:
            x = dict(e)
            x["offset"] = int(x.get("offset", 0)) + shift
            result.append(x)
        return result


def _render_with_entities(template: str, entities: list[dict], values: dict) -> RichText:
    if not values:
        return RichText(template, entities)

    # قالب را قطعه‌قطعه می‌سازیم تا offsetهای UTF-16 entityها بعد از جایگزینی
    # placeholderها دقیقاً به محل جدید منتقل شوند. Entityهایی که داخل یک
    # placeholder متغیر باشند عمداً حذف می‌شوند؛ چنین entityای متعلق به متن
    # ادمین نیست و نمی‌تواند به‌صورت امن روی مقدار داینامیک اعمال شود.
    import string
    formatter = string.Formatter()
    parts = []
    src_pos = 0
    out_pos = 0
    mappings = []  # (source_start, source_end, output_start, output_end)
    for literal, field, spec, conv in formatter.parse(template):
        if literal:
            parts.append(literal)
            n = len(literal.encode("utf-16-le")) // 2
            mappings.append((src_pos, src_pos+n, out_pos, out_pos+n))
            src_pos += n; out_pos += n
        if field is not None:
            # طول خود placeholder در سورس
            token = "{" + field
            if conv:
                token += "!" + conv
            if spec:
                token += ":" + spec
            token += "}"
            src_n = len(token.encode("utf-16-le")) // 2
            try:
                value = formatter.get_field(field, (), values)[0]
            except Exception:
                value = "{" + field + "}"
            value = str(value)
            parts.append(value)
            out_n = len(value.encode("utf-16-le")) // 2
            mappings.append((src_pos, src_pos+src_n, out_pos, out_pos+out_n))
            src_pos += src_n; out_pos += out_n

    rendered = "".join(parts)
    rendered_units = len(rendered.encode("utf-16-le")) // 2

    def map_boundary(pos: int):
        for a,b,c,d in mappings:
            if a <= pos <= b:
                if b == a:
                    return c
                # فقط entityهای واقعاً داخل literalها را جابه‌جا کن.
                if pos == b:
                    return d
                ratio = (pos-a)/(b-a)
                return int(round(c + ratio*(d-c)))
        return None

    out_entities = []
    for ent in entities or []:
        try:
            off = int(ent.get("offset", 0)); length = int(ent.get("length", 0))
            start = map_boundary(off); end = map_boundary(off+length)
            if start is None or end is None or end <= start or end > rendered_units:
                continue
            e = dict(ent); e["offset"] = start; e["length"] = end-start
            out_entities.append(e)
        except Exception:
            continue
    return RichText(rendered, out_entities)


def text(key: str, default: str | None = None, **values) -> str:
    if key not in _CACHE:
        _CACHE[key] = (
            db.get_text_override(key, TEXTS.get(key, default or "")),
            db.get_text_override_entities(key),
        )
    template, entities = _CACHE[key]
    if values:
        try:
            return _render_with_entities(template, entities, values)
        except Exception:
            fallback = TEXTS.get(key, default or template)
            try:
                return RichText(fallback.format_map(values), [])
            except Exception:
                return RichText(fallback, [])
    return RichText(template, entities) if entities else template


def refresh(key: str):
    _CACHE.pop(key, None)


def all_items():
    return TEXT_CATEGORIES
