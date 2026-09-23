import {useEffect,useRef,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {RefreshCw,ExternalLink,Globe,ArrowRight} from 'lucide-react';
import {useWorkspace} from '../app/context';
import {api} from '../lib/api';

/** A dev server the project is running, shown in place. Ports are probed on loopback; any URL works too. */
export function Preview({projectId}:{projectId:string}){
 const {tenant,path}=useWorkspace();
 const ports=useQuery({queryKey:['tenant',tenant?.id,`/projects/${projectId}/ports`],enabled:!!tenant,refetchInterval:8000,queryFn:({signal})=>api.get<{ports:number[]}>(path(`/projects/${projectId}/ports`),signal)});
 const key=`frontier.preview.${projectId}`;
 const [url,setUrl]=useState(()=>localStorage.getItem(key)||''),[draft,setDraft]=useState(''),[nonce,setNonce]=useState(0);
 const frame=useRef<HTMLIFrameElement>(null);
 useEffect(()=>{setDraft(url);localStorage.setItem(key,url)},[url]);
 useEffect(()=>{if(!url&&ports.data?.ports.length)setUrl(`http://localhost:${ports.data.ports[0]}/`)},[ports.data]);
 const go=(u:string)=>{const v=u.trim();if(!v)return;setUrl(/^https?:\/\//.test(v)?v:`http://${v}`);setNonce(n=>n+1)};
 return <div className="preview-panel">
  <form className="preview-bar" onSubmit={e=>{e.preventDefault();go(draft)}}><Globe size={13}/><input aria-label="Preview URL" placeholder="http://localhost:3000" value={draft} onChange={e=>setDraft(e.target.value)}/><button className="icon-button" aria-label="Open URL" type="submit"><ArrowRight size={13}/></button><button className="icon-button" type="button" title="Reload" aria-label="Reload preview" onClick={()=>setNonce(n=>n+1)}><RefreshCw size={13}/></button><button className="icon-button" type="button" title="Open in your browser" aria-label="Open in your browser" disabled={!url} onClick={()=>window.open(url,'_blank')}><ExternalLink size={13}/></button></form>
  <div className="preview-ports">{ports.data?.ports.length?<>Listening: {ports.data.ports.map(p=><button key={p} className={url.includes(`:${p}/`)||url.endsWith(`:${p}`)?'selected':''} onClick={()=>go(`http://localhost:${p}/`)}>:{p}</button>)}</>:<span>No dev server found on the usual ports. Start one in the Terminal, or type a URL.</span>}</div>
  {url?<iframe key={`${url}#${nonce}`} ref={frame} className="preview-frame" src={url} title="Preview" sandbox="allow-scripts allow-forms allow-same-origin allow-popups allow-modals"/>:<p className="inspector-empty">Pick a port above or enter a URL.</p>}
 </div>;
}
