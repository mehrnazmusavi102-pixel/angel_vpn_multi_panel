"""Multi-instance Rebecca adapter.
Uses the same REST shape as Rebecca's documented Marzban-derived API, but every
request is scoped to the selected vpn_panels row (no global active panel)."""
from __future__ import annotations
import asyncio, logging, time
import aiohttp
logger=logging.getLogger(__name__)
_TIMEOUT=aiohttp.ClientTimeout(total=20,connect=10)
_tokens={}; _sessions={}; _caps={}; _templates={}; _auto_profiles={}; _lock=asyncio.Lock()

def _pid(p): return int(p['id'])
async def _session(p):
    pid=_pid(p); s=_sessions.get(pid)
    if s and not s.closed: return s
    async with _lock:
        s=_sessions.get(pid)
        if not s or s.closed:
            s=aiohttp.ClientSession(timeout=_TIMEOUT,connector=aiohttp.TCPConnector(limit=20,ttl_dns_cache=300,keepalive_timeout=75)); _sessions[pid]=s
        return s
async def _token(p):
    pid=_pid(p); c=_tokens.setdefault(pid,{'token':None,'exp':0})
    if c['token'] and time.monotonic()<c['exp']: return c['token'],None
    base=(p.get('base_url') or '').rstrip('/'); u=p.get('username'); pw=p.get('password')
    if not (base and u and pw): return None,'اطلاعات اتصال Rebecca کامل نیست.'
    try:
        s=await _session(p)
        async with s.post(base+'/api/admin/token',data={'username':u,'password':pw,'grant_type':'password'}) as r:
            d=await r.json(content_type=None)
        if r.status!=200 or not isinstance(d,dict) or not d.get('access_token'):
            return None,f"ورود Rebecca ناموفق بود ({r.status}): {(d or {}).get('detail') if isinstance(d,dict) else d}"
        c['token']=d['access_token']; c['exp']=time.monotonic()+18*60; return c['token'],None
    except Exception as e: logger.exception('Rebecca auth'); return None,f'خطا در اتصال Rebecca: {e}'
async def _req(p,method,path,json=None,params=None):
    tok,err=await _token(p)
    if err:return False,None,err
    try:
        s=await _session(p); base=(p.get('base_url') or '').rstrip('/')
        async with s.request(method,base+path,json=json,params=params,headers={'Authorization':f'Bearer {tok}'}) as r:
            d=await r.json(content_type=None)
        if r.status==401: _tokens[_pid(p)]['token']=None
        if r.status>=400:return False,d,f"خطای Rebecca ({r.status}): {(d or {}).get('detail') if isinstance(d,dict) else d}"
        return True,d,'موفق'
    except Exception as e:return False,None,f'خطا در ارتباط با Rebecca: {e}'
async def test_connection(p):
    ok,d,m=await _req(p,'GET','/api/system')
    if not ok:
        ok2,d2,m2=await _req(p,'GET','/openapi.json');
        return (True,d2,'احراز هویت و OpenAPI Rebecca در دسترس است.') if ok2 else (False,d2 or d,m2)
    return True,d,m
async def get_system_stats(p): return await _req(p,'GET','/api/system')
async def _caps(p,force=False):
    pid=_pid(p); c=_caps.get(pid)
    if c and not force and time.monotonic()-c['at']<1800:return c
    ok,d,_=await _req(p,'GET','/openapi.json'); raw=str(d).lower() if ok else ''
    c={'hwid':('hwid_limit' in raw or 'hwid' in raw),'at':time.monotonic()}; _caps[pid]=c; return c
async def get_templates(p,force_refresh=False):
    pid=_pid(p); c=_templates.get(pid)
    if c and not force_refresh and time.monotonic()<c['exp']: return True,c['items'],'موفق (cache)'
    last=(False,None,'تمپلیت Rebecca پیدا نشد.')
    for path in ('/api/user_templates','/api/user_template','/api/user_templates/simple'):
        ok,d,m=await _req(p,'GET',path); last=(ok,d,m)
        if ok:
            items=d.get('items',d) if isinstance(d,dict) else d
            if isinstance(items,list): _templates[pid]={'items':items,'exp':time.monotonic()+1800}; return True,items,m
    return last
async def get_template(p,tid):
    ok,items,m=await get_templates(p)
    if ok:
        for x in items:
            if isinstance(x,dict) and str(x.get('id'))==str(tid): return True,x,'موفق'
    return False,None,f'تمپلیت Rebecca با شناسه {tid} پیدا نشد.'

async def _inbound_paths(p):
    """Discover Rebecca inbound-list endpoints from its live OpenAPI document."""
    paths=[]
    ok,spec,_=await _req(p,'GET','/openapi.json')
    if ok and isinstance(spec,dict):
        for path,methods in (spec.get('paths') or {}).items():
            if 'inbound' not in str(path).lower() or not isinstance(methods,dict): continue
            if 'get' in methods: paths.append(path)
    # Compatibility fallbacks for older Rebecca/Marzban-derived builds.
    for path in ('/api/inbounds','/api/inbounds/simple','/api/core/inbounds'):
        if path not in paths: paths.append(path)
    return paths

