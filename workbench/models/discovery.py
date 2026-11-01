"""Bounded, credential-scoped model catalog GETs. Never send prompts or follow redirects."""
import json
import http.client
import socket
import time
from urllib import request,error,parse

APIS=('openai-completions','openai-responses','anthropic-messages')
MAX_BYTES=2*1024*1024
MAX_MODELS=500
TIMEOUT=12

def validate_endpoint(base,api):
    if api not in APIS:raise ValueError('不支持的API协议')
    if not isinstance(base,str) or len(base)>1000:raise ValueError('API地址无效')
    try:
        url=parse.urlsplit(base);url.port
    except ValueError:raise ValueError('API地址或端口无效') from None
    if url.scheme not in ('https','http') or not url.hostname or url.username or url.password or url.query or url.fragment or any(c.isspace() or ord(c)<32 for c in base):raise ValueError('API地址必须为无账号、查询参数和片段的 HTTP(S) URL')
    return base.rstrip('/')

def validate_key(key):
    if not isinstance(key,str) or len(key)>4000 or any(c in key for c in '\r\n\0'):raise ValueError('API Key格式无效')
    return key

class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def fetch_models(base,api,key):
    base=validate_endpoint(base,api);validate_key(key)
    if not key:raise ValueError('请填写 API Key；无认证的本地服务可填写 local')
    endpoint=base+('/v1/models' if api=='anthropic-messages' and not parse.urlsplit(base).path.endswith('/v1') else '/models')
    headers={'Accept':'application/json','User-Agent':'workbench-model-catalog/1'}
    if api=='anthropic-messages':headers.update({'x-api-key':key,'anthropic-version':'2023-06-01'})
    else:headers['Authorization']='Bearer '+key
    opener=request.build_opener(request.ProxyHandler({}),NoRedirect())
    models={};warnings=[];deadline=time.monotonic()+TIMEOUT;total=0;cursor=None;seen=set();truncated=False;skipped=0
    try:
        for page in range(5):
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError()
            query={}
            if api=='anthropic-messages':
                query={'limit':100}
                if cursor:query['after_id']=cursor
            url=endpoint+('?' + parse.urlencode(query) if query else '')
            with opener.open(request.Request(url,headers=headers,method='GET'),timeout=min(5,remaining)) as response:
                if response.status!=200:raise ValueError('模型目录未返回成功响应，可手工填写模型ID')
                chunks=[]
                while True:
                    if time.monotonic()>deadline:raise TimeoutError()
                    chunk=response.read1(min(65536,MAX_BYTES-total+1))
                    if not chunk:break
                    total+=len(chunk)
                    if total>MAX_BYTES:raise ValueError('模型目录超过2MB上限，请手工填写所需模型ID')
                    chunks.append(chunk)
                try:payload=json.loads(b''.join(chunks))
                except (ValueError,UnicodeError):raise ValueError('服务未返回有效的模型目录JSON；可手工填写模型ID') from None
            if not isinstance(payload,dict) or not isinstance(payload.get('data'),list):raise ValueError('服务的模型目录格式不兼容，需要 data 数组；可手工填写模型ID')
            items=payload['data']
            for index,item in enumerate(items):
                ident=item.get('id') if isinstance(item,dict) else None
                if not isinstance(ident,str) or not ident or len(ident)>150 or ident.startswith('-') or any(c.isspace() or ord(c)<32 for c in ident):skipped+=1;continue
                name=item.get('display_name') or item.get('name') or ident
                if ident==key or (len(key)>=8 and (key in ident or isinstance(name,str) and key in name)):skipped+=1;continue
                models.setdefault(ident,{'id':ident,'name':name[:200] if isinstance(name,str) else ident})
                if len(models)>=MAX_MODELS:
                    truncated=index<len(items)-1 or payload.get('has_more') is True;break
            if len(models)>=MAX_MODELS:break
            if payload.get('has_more') is not True:break
            if api!='anthropic-messages':truncated=True;break
            cursor=payload.get('last_id')
            if not isinstance(cursor,str) or not cursor or len(cursor)>200 or cursor in seen:raise ValueError('模型目录的分页游标无效；可手工填写模型ID')
            seen.add(cursor)
        else:truncated=True
    except error.HTTPError as exc:
        code=exc.code;exc.close()
        if code in (401,403):raise ValueError('模型目录鉴权失败（'+str(code)+'），请检查密钥与目录访问权限') from None
        if code in (404,405):raise ValueError('此地址未提供模型目录；请核对 Base URL 或手工填写模型ID') from None
        if 300<=code<400:raise ValueError('模型目录发生重定向，为避免密钥转发已停止；请填写最终 Base URL') from None
        if code==429:raise ValueError('模型目录请求过于频繁，请稍后重试') from None
        raise ValueError('模型目录服务返回HTTP '+str(code)+'；可稍后重试或手工填写') from None
    except (TimeoutError,socket.timeout):raise ValueError('获取模型目录超时，原有模型列表未改变') from None
    except (error.URLError,OSError,UnicodeError,http.client.HTTPException):raise ValueError('无法连接模型目录，请检查地址、网络与TLS证书；原有列表未改变') from None
    if truncated:warnings.append('仅显示前500个模型或前5页；请搜索已获取结果，未列出的模型仍可手工填写')
    if skipped:warnings.append('已跳过 '+str(skipped)+' 个无效条目')
    if not models:warnings.append('服务未返回可导入的模型ID，可手工填写')
    return {'models':list(models.values()),'truncated':truncated,'warnings':warnings}
