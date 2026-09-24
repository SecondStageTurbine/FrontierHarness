import {useState} from 'react';
import {Plus,Trash2} from 'lucide-react';
import {useQuery} from '@tanstack/react-query';
import {useWorkspace,useRefresh} from '../app/context';
import {api} from '../lib/api';
import type {Project} from '../types';

type Status='todo'|'doing'|'blocked'|'done';
type Task={id:string;title:string;notes:string;status:Status;source:string;session_id:string|null;updated_by?:string;updated_at:string};
const COLUMNS:[Status,string][]=[['doing','Doing'],['todo','To do'],['blocked','Blocked'],['done','Done']];

/** The project's plan across sessions: tasks you add, team plans, and what agents move along. */
export function TaskBoard({project,onOpenSession}:{project:Project;onOpenSession?:(id:string)=>void}){
 const {path,tenant}=useWorkspace(),refresh=useRefresh();
 const base=`/projects/${project.id}/board`;
 // Agents move tasks from their own processes, so the board is re-read while it is open.
 const tasks=useQuery({queryKey:['tenant',tenant?.id,base],queryFn:({signal})=>api.get<Task[]>(path(base),signal),refetchInterval:5000,enabled:!!tenant});
 const [title,setTitle]=useState(''),[error,setError]=useState(''),[showDone,setShowDone]=useState(false);
 async function act(fn:()=>Promise<unknown>){setError('');try{await fn();await refresh()}catch(e){setError((e as Error).message)}}
 const add=(e:React.FormEvent)=>{e.preventDefault();const t=title.trim();if(!t)return;setTitle('');void act(()=>api.post(path(base),{title:t}))};
 const list=tasks.data||[];
 return <div className="task-board">
  <form className="task-add" onSubmit={add}><input aria-label="New task" placeholder="Add a task for this project…" value={title} onChange={e=>setTitle(e.target.value)}/><button className="primary" disabled={!title.trim()} aria-label="Add task"><Plus size={14}/></button></form>
  {error&&<p className="error-text">{error}</p>}
  {tasks.isPending?<p className="inspector-empty">Reading the board…</p>:!list.length?<p className="inspector-empty">No tasks yet. Add one here; team mode puts its plan here, and agents move tasks along as they work.</p>:
  COLUMNS.map(([status,label])=>{const rows=list.filter(t=>t.status===status);if(!rows.length)return null;
   if(status==='done'&&!showDone)return <button key={status} className="linkish task-done-toggle" onClick={()=>setShowDone(true)}>Show {rows.length} done</button>;
   return <section key={status} className={`task-column ${status}`}><h4>{label}<small>{rows.length}</small></h4>{rows.map(t=><div key={t.id} className="task-card">
    <select aria-label={`Status of ${t.title}`} value={t.status} onChange={e=>void act(()=>api.put(path(`${base}/${t.id}`),{status:e.target.value}))}>{COLUMNS.map(([v,l])=><option key={v} value={v}>{l}</option>)}</select>
    <div className="task-body"><strong>{t.title}</strong>{t.notes&&<p>{t.notes}</p>}<small>{t.source==='you'?'Added by you':t.source}{t.updated_by&&t.updated_by!=='you'?` · moved by ${t.updated_by}`:''}{t.session_id&&onOpenSession?<> · <button className="linkish" onClick={()=>onOpenSession(t.session_id!)}>open session</button></>:null}</small></div>
    <button className="icon-button" aria-label={`Delete ${t.title}`} title="Delete task" onClick={()=>void act(()=>api.delete(path(`${base}/${t.id}`)))}><Trash2 size={13}/></button>
   </div>)}</section>})}
 </div>;
}
