"""مدیریت یکپارچه‌ی چند نمونه‌ای مرزبان، پاسارگارد و Rebecca.
هیچ مفهوم «پنل فعال» در این ماژول وجود ندارد؛ هر پلن/دسته مستقیماً به یک
instance مشخص نگاشت می‌شود، درست مثل معماری چندپنلی Bomb، بدون شاهراه.
"""
import html, logging, random, re, string
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
import database as db
import panels
from states import AdminStates
from keyboards import (admin_vpn_panel_types_keyboard, admin_vpn_panel_list_keyboard,
    admin_vpn_panel_detail_keyboard, admin_vpn_panel_delete_confirm_keyboard,
    admin_vpn_panel_edit_menu_keyboard, vpn_panel_back_keyboard,
    admin_vpn_panel_types_cancel_keyboard, admin_vpn_panel_map_menu_keyboard,
    vpn_map_vip_category_pick_keyboard, vpn_map_vip_plans_keyboard,
    vpn_catalog_pick_keyboard)
from handlers.admin import _is_admin
router=Router(name='panel_admin')
log=logging.getLogger(__name__)

def _label(p): return panels.panel_label(p)

@router.callback_query(F.data=='admin_vpn_panels')
async def open_vpn_panels(callback:types.CallbackQuery):
    if not _is_admin(callback.from_user.id): return
    await callback.message.edit_text('🖥 مدیریت پنل‌های VPN\n\nمرزبان، پاسارگارد و Rebecca می‌توانند هم‌زمان چند نمونه داشته باشند.\n\nنوع پنل را انتخاب کن:',reply_markup=admin_vpn_panel_types_keyboard()); await callback.answer()

@router.message(F.text=='🖥 مدیریت پنل‌های VPN')
async def open_vpn_panels_msg(message:types.Message):
    if not _is_admin(message.from_user.id): return
    await message.answer('🖥 مدیریت پنل‌های VPN\n\nهر نوع پنل می‌تواند چند نمونه‌ی مستقل داشته باشد و هر پلن مستقیماً به نمونه‌ی موردنظر نگاشت می‌شود.\n\nنوع پنل را انتخاب کن:',reply_markup=admin_vpn_panel_types_keyboard())

@router.callback_query(F.data.startswith('vpntype|'))
async def panel_type(callback:types.CallbackQuery):
    if not _is_admin(callback.from_user.id): return
    typ=callback.data.split('|',1)[1]
    if typ not in panels.PANEL_TYPES: await callback.answer('❌ نوع پنل نامعتبر.',show_alert=True); return
    rows=db.list_vpn_panels(typ); await callback.message.edit_text(f'🖥 نمونه‌های {_label(rows[0]) if rows else panels.PANEL_TYPE_LABELS[typ]}',reply_markup=admin_vpn_panel_list_keyboard(typ,rows)); await callback.answer()

@router.callback_query(F.data.startswith('vpndetail|'))
async def panel_detail(callback:types.CallbackQuery):
    if not _is_admin(callback.from_user.id): return
    p=db.get_vpn_panel(int(callback.data.split('|')[1]))
    if not p: await callback.answer('❌ پنل پیدا نشد.',show_alert=True); return
    await callback.message.edit_text(f"🖥 {_label(p)}\nوضعیت: {'🟢 فعال' if p.get('enabled') else '🔴 غیرفعال'}\n🌐 <code>{html.escape(p.get('base_url') or '')}</code>",parse_mode='HTML',reply_markup=admin_vpn_panel_detail_keyboard(p)); await callback.answer()

@router.callback_query(F.data.startswith('vpntest|'))
async def panel_test(callback:types.CallbackQuery):
    if not _is_admin(callback.from_user.id): return
    p=db.get_vpn_panel(int(callback.data.split('|')[1]));
    if not p: await callback.answer('❌ پنل پیدا نشد.',show_alert=True); return
    await callback.answer('⏳ در حال تست...'); ok,data,msg=await panels.test_connection(p)
    await callback.message.answer(('✅' if ok else '❌')+f' {_label(p)}\n{msg}',reply_markup=vpn_panel_back_keyboard(p['id']))

