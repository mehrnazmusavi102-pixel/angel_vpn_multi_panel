"""Unified multi-instance VPN panel layer: Marzban, PasarGuard, Rebecca."""
import marzban_panel, pasargad_panel, rebecca_panel
PANEL_TYPE_LABELS={'marzban':'مرزبان','pasargad':'پاسارگارد','rebecca':'Rebecca'}
PANEL_TYPES=list(PANEL_TYPE_LABELS)
def panel_label(p): return f"{PANEL_TYPE_LABELS.get(p.get('panel_type'),p.get('panel_type','?'))} — {p.get('name') or '#'+str(p.get('id'))}"
def _client(p): return {'marzban':marzban_panel,'pasargad':pasargad_panel,'rebecca':rebecca_panel}.get(p.get('panel_type'))

async def test_connection(p):
    c=_client(p); return await c.test_connection(p) if c else (False,None,'نوع پنل نامعتبر.')

async def get_catalog(p):
    c=_client(p)
    if not c:return [],'نوع پنل نامعتبر.'

    # Current Rebecca is service-based. Services replace the old Template mapping.
    if p.get('panel_type') == 'rebecca' and hasattr(c, 'get_services'):
        sok, services, smsg = await c.get_services(p, force_refresh=True)
        if sok:
            out=[]
            for x in services:
                if not isinstance(x,dict) or x.get('id') is None: continue
                sid=str(x['id']); name=x.get('name') or f'Service {sid}'
                hosts=x.get('host_count')
                suffix=f' | {hosts} Host' if hosts is not None else ''
                out.append({'idx':len(out),'ref':f'service:{sid}','name':name,'label':f'🦋 {name}{suffix}'[:60]})
            if out: return out,'لیست Serviceهای Rebecca دریافت شد.'
            # No service is a real configuration problem; do not fake a Template.
            return [],'در Rebecca هیچ Service فعالی وجود ندارد. ابتدا در Rebecca یک Service بساز و Hostهای موردنظر را به آن متصل کن.'

    # Legacy Template flow remains intact for older Rebecca / other panels.
    ok,data,msg=await c.get_templates(p,force_refresh=True)
    if ok:
        items=data if isinstance(data,list) else []
        out=[]
        for i,x in enumerate(items):
            if not isinstance(x,dict) or x.get('id') is None:continue
            ref=str(x['id']); name=x.get('name') or x.get('remark') or f'Template {ref}'
            out.append({'idx':i,'ref':ref,'name':name,'label':f'📦 {name} (id: {ref})'[:60]})
        if out:return out,'موفق'
    if p.get('panel_type')=='rebecca' and hasattr(c,'get_auto_profile'):
        aok,profile,amsg=await c.get_auto_profile(p,force_refresh=True)
        if aok:return ([{'idx':0,'ref':'auto','name':'ساخت خودکار بدون Template','label':'⚙️ ساخت خودکار از Inboundهای Rebecca'}],amsg)
    return [],msg or f'هیچ مقصدی در {panel_label(p)} پیدا نشد.'

async def create_service(p,username,remote_ref,volume_gb=None,days=None,device_limit=None):
    c=_client(p)
    if not c:return False,None,None,None,'نوع پنل نامعتبر.'
    ref=str(remote_ref or '')

    if p.get('panel_type')=='rebecca' and ref.startswith('service:'):
        sid=ref.split(':',1)[1]
        if volume_gb is not None or days is not None:
            ok,data,msg=await c.create_user_custom_service(p,sid,username,volume_gb,days,device_limit=device_limit)
        else:
            ok,data,msg=await c.create_user_from_service(p,sid,username,device_limit=device_limit)
    else:
        is_auto=ref.lower() in ('auto','__auto__','0','none','null')
        if p.get('panel_type')=='rebecca' and is_auto:
            if volume_gb is not None or days is not None: ok,data,msg=await c.create_user_custom_auto(p,username,volume_gb,days,device_limit=device_limit)
            else: ok,data,msg=await c.create_user_auto(p,username,device_limit=device_limit)
        else:
            tid=int(ref)
            if volume_gb is not None or days is not None: ok,data,msg=await c.create_user_custom(p,tid,username,volume_gb,days,device_limit=device_limit)
            else: ok,data,msg=await c.create_user_from_template(p,tid,username,device_limit=device_limit)
    if not ok:return False,None,None,data,msg
    link,uid=c.extract_link_and_username(p,data)
    return True,link,uid or username,data,msg

async def renew_service(p,service_id,remote_ref=None,volume_gb=None,days=None,device_limit=None):
    c=_client(p)
    if not c:return False,None,service_id,None,'نوع پنل نامعتبر.'
    ref=str(remote_ref or '')
    if p.get('panel_type')=='rebecca' and ref.startswith('service:'):
        sid=ref.split(':',1)[1]
        ok,data,msg=await c.renew_service_user(p,service_id,sid,volume_gb,days,device_limit=device_limit)
    elif ref.lower() not in ('auto','__auto__','0','none','null'):
        ok,data,msg=await c.renew_user(p,service_id,int(ref),device_limit=device_limit)
    else:
        ok,data,msg=await c.renew_user_custom(p,service_id,volume_gb,days,device_limit=device_limit)
    if not ok:return False,None,service_id,data,msg
    link,uid=c.extract_link_and_username(p,data); return True,link,uid or service_id,data,msg

async def disable_service(p,s):
    c=_client(p); ok,d,m=await c.disable_user(p,s); return ok,m
async def enable_service(p,s):
    c=_client(p); ok,d,m=await c.enable_user(p,s); return ok,m
async def regenerate_sub_link(p,s):
    c=_client(p); ok,d,m=await c.revoke_sub(p,s)
    if not ok:return False,None,s,d,m
    link,uid=c.extract_link_and_username(p,d); return True,link,uid or s,d,m
async def delete_service(p,s):
    c=_client(p); ok,d,m=await c.delete_user(p,s); return ok,m
async def get_service_snapshot(p,s):
    c=_client(p); return await c.get_user(p,s)
