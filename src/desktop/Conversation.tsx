import {useState} from 'react';
import {Check,ChevronRight,LoaderCircle,FileCode2,AlertTriangle,Square,ArrowRightLeft,Eye,Pencil,Zap} from 'lucide-react';
import {MarkdownOutput} from '../components/Markdown';
import {duration} from '../lib/api';
import {modeLabels,type Message,type Mode,type Session} from '../types';
export type InspectorTab='Files'|'Changes'|'Terminal';
const modeIcon={read:Eye,edit:Pencil,auto:Zap};

export function Conversation({session,onInspect}:{session:Session;onInspect:(tab:InspectorTab,path?:string)=>void}){
 const [visible,setVisible]=useState(20);
 const shown=session.messages.slice(-visible);
 return <div className="conversation-thread">
  {session.messages.length>visible&&<button className="history-more" onClick={()=>setVisible(v=>v+20)}>Load earlier messages</button>}
  {shown.map(message=>message.role==='user'
   ?<section className="conversation-turn" key={message.id}><div className="user-message"><span className="message-author">You</span><p>{message.content}</p></div></section>
   :<AgentMessage key={message.id} message={message} onInspect={onInspect}/>)}
 </div>;
}

function AgentMessage({message,onInspect}:{message:Message;onInspect:(tab:InspectorTab,path?:string)=>void}){
 const running=message.status==='running';
 const ModeIcon=modeIcon[(message.mode||'edit') as Mode];
 const changes=message.changes||[];
 const seconds=message.finished_at?Math.round((new Date(message.finished_at).getTime()-new Date(message.created_at).getTime())/1000):0;
 return <div className={`agent-turn ${running?'working':''}`}>
  <div className="frontier-author">
   <span className="frontier-spark">{running?<LoaderCircle size={14} className="spin"/>:'✳'}</span>
   <strong>{message.model_name||'Agent'}</strong>
   <span className="agent-mode-badge" title={modeLabels[(message.mode||'edit') as Mode]}><ModeIcon size={12}/>{modeLabels[(message.mode||'edit') as Mode]}</span>
   <small>{running?`Working · ${duration(message.created_at,null)}`:message.status!=='complete'?message.status:seconds>2?duration(message.created_at,message.finished_at):''}</small>
  </div>
  {message.switched_from&&<div className="handover-note"><ArrowRightLeft size={13}/>Took over from {message.switched_from}. It was given this conversation and the project folder.</div>}
  {message.routing?.mode==='adaptive'&&message.routing.chosen&&<div className="routing-note" title={message.routing.requirements?.reason}>
   <span>Adaptive</span>{message.routing.attempts&&message.routing.attempts.length>1
    ?message.routing.attempts.map((a,i)=><span key={a.id+i}>{i>0&&' → '}<strong>{a.name}</strong>{a.outcome!=='completed'&&<em> · {a.outcome}</em>}</span>)
    :<><strong>{message.routing.chosen.name}</strong><em> · {message.routing.chosen.because}</em></>}
  </div>}
  {running
   ?<div className="agent-working-caption">{message.routing?.status==='choosing'?'Adaptive is choosing an agent for this message.':'Reading the project and working in it. The reply appears when the agent finishes.'}</div>
   :message.content&&<div className="inline-build"><MarkdownOutput text={message.content}/></div>}
  {changes.length>0&&<div className="changed-summary">
   <button onClick={()=>onInspect('Changes')}><FileCode2 size={14}/>{changes.length} file{changes.length===1?'':'s'} changed<ChevronRight size={13}/></button>
   <div>{changes.slice(0,8).map(c=><button key={c.id} onClick={()=>onInspect('Changes',c.path)}><span className={c.status==='added'?'file-added':c.status==='removed'?'file-modified':'file-modified'}>{c.status[0].toUpperCase()}</span>{c.path}</button>)}</div>
  </div>}
  {message.status==='failed'&&<div className="thread-alert"><AlertTriangle size={16}/><div><strong>This turn stopped.</strong><p>{message.error}</p></div></div>}
  {message.status==='cancelled'&&<div className="thread-alert"><Square size={15}/><div><strong>Stopped.</strong><p>{message.error}</p></div></div>}
  {message.status==='complete'&&!changes.length&&message.mode!=='read'&&<div className="thread-completion"><Check size={15}/><span>No files changed.</span></div>}
 </div>;
}