@router.callback_query(F.data.startswith('vpntoggle|'))
async def panel_toggle(callback:types.CallbackQuery):
    if not _is_admin(callback.from_user.id): return
    pid=int(callback.data.split('|')[1]); p=db.get_vpn_panel(pid)
    if not p:return
    db.update_vpn_panel(pid,enabled=not bool(p.get('enabled'))); p=db.get_vpn_panel(pid)
    await callback.message.edit_text(f"🖥 {_label(p)}\nوضعیت: {'🟢 فعال' if p.get('enabled') else '🔴 غیرفعال'}",reply_markup=admin_vpn_panel_detail_keyboard(p)); await callback.answer('✅ تغییر کرد.')

@router.callback_query(F.data.startswith('vpndelete|'))
async def panel_delete_ask(callback:types.CallbackQuery):
    if not _is_admin(callback.from_user.id): return
    pid=int(callback.data.split('|')[1]); p=db.get_vpn_panel(pid)
    if not p:return
    await callback.message.edit_text(f'⚠️ حذف {_label(p)}؟\nتمام نگاشت‌های مربوط به این نمونه نیز حذف می‌شوند.',reply_markup=admin_vpn_panel_delete_confirm_keyboard(pid)); await callback.answer()

@router.callback_query(F.data.startswith('vpndeleteconfirm|'))
async def panel_delete(callback:types.CallbackQuery):
    if not _is_admin(callback.from_user.id):return
    pid=int(callback.data.split('|')[1]); p=db.get_vpn_panel(pid)
    if not p:return
    typ=p['panel_type']; db.delete_vpn_panel(pid); await callback.message.edit_text(f'🗑 حذف شد.\n\nنمونه‌های فعلی {panels.PANEL_TYPE_LABELS[typ]}:',reply_markup=admin_vpn_panel_list_keyboard(typ,db.list_vpn_panels(typ))); await callback.answer()

@router.callback_query(F.data.startswith('vpnadd|'))
async def panel_add_start(callback:types.CallbackQuery,state:FSMContext):
    if not _is_admin(callback.from_user.id):return
    typ=callback.data.split('|')[1]
    if typ not in panels.PANEL_TYPES:return
    await state.update_data(new_panel_type=typ); await state.set_state(AdminStates.waiting_panel_name)
    await callback.message.edit_text(f'➕ افزودن {panels.PANEL_TYPE_LABELS[typ]}\n\nنام دلخواه نمونه را بفرست:',reply_markup=admin_vpn_panel_types_cancel_keyboard()); await callback.answer()

@router.message(AdminStates.waiting_panel_name)
async def panel_add_name(message:types.Message,state:FSMContext):
    if not _is_admin(message.from_user.id):return
    v=(message.text or '').strip()
    if not v:return await message.answer('❌ نام خالی معتبر نیست.')
    await state.update_data(new_panel_name=v); await state.set_state(AdminStates.waiting_panel_base_url); await message.answer('🌐 Base URL پنل را بفرست (مثلاً https://panel.example.com):')

@router.message(AdminStates.waiting_panel_base_url)
async def panel_add_url(message:types.Message,state:FSMContext):
    if not _is_admin(message.from_user.id):return
    v=(message.text or '').strip()
    if not v.startswith(('http://','https://')):return await message.answer('❌ آدرس باید با http:// یا https:// شروع شود.')
    await state.update_data(new_panel_base_url=v.rstrip('/')); await state.set_state(AdminStates.waiting_panel_username); await message.answer('👤 نام کاربری پنل را بفرست:')

@router.message(AdminStates.waiting_panel_username)
async def panel_add_user(message:types.Message,state:FSMContext):
    if not _is_admin(message.from_user.id):return
    v=(message.text or '').strip()
    if not v:return await message.answer('❌ نام کاربری خالی معتبر نیست.')
    await state.update_data(new_panel_username=v); await state.set_state(AdminStates.waiting_panel_password); await message.answer('🔑 رمز عبور پنل را بفرست:')

