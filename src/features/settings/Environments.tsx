import {useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {Monitor,Server,Trash2,Link2,ArrowRightLeft} from 'lucide-react';
import {useWorkspace} from '../../app/context';
import {Field,PageHeading} from '../../components/ui';
import {api} from '../../lib/api';
import {environment,switchEnvironment} from '../../lib/environment';

type Env={id:string;name:string;url:string;reachable:boolean;signed_in:boolean};

/** Other computers running Frontier, paired once and then worked on from this window. */
export default function Environments(){
 const {notify}=useWorkspace();const client=useQueryClient();
 const list=useQuery({queryKey:['environments'],queryFn:({signal})=>api.get<Env[]>('/environments',signal),refetchInterval:15000});
 const [name,setName]=useState(''),[link,setLink]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function add(e:React.FormEvent){e.preventDefault();setBusy(true);setError('');try{const env=await api.post<Env>('/environments',{name,link});setName('');setLink('');await client.invalidateQueries({queryKey:['environments']});notify(`${env.name} is paired`)}catch(err){setError((err as Error).message)}finally{setBusy(false)}}
 async function remove(env:Env){try{await api.delete(`/environments/${env.id}`);if(environment?.id===env.id)switchEnvironment(null);await client.invalidateQueries({queryKey:['environments']})}catch(err){setError((err as Error).message)}}
 return <>
  <PageHeading eyebrow="OTHER MACHINES" title="Environments" description="Work on another computer's projects from this window: its agents run there, on its files, with its tools and sign-ins."/>
  <div className="env-list">
   <div className={`env-row${!environment?' current':''}`}><Monitor size={16}/><div><strong>This computer</strong><small>Projects and agents on this machine</small></div>{environment?<button onClick={()=>switchEnvironment(null)}><ArrowRightLeft size={13}/>Switch here</button>:<span className="env-current">Current</span>}</div>
   {list.data?.map(e=><div key={e.id} className={`env-row${environment?.id===e.id?' current':''}`}><Server size={16}/><div><strong>{e.name}</strong><small>{e.url} · {!e.reachable?'not reachable now':!e.signed_in?'needs pairing again':'ready'}</small></div>
    {environment?.id===e.id?<span className="env-current">Current</span>:<button disabled={!e.reachable||!e.signed_in} onClick={()=>switchEnvironment(e)}><ArrowRightLeft size={13}/>Switch to it</button>}
    <button className="icon-button" aria-label={`Remove ${e.name}`} title="Forget this machine here" onClick={()=>remove(e)}><Trash2 size={13}/></button></div>)}
  </div>
  <h3><Link2 size={16}/> Pair another machine</h3>
  <p className="muted">On the other computer, open Settings → Remote access, turn it on, and press <strong>Show pairing code</strong>, then <strong>Copy link</strong>. Paste the link here within ten minutes. Both machines need to share a network or a tailnet such as Tailscale.</p>
  {error&&<p className="error-text" role="alert">{error}</p>}
  <form onSubmit={add} className="form-grid"><Field label="Name"><input value={name} onChange={e=>setName(e.target.value)} placeholder="Studio PC"/></Field><Field label="Pairing link"><input required value={link} onChange={e=>setLink(e.target.value)} placeholder="http://192.168.1.20:8765/pair?code=…"/></Field><div className="actions"><button className="primary" disabled={busy||!link.trim()}>{busy?'Pairing…':'Pair'}</button></div></form>
  <p className="muted small">On another machine the terminal, the folder picker and Open in editor are off, because they act on the computer in front of you. Everything else, including files, git, pull requests and the preview's agent browser, runs there.</p>
 </>;
}
