// A view owns its subscriptions/timers; changing selection invalidates pending work.
export function createViewSession(){
 let token=null;const resources=new Set();
 function close(){token=null;const pending=[...resources];resources.clear();for(const dispose of pending)dispose();}
 function begin(key){close();token=Object.freeze({key});return token;}
 function own(dispose){resources.add(dispose);return ()=>resources.delete(dispose);}
 return {begin,close,own,isCurrent:value=>value!==null&&value===token,get token(){return token;}};
}