@router.message(AdminStates.waiting_panel_password)
async def panel_add_pass(message:types.Message,state:FSMContext):
    if not _is_admin(message.from_user.id):return
    v=(message.text or '').strip(); d=await state.get_data()
    if not v:return await message.answer('❌ رمز خالی معتبر نیست.')
    pid=db.create_vpn_panel(d['new_panel_type'],d['new_panel_name'],d['new_panel_base_url'],username=d['new_panel_username'],password=v)
    await state.clear(); p=db.get_vpn_panel(pid); await message.answer(f'✅ {_label(p)} اضافه شد.',reply_markup=admin_vpn_panel_detail_keyboard(p))

@router.callback_query(F.data.startswith('vpnedit|'))
async def panel_edit_menu(callback:types.CallbackQuery):
    if not _is_admin(callback.from_user.id):return
    p=db.get_vpn_panel(int(callback.data.split('|')[1]));
    if p: await callback.message.edit_text(f'✏️ ویرایش {_label(p)}',reply_markup=admin_vpn_panel_edit_menu_keyboard(p))
    await callback.answer()

@router.callback_query(F.data.startswith('vpneditfield|'))
async def panel_edit_field(callback:types.CallbackQuery,state:FSMContext):
    if not _is_admin(callback.from_user.id):return
    _,pid,field=callback.data.split('|'); await state.update_data(edit_panel_id=int(pid),edit_panel_field=field); await state.set_state(AdminStates.waiting_panel_edit_value)
    await callback.message.answer(f'مقدار جدید {field} را بفرست:'); await callback.answer()

@router.message(AdminStates.waiting_panel_edit_value)
async def panel_edit_value(message:types.Message,state:FSMContext):
    if not _is_admin(message.from_user.id):return
    d=await state.get_data(); v=(message.text or '').strip();
    if not v:return await message.answer('❌ مقدار خالی معتبر نیست.')
    db.update_vpn_panel(d['edit_panel_id'],**{d['edit_panel_field']:v}); await state.clear(); p=db.get_vpn_panel(d['edit_panel_id']); await message.answer(f'✅ ذخیره شد. {_label(p)}',reply_markup=admin_vpn_panel_detail_keyboard(p))

# ---------------- نگاشت دقیق پلن -> instance پنل ----------------
@router.callback_query(F.data.startswith('vpnmap|'))
async def map_menu(callback:types.CallbackQuery):
    if not _is_admin(callback.from_user.id):return
    pid=int(callback.data.split('|')[1]); p=db.get_vpn_panel(pid)
    if p: await callback.message.edit_text(f'🗂 نگاشت به {_label(p)}\n\nاین نمونه را برای کدام سرویس‌ها می‌خواهی مقصد قرار بدهی؟',reply_markup=admin_vpn_panel_map_menu_keyboard(pid))
    await callback.answer()

@router.callback_query(F.data.startswith('vpnmapvip|'))
async def map_vip_categories(callback:types.CallbackQuery):
    if not _is_admin(callback.from_user.id):return
    pid=int(callback.data.split('|')[1]); cats=db.get_vip_categories(); await callback.message.edit_text('📁 دسته VIP را انتخاب کن:',reply_markup=vpn_map_vip_category_pick_keyboard(cats,pid)); await callback.answer()

@router.callback_query(F.data.startswith('vpnmapvipcat|'))
async def map_vip_cat(callback:types.CallbackQuery):
    if not _is_admin(callback.from_user.id):return
    _,pid,cid=callback.data.split('|'); pid=int(pid); cid=int(cid); plans=db.get_vip_plans(cid)
    await callback.message.edit_text('📦 پلن موردنظر را انتخاب کن. هر پلن می‌تواند به یک instance مستقل نگاشت شود:',reply_markup=vpn_map_vip_plans_keyboard(cid,plans,pid)); await callback.answer()

