/** Trusted, workbench-scoped static providers; never evaluate user-supplied code. */
import * as fs from 'node:fs';
import * as path from 'node:path';
import type {ExtensionAPI} from '@earendil-works/pi-coding-agent';
export default function(pi:ExtensionAPI){
 const directory=process.env.WORKBENCH_DATA_DIR;if(!directory)return;
 const file=path.join(directory,'pi-api.json');if(!fs.existsSync(file))return;
 const config=JSON.parse(fs.readFileSync(file,'utf8'));
 for(const [index,p] of config.providers.entries()){
  const variable='WORKBENCH_MANAGED_LLM_KEY_'+index;process.env[variable]=p.api_key;
  pi.registerProvider('wbapi-'+p.id,{
   name:p.name,baseUrl:p.base_url,api:p.api,apiKey:'$'+variable,
   models:p.models.map((id:string)=>({id,name:id,reasoning:p.reasoning,input:p.vision?['text','image']:['text'],cost:{input:0,output:0,cacheRead:0,cacheWrite:0},contextWindow:p.context_window,maxTokens:p.max_tokens})),
  });
 }
}
