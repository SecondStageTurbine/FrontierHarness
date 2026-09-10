export class ApiError extends Error { constructor(message:string,public status:number,public code?:string){super(message)} }
export async function request<T>(path:string,options:RequestInit={}):Promise<T>{
  const response=await fetch('/api'+path,{...options,credentials:'same-origin',headers:{...(options.body instanceof FormData?{}:{'Content-Type':'application/json'}),...options.headers}});
  const value=await response.json().catch(()=>({detail:'The server returned an unreadable response.'}));
  if(!response.ok){const detail=Array.isArray(value.detail)?value.detail.map((d:{loc:string[];msg:string})=>`${d.loc.slice(1).join(' ')}: ${d.msg}`).join('; '):value.detail;throw new ApiError(detail||'The request failed. Please retry.',response.status,value.code)}
  return value as T;
}
export const api={get:<T,>(p:string,signal?:AbortSignal)=>request<T>(p,{signal}),post:<T,>(p:string,data?:unknown)=>request<T>(p,{method:'POST',body:JSON.stringify(data??{})}),put:<T,>(p:string,data:unknown)=>request<T>(p,{method:'PUT',body:JSON.stringify(data)}),delete:(p:string)=>request(p,{method:'DELETE'}),upload:<T,>(p:string,data:FormData)=>request<T>(p,{method:'POST',body:data})};
export const money=(n:number|null|undefined)=>n==null?'Unavailable':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:n>0&&n<0.01?4:2}).format(n);
export const date=(s:string|null|undefined)=>s?new Date(s).toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):'—';
export const duration=(start:string,end?:string|null)=>{const seconds=Math.max(0,Math.floor(((end?new Date(end).getTime():Date.now())-new Date(start).getTime())/1000));return `${Math.floor(seconds/60)}m ${seconds%60}s`};
export function download(name:string,value:string){const link=document.createElement('a');const url=URL.createObjectURL(new Blob([value],{type:'text/plain;charset=utf-8'}));link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
