import {useState} from 'react';
import {Clock,Plus,Trash2,Pencil,Play,Webhook,Copy} from 'lucide-react';
import {useWorkspace,useResource,useRefresh} from '../../app/context';
import {Field,Modal,Confirm,PageHeading} from '../../components/ui';
import {api,date} from '../../lib/api';
import {environment} from '../../lib/environment';
import {ADAPTIVE,agentProviders,modes,modeLabels,type Automation,type Model,type Project,type Mode} from '../../types';

type Form={name:string;project_id:string;prompt:string;model_id:string;mode:Mode;kind:'every'|'daily'|'hook';every:string;daily_at:string;enabled:boolean};
const blank=(projectId:string):Form=>({name:'',project_id:projectId,prompt:'',model_id:ADAPTIVE,mode:'edit',kind:'daily',every:'60',daily_at:'09:00',enabled:true});

/** Turns that run on their own: on a schedule while Frontier is open, or when a webhook is called. */
export default function Automations({projects,models,currentProject}:{projects:Project[];models:Model[];currentProject?:Project}){
 const {path,notify,copy}=useWorkspace(),refresh=useRefresh();
 const list=useResource<Automation[]>('/automations');
 const agents=models.filter(m=>agentProviders.includes(m.provider));
 const [open,setOpen]=useState(false),[editing,setEditing]=useState<Automation|null>(null),[form,setForm]=useState<Form>(blank(currentProject?.id||projects[0]?.id||'')),[error,setError]=useState(''),[remove,setRemove]=useState<Automation|null>(null),[busy,setBusy]=useState('');
 function edit(a?:Automation){setEditing(a||null);setForm(a?{name:a.name,project_id:a.project_id,prompt:a.prompt,model_id:a.model_id,mode:a.mode,kind:a.every?'every':a.daily_at?'daily':'hook',every:String(a.every||60),daily_at:a.daily_at||'09:00',enabled:a.enabled}:blank(currentProject?.id||projects[0]?.id||''));setError('');setOpen(true)}
 async function save(e:React.FormEvent){e.preventDefault();setBusy('save');setError('');try{
  const payload={name:form.name.trim(),project_id:form.project_id,prompt:form.prompt.trim(),model_id:form.model_id,mode:form.mode,every:form.kind==='every'?Number(form.every):null,daily_at:form.kind==='daily'?form.daily_at:null,enabled:form.enabled};
  if(editing)await api.put(path(`/automations/${editing.id}`),payload);else await api.post(path('/automations'),payload);
  setOpen(false);await refresh();notify('Automation saved')}catch(e){setError((e as Error).message)}finally{setBusy('')}}
 const hookUrl=(a:Automation)=>`${environment?environment.url:location.origin}/api/hooks/${a.id}/${a.secret}`;
 return <>
  <PageHeading eyebrow="TURNS THAT RUN ON THEIR OWN" title="Automations" description="Each run opens a session in the project and sends the prompt as one turn under the posture you choose. Schedules only fire while Frontier is open."><button className="primary" onClick={()=>edit()} disabled={!projects.length||!agents.length}><Plus size={16}/>New automation</button></PageHeading>
  {!agents.length&&<p className="muted">Connect an agent first.</p>}
  {list.error&&<p className="error-text">{list.error.message}</p>}
  {!list.data?.length?<p className="muted">Nothing scheduled yet.</p>:<div className="automation-list">{list.data.map(a=>{const p=projects.find(x=>x.id===a.project_id);return <article key={a.id} className={`automation-card ${a.enabled?'':'off'}`}>
   <div className="automation-head"><Clock size={15}/><strong>{a.name}</strong><small>{p?.name||'Removed project'} · {modeLabels[a.mode]} · {a.model_id===ADAPTIVE?'Adaptive':models.find(m=>m.id===a.model_id)?.name||'Agent'}</small>
    <button className="icon-button" title="Run now" aria-label={`Run ${a.name} now`} disabled={busy===a.id} onClick={async()=>{setBusy(a.id);try{await api.post(path(`/automations/${a.id}/run`));await refresh();notify('Run started')}catch(e){notify((e as Error).message)}finally{setBusy('')}}}><Play size={14}/></button>
    <button className="icon-button" aria-label={`Edit ${a.name}`} onClick={()=>edit(a)}><Pencil size={14}/></button><button className="icon-button" aria-label={`Remove ${a.name}`} onClick={()=>setRemove(a)}><Trash2 size={14}/></button></div>
   <p className="automation-prompt">{a.prompt}</p>
   <dl className="metadata"><dt>Schedule</dt><dd>{a.every?`Every ${a.every} minutes`:a.daily_at?`Daily at ${a.daily_at}`:'Webhook only'}{!a.enabled&&' · disabled'}</dd><dt>Next run</dt><dd>{a.enabled&&a.next_run_at?date(a.next_run_at):'—'}</dd><dt>Last run</dt><dd>{a.last_run_at?`${date(a.last_run_at)} · ${a.last_trigger} · ${a.last_outcome}`:'Never'} · {a.runs||0} total</dd><dt>Webhook</dt><dd className="hook-row"><code>{hookUrl(a)}</code><button className="icon-button" title="Copy webhook URL" aria-label="Copy webhook URL" onClick={()=>copy(hookUrl(a),'Webhook URL copied')}><Copy size={13}/></button></dd></dl>
  </article>})}</div>}
  <p className="muted small"><Webhook size={12}/> A webhook is a POST to its URL; the secret in the URL is the whole credential, so treat it like a password. With remote access on, the URL works from other devices.</p>
  <Modal open={open} onClose={()=>setOpen(false)} title={editing?'Edit automation':'New automation'} description="One prompt, sent as a turn whenever it fires."><form onSubmit={save}>{error&&<p className="error-text" role="alert">{error}</p>}
   <Field label="Name"><input required maxLength={80} value={form.name} onChange={e=>setForm({...form,name:e.target.value})} placeholder="Nightly review"/></Field>
   <div className="form-grid"><Field label="Project"><select value={form.project_id} onChange={e=>setForm({...form,project_id:e.target.value})}>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></Field><Field label="Agent"><select value={form.model_id} onChange={e=>setForm({...form,model_id:e.target.value})}><option value={ADAPTIVE}>Adaptive</option>{agents.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></Field></div>
   <div className="form-grid"><Field label="What the agent may do"><select value={form.mode} onChange={e=>setForm({...form,mode:e.target.value as Mode})}>{modes.map(m=><option key={m} value={m}>{modeLabels[m]}</option>)}</select></Field><Field label="Trigger"><select value={form.kind} onChange={e=>setForm({...form,kind:e.target.value as Form['kind']})}><option value="daily">Daily at a time</option><option value="every">Every N minutes</option><option value="hook">Webhook only</option></select></Field></div>
   {form.kind==='every'&&<Field label="Every (minutes)"><input type="number" min={1} max={10080} value={form.every} onChange={e=>setForm({...form,every:e.target.value})}/></Field>}
   {form.kind==='daily'&&<Field label="Time (local)"><input type="time" value={form.daily_at} onChange={e=>setForm({...form,daily_at:e.target.value})}/></Field>}
   <Field label="Prompt"><textarea required rows={5} value={form.prompt} onChange={e=>setForm({...form,prompt:e.target.value})} placeholder="Review the last day of commits for bugs and leave a report in REVIEW.md."/></Field>
   <label className="toggle-row"><div><strong>Enabled</strong><p>A disabled automation keeps its settings but never fires, not even by webhook.</p></div><input type="checkbox" role="switch" checked={form.enabled} onChange={e=>setForm({...form,enabled:e.target.checked})}/></label>
   <div className="actions end"><button type="button" onClick={()=>setOpen(false)}>Cancel</button><button className="primary" disabled={busy==='save'}>{busy==='save'?'Saving…':'Save automation'}</button></div></form></Modal>
  <Confirm open={!!remove} onClose={()=>setRemove(null)} title={`Remove ${remove?.name}?`} description="Sessions it already opened stay in the project." onConfirm={async()=>{if(!remove)return;try{await api.delete(path(`/automations/${remove.id}`));await refresh()}catch(e){notify((e as Error).message)}setRemove(null)}}/>
 </>;
}
