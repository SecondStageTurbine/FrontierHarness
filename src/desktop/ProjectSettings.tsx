import {useEffect,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {KeyRound,Download,Brain,GitBranch,Play,ShieldCheck} from 'lucide-react';
import {useWorkspace,useRefresh} from '../app/context';
import {Field,Modal} from '../components/ui';
import {api} from '../lib/api';
import {ADAPTIVE,agentProviders,modes,modeLabels,type Model,type Project,type Mode} from '../types';

/** Per-project settings: defaults for new sessions, worktree setup, the dev server, secrets, and memory. */
export function ProjectSettings({project,models,open,onClose}:{project:Project;models:Model[];open:boolean;onClose:()=>void}){
 const {path,notify}=useWorkspace(),refresh=useRefresh();
 const agents=models.filter(m=>agentProviders.includes(m.provider));
 const [form,setForm]=useState({default_mode:'',default_model_id:'',worktree_setup:'',worktree_copy:'',protect_env:true,dev_command:'',memory:'',turn_minutes:''});
 const [busy,setBusy]=useState(''),[error,setError]=useState(''),[imported,setImported]=useState<string>('');
 const credentials=useQuery({queryKey:['tenant',project.id,'credentials'],enabled:open,queryFn:({signal})=>api.get<{file:string;names:string[]}[]>(path(`/projects/${project.id}/credentials`),signal)});
 useEffect(()=>{if(open)setForm({default_mode:project.default_mode||'',default_model_id:project.default_model_id||'',worktree_setup:project.worktree_setup||'',worktree_copy:(project.worktree_copy||[]).join(', '),protect_env:project.protect_env!==false,dev_command:project.dev_command||'',memory:project.memory||'',turn_minutes:project.turn_minutes?String(project.turn_minutes):''})},[open,project.id]);
 async function save(e:React.FormEvent){e.preventDefault();setBusy('save');setError('');try{
  await api.put(path(`/projects/${project.id}/settings`),{default_mode:form.default_mode||null,default_model_id:form.default_model_id||null,worktree_setup:form.worktree_setup.trim()||null,worktree_copy:form.worktree_copy.split(',').map(s=>s.trim()).filter(Boolean),protect_env:form.protect_env,dev_command:form.dev_command.trim()||null,memory:form.memory.trim()||null,turn_minutes:form.turn_minutes?Number(form.turn_minutes):null});
  await refresh();notify('Project settings saved');onClose()}catch(err){setError((err as Error).message)}finally{setBusy('')}}
 async function importHistory(){setBusy('import');setError('');try{const made=await api.post<{name:string;source:string;messages:number}[]>(path(`/projects/${project.id}/import`),{sources:['claude','codex']});setImported(made.length?`${made.length} conversation${made.length===1?'':'s'} imported: ${made.map(m=>`${m.name} (${m.source}, ${m.messages} messages)`).join('; ')}`:'Nothing new to import.');await refresh()}catch(err){setError((err as Error).message)}finally{setBusy('')}}
 return <Modal open={open} onClose={onClose} title={`${project.name} · settings`} description="What new sessions in this project start with, and what the agents are told." wide>
  <form onSubmit={save} className="project-settings">{error&&<p className="error-text" role="alert">{error}</p>}
   <div className="form-grid"><Field label="Default agent" hint="For new sessions; Adaptive picks per message."><select value={form.default_model_id} onChange={e=>setForm({...form,default_model_id:e.target.value})}><option value="">Adaptive (default)</option>{agents.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}<option value={ADAPTIVE}>Adaptive</option></select></Field>
    <Field label="Turn time limit (minutes)" hint="How long one agent may work on a turn before it is stopped. Raise it for long builds or engine test runs. 30 by default."><input type="number" min={5} max={240} value={form.turn_minutes} onChange={e=>setForm({...form,turn_minutes:e.target.value})} placeholder="30"/></Field>
    <Field label="Default posture"><select value={form.default_mode} onChange={e=>setForm({...form,default_mode:e.target.value})}><option value="">Edit files (default)</option>{modes.map(m=><option key={m} value={m}>{modeLabels[m as Mode]}</option>)}</select></Field></div>
   <h3><GitBranch size={15}/> Worktrees</h3>
   <div className="form-grid"><Field label="Setup command" hint="Run once in every new worktree, such as npm ci. Your shell, your environment, five-minute limit."><input value={form.worktree_setup} onChange={e=>setForm({...form,worktree_setup:e.target.value})} placeholder="npm ci"/></Field>
    <Field label="Copy into worktrees" hint="Ignored files a fresh branch needs, comma separated. Copied, never linked."><input value={form.worktree_copy} onChange={e=>setForm({...form,worktree_copy:e.target.value})} placeholder=".env, .env.local"/></Field></div>
   <h3><Play size={15}/> Dev server</h3>
   <Field label="Command" hint="Started and stopped from the Preview panel, in the session's folder."><input value={form.dev_command} onChange={e=>setForm({...form,dev_command:e.target.value})} placeholder="npm run dev"/></Field>
   <h3><KeyRound size={15}/> Credentials</h3>
   <label className="toggle-row"><div><strong>Keep .env files away from Claude</strong><p>Claude may not open .env files under any posture. Codex and OpenCode have no such switch; their sandboxes decide.</p></div><input type="checkbox" role="switch" checked={form.protect_env} onChange={e=>setForm({...form,protect_env:e.target.checked})}/></label>
   {credentials.data?.length?<div className="credential-list">{credentials.data.map(f=><div key={f.file}><strong>{f.file}</strong><span>{f.names.length?f.names.join(', '):'no variables'}</span></div>)}</div>:<p className="muted small"><ShieldCheck size={12}/> No .env files in this folder. Values are never read here, only names.</p>}
   <h3><Brain size={15}/> Project memory</h3>
   <Field label="Notes every agent is given" hint="Conventions, decisions, where things live. Right-click a session and choose Remember this session to let the agent add to it."><textarea rows={6} value={form.memory} onChange={e=>setForm({...form,memory:e.target.value})} placeholder="- Tests live in tests/ and run with pytest -q"/></Field>
   <h3><Download size={15}/> Import conversations</h3>
   <p className="muted small">Bring in what Claude Code and Codex kept about this folder as sessions here. Each conversation is imported once.</p>
   <div className="actions"><button type="button" disabled={busy==='import'} onClick={importHistory}>{busy==='import'?'Importing…':'Import from Claude and Codex'}</button>{imported&&<span className="muted small">{imported}</span>}</div>
   <div className="actions end"><button type="button" onClick={onClose}>Cancel</button><button className="primary" disabled={busy==='save'}>{busy==='save'?'Saving…':'Save settings'}</button></div>
  </form>
 </Modal>;
}
