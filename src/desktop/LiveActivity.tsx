import {useEffect,useRef,useState} from 'react';
import {LoaderCircle} from 'lucide-react';
import {api} from '../lib/api';
import {useWorkspace} from '../app/context';

type Line={at:string;text:string};
type Live={running:boolean;agent:string|null;started:string|null;total:number;lines:Line[]};

/** What the agent is doing right now, line by line: the commands it runs, the files it reads and edits, what it says. */
export function LiveActivity({projectId,sessionId}:{projectId:string;sessionId?:string}){
 const {path}=useWorkspace();
 const [lines,setLines]=useState<Line[]>([]),[state,setState]=useState<Omit<Live,'lines'>|null>(null),[error,setError]=useState('');
 const box=useRef<HTMLDivElement>(null),follow=useRef(true),seen=useRef(0);
 useEffect(()=>{
  setLines([]);setState(null);seen.current=0;
  if(!sessionId)return;
  let alive=true,timer=0;
  // Only what is new is asked for; a turn that started afresh (fewer lines than seen) starts the view over.
  const poll=async()=>{
   try{
    const live=await api.get<Live>(path(`/projects/${projectId}/sessions/${sessionId}/live?since=${seen.current}`));
    if(!alive)return;
    if(live.total<seen.current){seen.current=0;setLines(live.lines)}else setLines(v=>[...v,...live.lines].slice(-600));
    seen.current=live.total;setState(live);setError('');
    timer=window.setTimeout(poll,live.running?1200:4000);
   }catch(e){if(alive){setError((e as Error).message);timer=window.setTimeout(poll,5000)}}
  };
  void poll();
  return()=>{alive=false;window.clearTimeout(timer)};
 },[projectId,sessionId,path]);
 // Stay at the bottom while the user is there; scrolling up to read stops the follow.
 useEffect(()=>{const el=box.current;if(el&&follow.current)el.scrollTop=el.scrollHeight},[lines]);
 if(!sessionId)return <p className="inspector-empty">Open a conversation to follow its agent.</p>;
 return <div className="live-activity">
  <div className="live-head">{state?.running?<><LoaderCircle size={12} className="spin"/><strong>{state.agent||'The agent'}</strong> is working</>:<span>{lines.length?`Last turn${state?.agent?` · ${state.agent}`:''}`:'Nothing yet'}</span>}</div>
  {error&&<p className="thread-error">{error}</p>}
  <div className="live-lines" ref={box} onScroll={e=>{const el=e.currentTarget;follow.current=el.scrollHeight-el.scrollTop-el.clientHeight<40}}>
   {!lines.length&&<p className="inspector-empty">{state?.running?'Waiting for the agent’s first step…':'When an agent works in this conversation, every command it runs, file it reads or edits, and what it says appears here as it happens.'}</p>}
   {lines.map((l,i)=><div key={i} className={`live-line ${l.text.startsWith('▸')?'tool':l.text.startsWith('  ↳')?'result':l.text.startsWith('—')?'note':l.text.startsWith('⚠')?'warn':''}`}><time>{new Date(l.at).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'})}</time><span>{l.text}</span></div>)}
  </div>
 </div>;
}
