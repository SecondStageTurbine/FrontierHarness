import {useState} from 'react';
import {Check,ChevronRight,LoaderCircle,FileCode2,AlertTriangle,Square,ArrowRightLeft,Eye,Pencil,Zap,Clock,X,Undo2} from 'lucide-react';
import {MarkdownOutput} from '../components/Markdown';
import {duration,money} from '../lib/api';
import {modeLabels,type Message,type Mode,type Session} from '../types';
export type InspectorTab='Files'|'Changes'|'Terminal';
const modeIcon={read:Eye,edit:Pencil,auto:Zap};

export function Conversation({session,onInspect,onUnqueue,onRevert}:{session:Session;onInspect:(tab:InspectorTab,path?:string,line?:number)=>void;onUnqueue?:(id:string)=>void;onRevert?:(id:string)=>void}){
 const [visible,setVisible]=useState(20);
 const shown=session.messages.slice(-visible);
 const queue=session.queue||[];
 return <div className="conversation-thread">
  {session.messages.length>visible&&<button className="history-more" onClick={()=>setVisible(v=>v+20)}>Load earlier messages</button>}
  {shown.map(message=>message.role==='user'
   ?<section className="conversation-turn" key={message.id}><div className="user-message"><span className="message-author">You</span><p>{message.content}</p></div></section>
   :<AgentMessage key={message.id} message={message} onInspect={onInspect} onRevert={onRevert}/>)}
  {queue.map((q,i)=><section className="conversation-turn queued" key={q.id}><div className="user-message"><span className="message-author"><Clock size={11}/> Queued {i+1} of {queue.length} · {modeLabels[q.mode]}</span><p>{q.content}</p>{onUnqueue&&<button className="icon-button" aria-label="Remove from queue" title="Remove from queue" onClick={()=>onUnqueue(q.id)}><X size={12}/></button>}</div></section>)}
 </div>;
}

/** What a finished turn cost: time, tokens in and out, money when the model has rates, and how many agents it took. */
function turnStats(m:Message){
 const parts:string[]=[];
 const seconds=m.finished_at?Math.round((new Date(m.finished_at).getTime()-new Date(m.created_at).getTime())/1000):0;
 if(seconds>2)parts.push(duration(m.created_at,m.finished_at));
 if(m.input_tokens!=null||m.output_tokens!=null)parts.push(`${compact(m.input_tokens||0)} in · ${compact(m.output_tokens||0)} out`);
 if(m.cost)parts.push(money(m.cost)); // 0 is a subscription turn: covered, not worth a line.
 const attempts=m.routing?.attempts?.length||0;
 if(attempts>1)parts.push(`${attempts} attempts`);
 return parts.join(' · ');
}
const compact=(n:number)=>n>=1000?`${(n/1000).toFixed(n>=10000?0:1)}k`:String(n);

function AgentMessage({message,onInspect,onRevert}:{message:Message;onInspect:(tab:InspectorTab,path?:string,line?:number)=>void;onRevert?:(id:string)=>void}){
 const running=message.status==='running';
 const ModeIcon=modeIcon[(message.mode||'edit') as Mode];
 const changes=message.changes||[];
 return <div className={`agent-turn ${running?'working':''}`}>
  <div className="frontier-author">
   <span className="frontier-spark">{running?<LoaderCircle size={14} className="spin"/>:'✳'}</span>
   <strong>{message.model_name||'Agent'}</strong>
   <span className="agent-mode-badge" title={modeLabels[(message.mode||'edit') as Mode]}><ModeIcon size={12}/>{modeLabels[(message.mode||'edit') as Mode]}</span>
   <small>{running?`Working · ${duration(message.created_at,null)}`:[message.status!=='complete'?message.status:'',turnStats(message)].filter(Boolean).join(' · ')}</small>
  </div>
  {message.switched_from&&<div className="handover-note"><ArrowRightLeft size={13}/>Took over from {message.switched_from}. It was given this conversation and the project folder.</div>}
  {message.routing?.mode==='adaptive'&&message.routing.chosen&&<div className="routing-note" title={message.routing.requirements?.reason}>
   <span>Adaptive</span>{message.routing.attempts&&message.routing.attempts.length>1
    ?message.routing.attempts.map((a,i)=><span key={a.id+i}>{i>0&&' → '}<strong>{a.name}</strong>{a.outcome!=='completed'&&<em> · {a.outcome}</em>}</span>)
    :<><strong>{message.routing.chosen.name}</strong><em> · {message.routing.chosen.because}</em></>}
  </div>}
  {running
   ?<div className="agent-working-caption">{message.routing?.status==='choosing'?'Adaptive is choosing an agent for this message.':'Reading the project and working in it. The reply appears when the agent finishes.'}</div>
   :message.content&&<div className="inline-build"><MarkdownOutput text={message.content} onFile={(p,l)=>onInspect('Files',p,l)}/></div>}
  {changes.length>0&&<div className={`changed-summary ${message.reverted_at?'reverted':''}`}>
   <button onClick={()=>onInspect('Changes')}><FileCode2 size={14}/>{changes.length} file{changes.length===1?'':'s'} changed<ChevronRight size={13}/></button>
   {message.reverted_at?<span className="reverted-badge"><Undo2 size={12}/>Reverted</span>:onRevert&&!running&&<button className="revert-button" title={message.checkpoint?'Put every file this turn touched back exactly as it was, from the repository checkpoint':'Write back what each changed file held before this turn'} onClick={()=>onRevert(message.id)}><Undo2 size={12}/>Revert this turn</button>}
   <div>{changes.slice(0,8).map(c=><button key={c.id} onClick={()=>onInspect('Changes',c.path)}><span className={c.status==='added'?'file-added':c.status==='removed'?'file-modified':'file-modified'}>{c.status[0].toUpperCase()}</span>{c.path}</button>)}</div>
  </div>}
  {message.status==='failed'&&<div className="thread-alert"><AlertTriangle size={16}/><div><strong>This turn stopped.</strong><p>{message.error}</p></div></div>}
  {message.status==='cancelled'&&<div className="thread-alert"><Square size={15}/><div><strong>Stopped.</strong><p>{message.error}</p></div></div>}
  {message.status==='complete'&&!changes.length&&message.mode!=='read'&&<div className="thread-completion"><Check size={15}/><span>No files changed.</span></div>}
 </div>;
}
