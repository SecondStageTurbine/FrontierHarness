import {useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {RefreshCw} from 'lucide-react';
import {useWorkspace} from '../app/context';
import {api} from '../lib/api';

type Limit={label:string;percent:number;severity:string;resets_at:string|null};
type Plan={provider:string;label:string;plan:string|null;error:string|null;retry_after:number|null;limits:Limit[]};

function resets(at:string|null){
 if(!at)return '';
 const ms=new Date(at).getTime()-Date.now();
 if(ms<=0)return 'resetting now';
 const h=Math.floor(ms/3600000),m=Math.round(ms%3600000/60000);
 if(h<24)return `resets in ${h?`${h}h `:''}${m}m`;
 return `resets ${new Date(at).toLocaleString([],{weekday:'short',hour:'numeric',minute:'2-digit'})}`;
}

/** How much of each signed-in subscription's windows is used, like the tools' own /usage, beside the composer. */
export function UsageDock({inline=false}:{inline?:boolean}){
 const {path}=useWorkspace();
 const [force,setForce]=useState(false);
 const limits=useQuery({queryKey:['subscription-limits'],refetchInterval:300000,staleTime:240000,
  queryFn:({signal})=>api.get<Plan[]>(path(`/providers/limits${force?'?refresh=true':''}`),signal).finally(()=>setForce(false))});
 if(!limits.data?.length)return inline&&limits.isSuccess?<p className="muted">No Claude Code or Codex subscription is signed in on this computer.</p>:null;
 return <aside className={`usage-dock${inline?' inline':''}`} aria-label="Subscription usage">
  <div className="usage-dock-head"><strong>{inline?'Subscription limits':'Usage'}</strong><button className="icon-button" title="Refresh usage" aria-label="Refresh usage" disabled={limits.isFetching} onClick={()=>{setForce(true);setTimeout(()=>limits.refetch())}}><RefreshCw size={12} className={limits.isFetching?'spin':''}/></button></div>
  {limits.data.map(p=><div key={p.provider} className="usage-plan-card">
   <div className="usage-plan-title">{p.label}{p.plan&&<small>{p.plan}</small>}</div>
   {p.error?<p className="usage-plan-error">{p.error}</p>:p.limits.map(l=>{const level=l.percent>=90||l.severity==='critical'?'critical':l.percent>=70||l.severity==='warning'?'warning':'';return <div key={l.label} className="usage-limit">
    <div className="usage-limit-top"><span>{l.label}</span><span>{Math.round(l.percent)}%</span></div>
    <div className="usage-limit-track"><div className={`usage-limit-fill ${level}`} style={{width:`${Math.max(0,Math.min(100,l.percent))}%`}}/></div>
    <small>{resets(l.resets_at)}</small>
   </div>})}
  </div>)}
 </aside>;
}
