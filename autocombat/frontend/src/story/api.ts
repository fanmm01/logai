import {requestId} from '../types';
export async function storyApi<T>(path:string,data?:unknown):Promise<T> {
  const response=await fetch('/api/ui/story'+path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result=await response.json();
  if(!response.ok){const error=Object.assign(new Error(result.message||'剧情请求失败'),{status:response.status,diagnostics:result.diagnostics});throw error;}
  return result;
}
export const commandId=requestId;