async def get_auto_profile(p, force_refresh=False):
    """Build a template-like profile directly from Rebecca inbounds.

    This is the fallback used when the Rebecca instance has no user templates.
    Existing Template-based installations continue to use their original flow.
    """
    pid=_pid(p); cached=_auto_profiles.get(pid)
    if cached and not force_refresh and time.monotonic()<cached['exp']:
        return True,cached['profile'],'موفق (cache)'

    last=(False,None,'Inboundهای Rebecca برای ساخت خودکار پیدا نشد.')
    for path in await _inbound_paths(p):
        ok,d,m=await _req(p,'GET',path); last=(ok,d,m)
        if not ok: continue
        raw=d
        if isinstance(d,dict):
            raw=d.get('items') or d.get('inbounds') or d.get('data') or d.get('results') or d
        if isinstance(raw,dict): raw=list(raw.values())
        if not isinstance(raw,list): continue

        inbounds={}
        for x in raw:
            if not isinstance(x,dict): continue
            tag=(x.get('tag') or x.get('name') or x.get('inbound_tag') or x.get('remark') or x.get('id'))
            protocol=str(x.get('protocol') or x.get('type') or '').lower()
            if not tag: continue
            if protocol not in ('vless','vmess','trojan','shadowsocks','shadowsocks2022'):
                for key in ('settings','config','inbound','core_config'):
                    cfg=x.get(key)
                    if isinstance(cfg,str):
                        try:
                            import json as _json; cfg=_json.loads(cfg)
                        except Exception:
                            cfg={}
                    if isinstance(cfg,dict):
                        protocol=str(cfg.get('protocol') or cfg.get('type') or '').lower()
                        if protocol in ('vless','vmess','trojan','shadowsocks','shadowsocks2022'): break
            if protocol == 'shadowsocks2022': protocol='shadowsocks'
            if protocol in ('vless','vmess','trojan','shadowsocks'):
                inbounds.setdefault(protocol,[]).append(str(tag))

        if inbounds:
            profile={'inbounds':inbounds,'data_limit_reset_strategy':'no_reset'}
            _auto_profiles[pid]={'profile':profile,'exp':time.monotonic()+300}
            return True,profile,'ساخت خودکار بر اساس Inboundهای Rebecca'
    return last

async def get_creation_profile(p,tid=None):
    """Resolve a normal Template or the special automatic profile."""
    if tid is not None and str(tid).lower() not in ('auto','__auto__','0','none','null'):
        return await get_template(p,tid)
    return await get_auto_profile(p)

def _proxies(t):
    ins=t.get('inbounds') or {}
    if not ins: return {'vless':{}}
    return {k:{} for k in ins if k in ('vless','vmess','trojan','shadowsocks')} or {'vless':{}}
def _bytes(gb):
    try:return int(float(gb or 0)*1024**3) if float(gb or 0)>0 else 0
    except:return 0
def _expire(days):
    try:return int(time.time())+int(days)*86400 if int(days or 0)>0 else 0
    except:return 0
async def _build(p,tid,username,volume,days,device_limit):
    ok,t,m=await get_creation_profile(p,tid)
    if not ok:return False,None,m
    body={'username':username,'proxies':_proxies(t),'inbounds':t.get('inbounds') or {},'expire':_expire(days) if days is not None else int(time.time())+int(t.get('expire_duration') or 0),'data_limit':_bytes(volume) if volume is not None else int(t.get('data_limit') or 0),'data_limit_reset_strategy':t.get('data_limit_reset_strategy') or 'no_reset'}
    gids=t.get('group_ids');
    if isinstance(gids,list): body['group_ids']=gids
    if device_limit not in (None,0,'0'):
        cap=await _caps(p)
        if not cap.get('hwid'): return False,None,'Rebecca این قابلیت HWID را در OpenAPI اعلام نکرده است؛ محدودیت دستگاه اعمال نشد.'
        body['hwid_limit']=int(device_limit)
    for path in ('/api/user','/api/users'):
        ok,d,m=await _req(p,'POST',path,json=body)
        if ok:return True,d,m
    return ok,d,m
async def create_user_from_template(p,tid,username,device_limit=None):
    return await _build(p,tid,username,None,None,device_limit)
async def create_user_auto(p,username,device_limit=None):
    return await _build(p,'auto',username,None,None,device_limit)
async def create_user_custom(p,tid,username,volume_gb,days,device_limit=None):
    return await _build(p,tid,username,volume_gb,days,device_limit)
async def create_user_custom_auto(p,username,volume_gb,days,device_limit=None):
    return await _build(p,'auto',username,volume_gb,days,device_limit)
async def get_user(p,username): return await _req(p,'GET',f'/api/user/{username}')
async def renew_user(p,username,tid,device_limit=None):
    ok,t,m=await get_creation_profile(p,tid)
    if not ok:return False,None,m
    return await renew_user_custom(p,username,t.get('data_limit'),int((t.get('expire_duration') or 0)/86400),device_limit)
async def renew_user_custom(p,username,volume_gb,days,device_limit=None):
    body={'data_limit':_bytes(volume_gb),'expire':_expire(days)}
    if device_limit not in (None,0,'0'):
        cap=await _caps(p)
        if cap.get('hwid'): body['hwid_limit']=int(device_limit)
    return await _req(p,'PUT',f'/api/user/{username}',json=body)
async def disable_user(p,username): return _simple(p,'PUT',f'/api/user/{username}',{'status':'disabled'})
async def enable_user(p,username): return _simple(p,'PUT',f'/api/user/{username}',{'status':'active'})
async def revoke_sub(p,username):
    for path in (f'/api/user/{username}/revoke_sub',f'/api/user/{username}/revoke'):
        ok,d,m=await _req(p,'POST',path)
        if ok:return ok,d,m
    return False,None,m
def _simple(p,meth,path,body): return _req(p,meth,path,json=body)
async def delete_user(p,username): return await _req(p,'DELETE',f'/api/user/{username}')
def extract_link_and_username(p,data):
    if not isinstance(data,dict):return None,None
    link=data.get('subscription_url') or data.get('subscription') or data.get('sub_url') or data.get('sub') or data.get('link')
    return link,data.get('username')
