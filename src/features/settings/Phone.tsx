import {useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {BellRing,Copy,QrCode,RefreshCw,Send,Smartphone} from 'lucide-react';
import {useWorkspace} from '../../app/context';
import {Qr} from '../../components/Qr';
import {api} from '../../lib/api';

type Push={enabled:boolean;server:string;topic:string|null;events:string[];details:boolean;subscribe_url:string|null};
const EVENTS:[string,string][]=[['done','A turn finished'],['failed','A turn stopped with an error'],['waiting','An agent is waiting on you (a question or a permission)']];

/** Pair a phone by scanning a code, and send it notifications. Shown in Remote access. */
export function Phone({listening}:{listening:boolean}){
 const {notify,copy}=useWorkspace();const client=useQueryClient();
 const [pairing,setPairing]=useState<{urls:string[];expires:number}|null>(null),[address,setAddress]=useState(0);
 const [error,setError]=useState(''),[busy,setBusy]=useState('');
 const push=useQuery({queryKey:['push'],queryFn:({signal})=>api.get<Push>('/push',signal)});
 const [server,setServer]=useState<string|null>(null);
 async function act(what:string,run:()=>Promise<unknown>){setBusy(what);setError('');try{await run()}catch(e){setError((e as Error).message)}finally{setBusy('')}}
 const save=(data:Partial<Push>&{new_topic?:boolean})=>act('push',async()=>{await api.put('/push',data);await client.invalidateQueries({queryKey:['push']})});
 const p=push.data;
 const url=pairing?.urls[address];
 return <>
  <h3><Smartphone size={16}/> Pair a phone</h3>
  {!listening?<p className="muted">Turn remote access on and restart Frontier, then pair your phone here.</p>:<>
   <p className="muted">Scan with the phone's camera. It opens Frontier signed in as you, with no password to type. The code works once, for ten minutes, and only on your network or tailnet.</p>
   {pairing&&url?<div className="pair-box"><Qr text={url} label="Pairing code for your phone"/><div>
     {pairing.urls.length>1&&<select aria-label="Address" value={address} onChange={e=>setAddress(Number(e.target.value))}>{pairing.urls.map((u,i)=><option key={u} value={i}>{new URL(u).host}</option>)}</select>}
     <small className="muted">Expires {new Date(pairing.expires).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})}. If the phone cannot open it, try another address; a phone on mobile data needs a tailnet such as Tailscale.</small>
     <div className="actions"><button onClick={()=>copy(url,'Pairing link copied')}><Copy size={13}/>Copy link</button><button onClick={()=>setPairing(null)}>Done</button></div>
     <small className="muted">The link also pairs another computer's Frontier: paste it in Settings → Environments there.</small></div></div>
   :<button disabled={busy!==''} onClick={()=>act('pair',async()=>{const r=await api.post<{urls:string[];expires_in:number}>('/remote/pair');if(!r.urls.length)throw new Error('No network address found to pair over.');setAddress(0);setPairing({urls:r.urls,expires:Date.now()+r.expires_in*1000})})}><QrCode size={14}/>Show pairing code</button>}
  </>}
  <h3><BellRing size={16}/> Push notifications</h3>
  <p className="muted">Phones only allow web notifications from secure sites, which a local address is not, so Frontier sends them through <a href="https://ntfy.sh" target="_blank" rel="noreferrer">ntfy</a>, a free notification app for Android and iPhone. Install it, subscribe to the topic below (scan it), and Frontier tells you when an agent finishes or needs you. Tapping one opens the conversation.</p>
  {error&&<p className="error-text" role="alert">{error}</p>}
  {p&&<>
   <label className="toggle-row"><div><strong>Send push notifications</strong><p>{p.enabled?`To ${p.server}/${p.topic}`:'Off.'}</p></div><input type="checkbox" role="switch" checked={p.enabled} disabled={busy!==''} onChange={e=>save({enabled:e.target.checked})}/></label>
   {p.enabled&&p.subscribe_url&&<>
    <div className="pair-box"><Qr text={p.subscribe_url} label="Topic to subscribe to in ntfy"/><div>
     <code className="push-topic">{p.subscribe_url}</code>
     <div className="actions"><button onClick={()=>copy(p.subscribe_url!,'Topic copied')}><Copy size={13}/>Copy</button>
      <button disabled={busy!==''} onClick={()=>act('test',async()=>{await api.post('/push/test');notify('Test notification sent')})}><Send size={13}/>Send a test</button>
      <button disabled={busy!==''} title="Anyone who knows the topic can read the notifications. A new one cuts off every device subscribed to the old." onClick={()=>save({new_topic:true})}><RefreshCw size={13}/>New topic</button></div>
     <small className="muted">The topic name is the key: keep it private. Notifications name the agent, the project and the session.</small></div></div>
    <div className="push-events">{EVENTS.map(([id,label])=><label key={id}><input type="checkbox" checked={p.events.includes(id)} onChange={e=>save({events:e.target.checked?[...p.events,id]:p.events.filter(x=>x!==id)})}/>{label}</label>)}
     <label><input type="checkbox" checked={p.details} onChange={e=>save({details:e.target.checked})}/>Include the start of the agent's reply (it passes through the ntfy server)</label></div>
    <div className="pr-inline"><label>Server</label><input aria-label="ntfy server" value={server??p.server} onChange={e=>setServer(e.target.value)} placeholder="https://ntfy.sh"/><button disabled={busy!==''||!server||server===p.server} onClick={()=>save({server:server!}).then(()=>setServer(null))}>Save</button></div>
    <small className="muted">Run your own ntfy server to keep notifications off the public one.</small>
   </>}
  </>}
 </>;
}
