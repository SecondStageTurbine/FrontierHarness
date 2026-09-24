import {useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {FolderOpen,GitBranch,Play,Square,RotateCw,ExternalLink,LoaderCircle,Settings2,AlertTriangle,MessageSquare} from 'lucide-react';
import {useWorkspace} from '../app/context';
import {api} from '../lib/api';

type Row={id:string;name:string;root:string;missing:boolean;state:'working'|'waiting'|'done'|null;working:number;sessions:number;last_activity:string|null;
 last_session:{id:string;name:string;snippet:string}|null;dev:{running:boolean;port?:number|null;command?:string|null;exit_code?:number|null};
 git:{branch:string;changes:number;ahead:number;behind:number}|null;board:{todo:number;doing:number;blocked:number;done:number}};

const STATE_LABEL={working:'Working',waiting:'Needs you',done:'Up to date'};

function ago(at:string|null){
 if(!at)return 'no activity yet';
 const s=(Date.now()-new Date(at).getTime())/1000;
 if(s<60)return 'just now';if(s<3600)return `${Math.floor(s/60)} min ago`;if(s<86400)return `${Math.floor(s/3600)} h ago`;
 if(s<86400*14)return `${Math.floor(s/86400)} d ago`;
 return new Date(at).toLocaleDateString([],{month:'short',day:'numeric'});
}

/** Every project at a glance: its agents, its dev server, its branch, its board, and when it last moved. */
export function ProjectsDashboard({onOpen,onOpenSession,onSettings}:{onOpen:(id:string)=>void;onOpenSession:(projectId:string,sessionId:string)=>void;onSettings:(id:string)=>void}){
 const {path,tenant,notify}=useWorkspace();
 const rows=useQuery({queryKey:['tenant',tenant?.id,'/dashboard'],queryFn:({signal})=>api.get<Row[]>(path('/dashboard'),signal),refetchInterval:5000,enabled:!!tenant});
 const [busy,setBusy]=useState('');
 async function dev(row:Row,action:'start'|'stop'|'restart'){
  setBusy(row.id);
  try{await api.post(path(`/projects/${row.id}/devserver`),{action});await rows.refetch()}catch(e){notify((e as Error).message)}finally{setBusy('')}
 }
 return <div className="projects-dashboard">
  <header><h1>Projects</h1><p>{rows.data?`${rows.data.length} project${rows.data.length===1?'':'s'}, most recently active first.`:'Reading your projects…'}</p></header>
  {rows.error&&<p className="error-text">{rows.error.message}</p>}
  <div className="dashboard-grid">{rows.data?.map(r=><article key={r.id} className={`dashboard-card ${r.state||'quiet'}`}>
   <div className="dashboard-card-head"><button className="dashboard-name" onClick={()=>onOpen(r.id)}><FolderOpen size={15}/><strong>{r.name}</strong></button>
    <span className={`dashboard-state ${r.state||'quiet'}`}>{r.state?(r.state==='working'&&r.working>1?`${r.working} working`:STATE_LABEL[r.state]):'Quiet'}</span>
    <button className="icon-button" title="Project settings" aria-label={`Settings for ${r.name}`} onClick={()=>onSettings(r.id)}><Settings2 size={13}/></button></div>
   <code className="dashboard-root" title={r.root}>{r.root}</code>
   {r.missing&&<p className="dashboard-warning"><AlertTriangle size={12}/> This folder no longer exists.</p>}
   <div className="dashboard-last">{r.last_session?<button className="linkish" onClick={()=>onOpenSession(r.id,r.last_session!.id)}><MessageSquare size={12}/>{r.last_session.name}</button>:<span className="muted">No sessions yet</span>}<small>{ago(r.last_activity)}{r.sessions>1?` · ${r.sessions} sessions`:''}</small></div>
   {r.last_session?.snippet&&<p className="dashboard-snippet">{r.last_session.snippet}</p>}
   <div className="dashboard-facts">
    {r.git&&<span title={`${r.git.changes} uncommitted change${r.git.changes===1?'':'s'}`}><GitBranch size={12}/>{r.git.branch}{r.git.changes?<em>{r.git.changes} changed</em>:null}{r.git.ahead?<em>↑{r.git.ahead}</em>:null}{r.git.behind?<em>↓{r.git.behind}</em>:null}</span>}
    {(r.board.doing+r.board.todo+r.board.blocked)>0&&<span title="Open tasks on the board">{r.board.doing} doing · {r.board.todo} to do{r.board.blocked?` · ${r.board.blocked} blocked`:''}</span>}
   </div>
   <div className="dashboard-dev">
    <span className={`dev-dot ${r.dev.running?'on':''}`}/><span className="dev-label">{r.dev.running?(r.dev.port?`Dev server on :${r.dev.port}`:'Dev server starting…'):r.dev.command?'Dev server stopped':'No dev server command'}</span>
    {busy===r.id?<LoaderCircle size={13} className="spin"/>:r.dev.running?<>
     {r.dev.port&&<a className="icon-button" href={`http://localhost:${r.dev.port}/`} target="_blank" rel="noreferrer" title="Open in the browser" aria-label={`Open ${r.name} in the browser`}><ExternalLink size={13}/></a>}
     <button className="icon-button" title="Restart" aria-label={`Restart ${r.name} dev server`} onClick={()=>dev(r,'restart')}><RotateCw size={13}/></button>
     <button className="icon-button" title="Stop" aria-label={`Stop ${r.name} dev server`} onClick={()=>dev(r,'stop')}><Square size={13}/></button></>
    :r.dev.command?<button className="icon-button" title={`Start: ${r.dev.command}`} aria-label={`Start ${r.name} dev server`} onClick={()=>dev(r,'start')}><Play size={13}/></button>
    :<button className="linkish" onClick={()=>onSettings(r.id)}>Set one</button>}
   </div>
  </article>)}</div>
 </div>;
}
