import {useState} from 'react';
import {Check,Download,LoaderCircle,Trash2} from 'lucide-react';
import {useResource,useWorkspace,useRefresh} from '../../app/context';
import {api} from '../../lib/api';
import type {Project} from '../../types';

type Entry={name:string;title:string;category:string;description:string;installed:boolean;partly:boolean;edited:boolean};

/** Curated skills, installed into the open project with one click, where Claude, Codex and OpenCode all find them. */
export function SkillCatalog({project}:{project:Project}){
 const {path,notify}=useWorkspace(),refresh=useRefresh();
 const base=`/projects/${project.id}/skill-catalog`;
 const list=useResource<Entry[]>(base);
 const [busy,setBusy]=useState(''),[error,setError]=useState('');
 async function act(entry:Entry,install:boolean){
  setBusy(entry.name);setError('');
  try{install?await api.post(path(`${base}/${entry.name}`)):await api.delete(path(`${base}/${entry.name}`));await refresh();notify(install?`${entry.title} installed in ${project.name}`:`${entry.title} removed`)}
  catch(e){setError((e as Error).message)}finally{setBusy('')}
 }
 const groups=[...new Set((list.data||[]).map(e=>e.category))];
 return <div className="skill-catalog">
  <p className="muted small">Installing writes the skill to <code>.claude/skills</code> and <code>.agents/skills</code> in {project.name}, so Claude, Codex and OpenCode each use it when a task matches. Commit it to share it with the project.</p>
  {error&&<p className="error-text">{error}</p>}
  {list.isPending?<p className="muted">Reading the catalog…</p>:groups.map(g=><section key={g}><h4>{g}</h4>{list.data!.filter(e=>e.category===g).map(e=><div key={e.name} className="catalog-row">
   <div><strong>{e.title}</strong><code>{e.name}</code><p>{e.description}</p>{e.edited&&<small className="muted">Edited in this project; Frontier leaves it alone.</small>}</div>
   {busy===e.name?<LoaderCircle size={14} className="spin"/>:e.installed||e.partly?<div className="catalog-actions"><span className="file-added"><Check size={12}/> Installed</span>{!e.edited&&<button className="icon-button" aria-label={`Remove ${e.title}`} title="Remove from this project" onClick={()=>act(e,false)}><Trash2 size={13}/></button>}</div>
    :<button onClick={()=>act(e,true)} aria-label={`Install ${e.title}`}><Download size={13}/>Install</button>}
  </div>)}</section>)}
 </div>;
}
