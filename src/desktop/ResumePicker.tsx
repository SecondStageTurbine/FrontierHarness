import {useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {LoaderCircle} from 'lucide-react';
import {useWorkspace} from '../app/context';
import {Modal} from '../components/ui';
import {api} from '../lib/api';
import type {Session} from '../types';

type CliConversation={source:'claude'|'codex';key:string;name:string;first:string;created_at:string;updated_at:string;messages:number;session_id:string|null};

/** Recent Claude Code and Codex conversations about this folder; the one picked becomes the current session. */
export function ResumePicker({open,projectId,onClose,onResumed}:{open:boolean;projectId:string;onClose:()=>void;onResumed:(s:Session)=>void}){
 const {path}=useWorkspace();
 const list=useQuery({queryKey:['cli-sessions',projectId],enabled:open,staleTime:0,queryFn:({signal})=>api.get<CliConversation[]>(path(`/projects/${projectId}/cli-sessions`),signal)});
 const [busy,setBusy]=useState(''),[error,setError]=useState('');
 async function pick(c:CliConversation){
  setBusy(c.key);setError('');
  try{onResumed(await api.post<Session>(path(`/projects/${projectId}/cli-sessions/resume`),{source:c.source,key:c.key}))}
  catch(e){setError((e as Error).message)}finally{setBusy('')}
 }
 return <Modal open={open} onClose={onClose} title="Resume from CLI" description="Conversations you had with Claude Code or Codex in this project's folder. The next turn by the same tool continues its own session; another agent picks it up from the transcript." wide>
  {list.isPending?<p className="muted"><LoaderCircle size={14} className="spin"/> Reading Claude and Codex history…</p>:list.error?<p className="error-text">{list.error.message}</p>:
   !list.data?.length?<p className="muted">No Claude Code or Codex conversations were found for this folder.</p>:
   <div className="resume-list" role="listbox" aria-label="Command line conversations">{list.data.map(c=><button key={`${c.source}:${c.key}`} role="option" aria-selected={false} disabled={!!busy} onClick={()=>pick(c)}>
    <span className={`resume-source ${c.source}`}>{c.source==='claude'?'Claude':'Codex'}</span>
    <span className="resume-text"><strong>{c.name}</strong>{c.first!==c.name&&<small>{c.first}</small>}</span>
    <span className="resume-meta">{busy===c.key?<LoaderCircle size={13} className="spin"/>:<>{new Date(c.updated_at).toLocaleString([],{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}<small>{c.messages} messages{c.session_id?' · imported':''}</small></>}</span>
   </button>)}</div>}
  {error&&<p className="error-text">{error}</p>}
 </Modal>;
}
