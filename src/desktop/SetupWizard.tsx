import {useEffect,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {CheckCircle2,CircleDashed,Copy,LoaderCircle,Wand2,XCircle} from 'lucide-react';
import {useWorkspace,useRefresh} from '../app/context';
import {Modal} from '../components/ui';
import {api} from '../lib/api';
import type {Model} from '../types';

type Tool={provider:string;label:string;installed:boolean;path:string|null;version:string|null;install:string;signin:string;models:string[];default:string};
type Pick={on:boolean;model:string};
type Result={state:'working'|'ok'|'failed';message?:string};

/** First run: find the agent tools this computer has, connect them in one go, and say how to get the rest. */
export function SetupWizard({open,onClose,models}:{open:boolean;onClose:()=>void;models:Model[]}){
 const {path,notify}=useWorkspace(),refresh=useRefresh();
 const detect=useQuery({queryKey:['setup-detect'],enabled:open,staleTime:60000,queryFn:({signal})=>api.get<Tool[]>(path('/setup/detect'),signal)});
 const [picks,setPicks]=useState<Record<string,Pick>>({});
 const [results,setResults]=useState<Record<string,Result>>({});
 const [busy,setBusy]=useState(false);
 useEffect(()=>{if(!detect.data)return;setPicks(Object.fromEntries(detect.data.map(t=>[t.provider,{on:t.installed&&!models.some(m=>m.provider===t.provider),model:t.default}])))},[detect.data]);
 async function connect(){
  setBusy(true);
  for(const t of detect.data||[]){
   const pick=picks[t.provider];if(!pick?.on)continue;
   setResults(r=>({...r,[t.provider]:{state:'working'}}));
   try{
    const made=await api.post<Model>(path('/models'),{name:`${t.label.replace(' CLI','')} ${pick.model}`,provider:t.provider,model_name:pick.model});
    await api.post(path(`/models/${made.id}/test`));
    setResults(r=>({...r,[t.provider]:{state:'ok'}}));
   }catch(e){setResults(r=>({...r,[t.provider]:{state:'failed',message:(e as Error).message}}))}
  }
  await refresh();setBusy(false);notify('Agents connected');
 }
 const done=Object.values(results).some(r=>r.state==='ok');
 return <Modal open={open} onClose={onClose} title="Set up your agents" description="Frontier runs the agent command line tools on this computer. Here is what it found." wide>
  {detect.isPending?<p className="muted"><LoaderCircle size={14} className="spin"/> Looking for Claude Code, Codex, OpenCode and Gemini CLI…</p>:detect.error?<p className="error-text">{detect.error.message}</p>:
  <div className="wizard-tools">{detect.data?.map(t=>{const pick=picks[t.provider]||{on:false,model:t.default};const result=results[t.provider];const already=models.some(m=>m.provider===t.provider);return <div key={t.provider} className={`wizard-tool ${t.installed?'found':'missing'}`}>
   <div className="wizard-head">{t.installed?<CheckCircle2 size={16} className="file-added"/>:<XCircle size={16} className="file-removed"/>}<strong>{t.label}</strong><small>{t.installed?`v${t.version}`:'not installed'}{already?' · already connected':''}</small></div>
   {t.installed?<><code className="wizard-path" title={t.path||''}>{t.path}</code>
    <div className="wizard-pick"><label><input type="checkbox" checked={pick.on} onChange={e=>setPicks(p=>({...p,[t.provider]:{...pick,on:e.target.checked}}))}/>Connect with</label>
     <select aria-label={`${t.label} model`} value={pick.model} onChange={e=>setPicks(p=>({...p,[t.provider]:{...pick,model:e.target.value}}))}>{t.models.map(m=><option key={m} value={m}>{m}</option>)}</select>
     {result&&(result.state==='working'?<LoaderCircle size={14} className="spin"/>:result.state==='ok'?<span className="file-added">Connected</span>:<span className="file-removed" title={result.message}>{(result.message||'Failed').slice(0,120)}</span>)}</div>
    <small className="muted">Sign in once in a terminal if you have not: <code>{t.signin}</code></small></>
   :<div className="wizard-install"><span>Install it in PowerShell, then sign in once:</span><code>{t.install}</code><code>{t.signin}</code><button className="icon-button" aria-label={`Copy ${t.label} install commands`} onClick={()=>navigator.clipboard.writeText(`${t.install}\n${t.signin}`).then(()=>notify('Commands copied')).catch(()=>notify('Clipboard unavailable'))}><Copy size={13}/></button></div>}
  </div>})}</div>}
  <p className="muted small"><CircleDashed size={12}/> Nothing here uses an API key: each tool signs in with your own subscription. A tool installed after this window opened is found when you press Look again.</p>
  <div className="actions end"><button type="button" onClick={()=>detect.refetch()} disabled={detect.isFetching}>Look again</button><button type="button" onClick={onClose}>{done?'Done':'Skip for now'}</button><button className="primary" disabled={busy||!Object.values(picks).some(p=>p.on)} onClick={connect}>{busy?<><LoaderCircle size={14} className="spin"/> Connecting…</>:<><Wand2 size={14}/> Connect selected</>}</button></div>
 </Modal>;
}
