import {useState} from 'react';
import {Check,ChevronRight,LoaderCircle,FileCode2,AlertTriangle,Square,ArrowRightLeft,Eye,Pencil,Zap,Clock,X,Undo2,FoldVertical,ShieldQuestion,Users,FileText,Terminal,GitCompareArrows,ExternalLink,MessageSquare} from 'lucide-react';
import {MarkdownOutput} from '../components/Markdown';
import {duration,money} from '../lib/api';
import {working} from '../types';
import {modeLabels,type Approval,type Message,type Mode,type Session,type Team as TeamState} from '../types';
export type InspectorTab='Files'|'Changes'|'Terminal'|'Preview';
const modeIcon={read:Eye,edit:Pencil,auto:Zap};

export function Conversation({session,onInspect,onUnqueue,onRevert,onDecide,onRewind,onOpenSession,onCite}:{session:Session;onInspect:(tab:InspectorTab,path?:string,line?:number)=>void;onUnqueue?:(id:string)=>void;onRevert?:(id:string)=>void;onDecide?:(approval:Approval,allow:boolean)=>void;onRewind?:(id:string)=>void;onOpenSession?:(id:string)=>void;onCite?:(text:string,from:string)=>void}){
 const busy=working(session.messages.at(-1));
 const [cite,setCite]=useState<{text:string;from:string;x:number;y:number}|null>(null);
 // Selecting text inside a reply offers to cite it in the composer as a typed reference.
 function onSelect(e:React.MouseEvent){if(!onCite)return;const sel=window.getSelection();const text=sel?.toString().trim()||'';if(!text||!sel||sel.rangeCount===0){setCite(null);return}const node=sel.anchorNode instanceof Element?sel.anchorNode:sel.anchorNode?.parentElement;const turn=node?.closest('.agent-turn');if(!turn){setCite(null);return}const rect=sel.getRangeAt(0).getBoundingClientRect();const host=(e.currentTarget as HTMLElement).getBoundingClientRect();setCite({text:text.slice(0,20000),from:turn.querySelector('.frontier-author strong')?.textContent||'the agent',x:rect.left-host.left,y:rect.top-host.top})}
 const [visible,setVisible]=useState(20);
 const shown=session.messages.slice(-visible);
 const queue=session.queue||[];
 const [showSummary,setShowSummary]=useState(false);
 const boundary=session.summary?.through;
 return <div className="conversation-thread" onMouseUp={onSelect} style={{position:'relative'}}>
  {cite&&<button type="button" className="cite-button" style={{left:Math.max(0,cite.x),top:Math.max(0,cite.y-30)}} onMouseDown={e=>e.preventDefault()} onClick={()=>{onCite?.(cite.text,cite.from);setCite(null);window.getSelection()?.removeAllRanges()}}><MessageSquare size={11}/>Cite in composer</button>}
  {session.messages.length>visible&&<button className="history-more" onClick={()=>setVisible(v=>v+20)}>Load earlier messages</button>}
  {shown.map(message=><div key={message.id} className={message.id===boundary?'':undefined}>{message.role==='user'
   ?<section className="conversation-turn"><div className="user-message"><span className="message-author">You</span>{onRewind&&!busy&&<button className="rewind-button" title="Edit this message and resend it; later messages are removed and the folder is put back to how it was before it" onClick={()=>onRewind(message.id)}><Pencil size={11}/>Edit from here</button>}<p>{message.content.split('\n\nREFERENCED CONTEXT')[0].split('\n\nAttached for context')[0]}</p>{!!message.context?.length&&<div className="context-chips read">{message.context.map((c,i)=><span key={i}>{c.kind==='file'?<FileText size={10}/>:c.kind==='terminal'?<Terminal size={10}/>:<GitCompareArrows size={10}/>}{c.label||c.path||c.kind}{c.start?`:${c.start}${c.end&&c.end!==c.start?`-${c.end}`:''}`:''}</span>)}</div>}</div></section>
   :<AgentMessage message={message} onInspect={onInspect} onRevert={onRevert} approvals={(session.approvals||[]).filter(a=>a.message_id===message.id)} onDecide={onDecide} onOpenSession={onOpenSession}/>}
   {message.id===boundary&&session.summary&&<div className="compact-note"><button onClick={()=>setShowSummary(v=>!v)}><FoldVertical size={13}/>{session.summary.count} earlier messages compacted by {session.summary.model_name}. The agent now sees this summary instead.<ChevronRight size={12} className={showSummary?'expanded-chevron':''}/></button>{showSummary&&<div className="compact-summary"><MarkdownOutput text={session.summary.text}/></div>}</div>}</div>)}
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

/** What a tool asked to do, in one line the user can judge: the command, the file, or the arguments. */
function describeInput(tool:string,input:Record<string,unknown>){const s=(k:string)=>typeof input[k]==='string'?input[k] as string:'';return s('command')||s('file_path')||s('path')||s('url')||s('pattern')||s('query')||(Object.keys(input).length?JSON.stringify(input).slice(0,400):tool)}
/** The lead's plan and every worker's progress, while a team turn runs and after it ends. */
function TeamCard({team,onOpenSession}:{team:TeamState;onOpenSession?:(id:string)=>void}){
 const label={planning:'planning the work',working:'workers are on it',reviewing:'reviewing the results',fixing:'sending fixes back',done:'finished'}[team.status];
 return <div className={`team-card ${team.status}`}>
  <div className="team-head"><Users size={14}/><strong>{team.lead} leads a team</strong><small>{label}</small></div>
  {team.summary&&<p>{team.summary}</p>}
  {team.tasks.length>0&&<ol className="team-tasks">{team.tasks.map(t=><li key={t.id} className={t.status}>
   <span className={`team-status ${t.status}`}>{t.status==='working'||t.status==='fixing'?<LoaderCircle size={11} className="spin"/>:t.status==='done'?<Check size={11}/>:t.status==='failed'?<AlertTriangle size={11}/>:<Clock size={11}/>}</span>
   <div><strong>{t.title}</strong><small>{t.model_name||'unassigned'}{t.merge?` · ${t.merge.startsWith('conflict')?'conflict on merge':t.merge}`:''}{t.changed?.length?` · ${t.changed.length} file${t.changed.length===1?'':'s'}`:''}</small>{t.report&&t.status!=='working'&&<p>{t.report.slice(0,240)}{t.report.length>240?'…':''}</p>}</div>
   {t.session_id&&onOpenSession&&<button className="icon-button" title="Open this worker's session" aria-label="Open worker session" onClick={()=>onOpenSession(t.session_id!)}><ExternalLink size={12}/></button>}
  </li>)}</ol>}
 </div>;
}
function AgentMessage({message,onInspect,onRevert,approvals=[],onDecide,onOpenSession}:{message:Message;onInspect:(tab:InspectorTab,path?:string,line?:number)=>void;onRevert?:(id:string)=>void;approvals?:Approval[];onDecide?:(approval:Approval,allow:boolean)=>void;onOpenSession?:(id:string)=>void}){
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
  {message.team&&<TeamCard team={message.team} onOpenSession={onOpenSession}/>}
  {running&&approvals.map(a=><div key={a.id} className="approval-card" role="alertdialog" aria-label={`${a.tool_name} needs permission`}><ShieldQuestion size={16}/><div><strong>{message.model_name} wants to run {a.tool_name}</strong><code>{describeInput(a.tool_name,a.input)}</code><small>Allowed once; the turn continues either way. Nothing happens until you answer.</small></div><div className="approval-actions"><button className="primary" onClick={()=>onDecide?.(a,true)}>Allow</button><button onClick={()=>onDecide?.(a,false)}>Deny</button></div></div>)}
  {running
   ?<div className="agent-working-caption">{message.routing?.status==='choosing'?'Adaptive is choosing an agent for this message.':message.team?'The team is working. Each task runs in its own session; open one from the card to watch it.':'Reading the project and working in it. The reply appears when the agent finishes.'}</div>
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
