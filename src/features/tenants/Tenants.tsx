import {useState} from 'react';
import {Plus,Trash2,Layers} from 'lucide-react';
import {useWorkspace,useResource} from '../../app/context';
import {PageHeading,Modal,Field,ErrorState} from '../../components/ui';
import {api} from '../../lib/api';
import {agentProviders,providerNames,type Model,type Tenant} from '../../types';
type TenantForm={name:string;environment:string;router_model_id:string};
const blank:TenantForm={name:'',environment:'Development',router_model_id:''};

/** Workspaces separate projects, conversations and connected agents from each other. They no
 *  longer carry budgets or routing defaults: an agent is chosen per turn, in the conversation. */
export default function Tenants(){
 const models=useResource<Model[]>('/models');const {tenant,tenants,switchTenant,notify,refreshTenants}=useWorkspace();
 // Any keyed model can classify for Adaptive; an agent cannot, because a turn is the wrong
 // shape for a two-second structured answer. A local Ollama model is the intended choice.
 const classifiers=(models.data||[]).filter(m=>!agentProviders.includes(m.provider));
 const [open,setOpen]=useState(false),[editing,setEditing]=useState<Tenant|null>(null),[form,setForm]=useState<TenantForm>(blank),[error,setError]=useState<Error|null>(null),[busy,setBusy]=useState(false),[remove,setRemove]=useState<Tenant|null>(null),[confirmName,setConfirmName]=useState('');
 function edit(t?:Tenant){setEditing(t||null);setForm(t?{name:t.name,environment:t.environment,router_model_id:t.router_model_id||''}:blank);setError(null);setOpen(true)}
 async function save(e:React.FormEvent){e.preventDefault();setBusy(true);setError(null);
  const payload={...form,router_model_id:form.router_model_id||null};
  try{if(editing)await api.put(`/tenants/${editing.id}`,payload);else await api.post('/tenants',payload);setOpen(false);setForm(blank);notify('Workspace saved');refreshTenants()}
  catch(e){setError(e as Error)}finally{setBusy(false)}}
 return <><PageHeading eyebrow="ISOLATION BOUNDARY" title="Workspaces" description="Projects, conversations, and connected agents belong to one workspace and are never shared across them."><button className="primary" onClick={()=>edit()}><Plus size={16}/>New workspace</button></PageHeading>
 {error&&!open&&<ErrorState error={error}/>}
 <div className="model-grid">{tenants.map(t=><article className="model-card" key={t.id}>
  <div className="section-head"><span className="provider-symbol"><Layers size={22}/></span>{tenant?.id===t.id?<small className="muted">Current</small>:<button onClick={()=>switchTenant(t.id)}>Switch to</button>}</div>
  <h3>{t.name}</h3><p className="model-id">{t.environment}{t.router_model_id&&` · Adaptive classifier: ${(models.data||[]).find(m=>m.id===t.router_model_id)?.name||'set'}`}</p>
  <div className="model-card-footer"><button onClick={()=>edit(t)}>Rename</button><button className="icon-button" aria-label={`Delete ${t.name}`} title="Delete workspace" onClick={()=>{setConfirmName('');setRemove(t)}}><Trash2 size={15}/></button></div>
 </article>)}</div>
 <Modal open={open} onClose={()=>setOpen(false)} title={editing?'Workspace':'New workspace'} description="A separate set of projects, conversations, and agent logins.">
  <form onSubmit={save}>{error&&<p className="error-text" role="alert">{error.message}</p>}
   <Field label="Name"><input required maxLength={100} value={form.name} onChange={e=>setForm({...form,name:e.target.value})} autoFocus/></Field>
   <Field label="Environment" hint="A label for you. It changes nothing about how agents run."><input maxLength={50} value={form.environment} onChange={e=>setForm({...form,environment:e.target.value})}/></Field>
   {(!editing||editing.id===tenant?.id)&&<Field label="Adaptive classifier" hint="A small keyed model Adaptive asks what a message needs before choosing an agent. Without one, Adaptive uses built-in heuristics, which it also falls back to whenever the classifier fails."><select value={form.router_model_id} onChange={e=>setForm({...form,router_model_id:e.target.value})}><option value="">Heuristics only</option>{classifiers.map(m=><option key={m.id} value={m.id}>{providerNames[m.provider]||m.provider} · {m.name}</option>)}</select></Field>}
   <div className="actions end"><button type="button" onClick={()=>setOpen(false)}>Cancel</button><button className="primary" disabled={busy}>{busy?'Saving…':'Save workspace'}</button></div>
  </form>
 </Modal>
 <Modal open={!!remove} onClose={()=>setRemove(null)} title="Delete this workspace?" description="Its projects, conversations, and connected agent logins are removed from Frontier. Files already written to your project folders stay where they are.">
  <Field label={`Type ${remove?.name} to confirm`}><input value={confirmName} onChange={e=>setConfirmName(e.target.value)}/></Field>
  <div className="actions end"><button onClick={()=>setRemove(null)}>Keep it</button><button className="danger" disabled={busy||confirmName!==remove?.name} onClick={async()=>{if(!remove)return;setBusy(true);try{await api.delete(`/tenants/${remove.id}?name=${encodeURIComponent(remove.name)}`);setRemove(null);notify('Workspace deleted');refreshTenants()}catch(e){setError(e as Error)}finally{setBusy(false)}}}>Delete workspace</button></div>
 </Modal>
 </>;
}