async def _catalog(callback,state,panel,scope,scope_id,prompt):
    await callback.answer('⏳ در حال دریافت بسته‌های پنل...'); choices,msg=await panels.get_catalog(panel)
    if not choices:return await callback.message.answer(f'❌ {msg}',reply_markup=vpn_panel_back_keyboard(panel['id']))
    await state.update_data(panel_map_scope=scope,panel_map_scope_id=scope_id,panel_map_panel_id=panel['id'],panel_map_choices=choices)
    await callback.message.answer(prompt,reply_markup=vpn_catalog_pick_keyboard(choices,panel['id']))

@router.callback_query(F.data.startswith('vpnmapvipplan|'))
async def map_vip_plan(callback:types.CallbackQuery,state:FSMContext):
    if not _is_admin(callback.from_user.id):return
    _,pid,cid,planid=callback.data.split('|'); p=db.get_vpn_panel(int(pid));
    if p: await _catalog(callback,state,p,'vip_plan',int(planid),f'یک مقصد از {_label(p)} را برای این پلن انتخاب کن (در Rebecca می‌تواند ساخت خودکار بدون Template باشد):')

@router.callback_query(F.data.startswith('vpnmapcatset|'))
async def map_category(callback:types.CallbackQuery,state:FSMContext):
    if not _is_admin(callback.from_user.id):return
    _,pid,cid=callback.data.split('|'); p=db.get_vpn_panel(int(pid));
    if p: await _catalog(callback,state,p,'vip_category',int(cid),f'یک مقصد از {_label(p)} را به‌عنوان مقصد پیش‌فرض کل این دسته انتخاب کن:')

@router.callback_query(F.data.startswith('vpnmapcustom|'))
async def map_custom(callback:types.CallbackQuery,state:FSMContext):
    if not _is_admin(callback.from_user.id):return
    pid=int(callback.data.split('|')[1]); p=db.get_vpn_panel(pid)
    if p: await _catalog(callback,state,p,'custom_build',0,f'یک مقصد از {_label(p)} را برای «بساز سرویس خودت» انتخاب کن:')

@router.callback_query(F.data.startswith('vpnmapfreetest|'))
async def map_test(callback:types.CallbackQuery,state:FSMContext):
    if not _is_admin(callback.from_user.id):return
    pid=int(callback.data.split('|')[1]); p=db.get_vpn_panel(pid)
    if p: await _catalog(callback,state,p,'free_test',0,f'یک مقصد از {_label(p)} را برای تست رایگان انتخاب کن:')

@router.callback_query(F.data.startswith('vpnmapchoose|'))
async def map_choose(callback:types.CallbackQuery,state:FSMContext):
    if not _is_admin(callback.from_user.id):return
    _,pid,idx=callback.data.split('|'); d=await state.get_data(); choices=d.get('panel_map_choices') or []; chosen=next((x for x in choices if int(x['idx'])==int(idx)),None)
    if not chosen:return await callback.answer('❌ این انتخاب منقضی شده؛ دوباره باز کن.',show_alert=True)
    db.set_panel_plan_map(d['panel_map_scope'],int(d['panel_map_scope_id']),int(pid),chosen['ref'],chosen.get('name')); await state.clear()
    p=db.get_vpn_panel(int(pid)); await callback.message.answer(f'✅ نگاشت ذخیره شد.\n\n{_label(p)} ← {chosen["name"]}',reply_markup=admin_vpn_panel_map_menu_keyboard(int(pid))); await callback.answer()

@router.callback_query(F.data.startswith('vpnmapclear|'))
async def map_clear(callback:types.CallbackQuery):
    if not _is_admin(callback.from_user.id):return
    _,pid,scope,sid=callback.data.split('|'); db.delete_panel_plan_map(scope,int(sid)); await callback.answer('🗑 نگاشت حذف شد.',show_alert=True)

# برای سازگاری با دکمه‌ی قدیمی؛ دیگر «پنل فعال» نداریم و مستقیم مدیریت چندپنلی را باز می‌کنیم.
@router.callback_query(F.data=='admin_marzban')
async def legacy_marzban_button(callback:types.CallbackQuery):
    await open_vpn_panels(callback)
@router.message(F.text=='📦 نگاشت پلن‌ها به پنل متصل')
async def legacy_marzban_message(message:types.Message):
    await open_vpn_panels_msg(message)
