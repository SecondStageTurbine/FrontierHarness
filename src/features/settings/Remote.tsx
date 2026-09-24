import {useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {Globe,KeyRound,Copy} from 'lucide-react';
import {useWorkspace} from '../../app/context';
import {Field,PageHeading} from '../../components/ui';
import {Phone} from './Phone';
import {api} from '../../lib/api';

type Status={enabled:boolean;listening:boolean;addresses:string[];port:number|null;has_password:boolean;username:string};

/** Reaching this Frontier from a phone or another computer on the same network or tailnet. */
export default function Remote(){
 const {notify,copy}=useWorkspace();const client=useQueryClient();
 const q=useQuery({queryKey:['remote'],queryFn:({signal})=>api.get<Status>('/remote',signal)});
 const [password,setPassword]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[restart,setRestart]=useState(false);
 const s=q.data;
 async function toggle(enabled:boolean){setBusy(true);setError('');try{const r=await api.put<{enabled:boolean;restart_required:boolean}>('/remote',{enabled});setRestart(r.restart_required);await client.invalidateQueries({queryKey:['remote']})}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 async function setPass(e:React.FormEvent){e.preventDefault();setBusy(true);setError('');try{await api.post('/password',{password});setPassword('');await client.invalidateQueries({queryKey:['remote']});notify('Password set')}catch(err){setError((err as Error).message)}finally{setBusy(false)}}
 return <>
  <PageHeading eyebrow="FROM ANOTHER DEVICE" title="Remote access" description="Off, Frontier answers only this window. On, it also answers browsers on your network or tailnet, signed in with your password."/>
  {q.error&&<p className="error-text">{q.error.message}</p>}
  {error&&<p className="error-text" role="alert">{error}</p>}
  {s&&<>
   <label className="toggle-row"><div><strong>Answer other devices</strong><p>{s.enabled?(s.listening?'Listening on every network interface.':'Turned on. Restart Frontier to start listening.'):'Only this window can reach Frontier.'}</p></div><input type="checkbox" role="switch" checked={s.enabled} disabled={busy} onChange={e=>toggle(e.target.checked)}/></label>
   {restart&&<p className="notice"><Globe size={16}/><span>Quit Frontier and start it again for this to take effect.</span></p>}
   <h3><KeyRound size={16}/> Password</h3>
   <p className="muted">{s.has_password?`Signed in as ${s.username}. Other devices sign in with that name and the password below.`:`This window signs in on its own; ${s.username} has no password yet. Set one so another device can sign in.`}</p>
   <form onSubmit={setPass} className="form-grid"><Field label={s.has_password?'New password':'Password'} hint="At least 12 characters."><input type="password" autoComplete="new-password" minLength={12} required value={password} onChange={e=>setPassword(e.target.value)}/></Field><div className="actions"><button className="primary" disabled={busy||password.length<12}>{s.has_password?'Change password':'Set password'}</button></div></form>
   <h3><Globe size={16}/> Addresses</h3>
   {!s.enabled?<p className="muted">Turn remote access on to see where to connect.</p>:!s.addresses.length?<p className="muted">No network address found on this machine.</p>:<ul className="address-list">{s.addresses.map(a=>{const url=`http://${a}:${s.port??''}`;return <li key={a}><code>{url}</code><button className="icon-button" aria-label={`Copy ${url}`} onClick={()=>copy(url,'Address copied')}><Copy size={13}/></button></li>})}</ul>}
   <p className="muted small">The connection is plain HTTP, so use it on a network you trust or over a tailnet such as Tailscale, whose address appears above when it is running. In a browser the terminal, updates and desktop notifications are unavailable; everything else works, on a phone too.</p>
   <Phone listening={s.enabled&&s.listening}/>
  </>}
 </>;
}
