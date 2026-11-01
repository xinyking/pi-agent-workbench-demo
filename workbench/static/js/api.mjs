export async function api(path,data,{timeoutMs=8000}={}){
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeoutMs);
 try{const options=data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)};const response=await fetch(path,{...options,signal:controller.signal});if(!(response.headers.get('Content-Type')||'').includes('application/json'))throw new Error('服务返回了非预期页面，请检查 4310 端口是否运行本工作台');const result=await response.json();if(!response.ok){const outdated=response.status===404&&result.error==='不存在'&&/\/(rename|delete)$/.test(path);const error=new Error(outdated?'当前后台尚未加载对话管理功能，请在 WSL 终端重启工作台后重试':result.error||'请求失败');error.status=response.status;throw error;}return result;}
 catch(error){if(error.name==='AbortError')throw new Error(`请求超过 ${timeoutMs/1000} 秒，请检查网络或后台服务后重试`);throw error;}
 finally{clearTimeout(timer);}
}
