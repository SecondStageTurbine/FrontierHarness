import {useState,useEffect} from 'react';
import {X,FileCode2,Folder,ChevronRight,Download,Terminal,SplitSquareHorizontal,AlignLeft,Play} from 'lucide-react';
import {useResource,useWorkspace,useRefresh} from '../app/context';
import {api,download} from '../lib/api';
import type {ProjectFile,Project,Session,FileChange} from '../types';
import type {InspectorTab} from './Conversation';
import {GitPanel} from './GitPanel';

export function Inspector({tab,onTab,onClose,project,session,filePath,onFile,busy}:{tab:InspectorTab;onTab:(t:InspectorTab)=>void;onClose:()=>void;project:Project;session?:Session;filePath:string|null;onFile:(s:string)=>void;busy:boolean}){
 const {path}=useWorkspace(),refresh=useRefresh();
 // A session in its own worktree reads that worktree; the project folder otherwise.
 const scope=session?.worktree?`session_id=${session.id}`:'';
 const tree=useResource<ProjectFile[]>(`/projects/${project.id}/files${scope?`?${scope}`:''}`);
 const fileQ=useResource<{path:string;content:string}>(`/projects/${project.id}/file?path=${encodeURIComponent(filePath||'')}${scope?`&${scope}`:''}`,!!filePath&&tab==='Files');
 const [split,setSplit]=useState(false),[command,setCommand]=useState(''),[running,setRunning]=useState(false),[error,setError]=useState('');
 const [changeId,setChangeId]=useState('');
 // Every turn's edits, newest last, so the panel shows the conversation's whole effect on the
 // folder rather than only the last message's.
 const changes:FileChange[]=(session?.messages||[]).flatMap(m=>m.changes||[]);
 const commands=session?.commands||[];
 useEffect(()=>setChangeId(''),[filePath,session?.id]);
 useEffect(()=>{void tree.refetch()},[changes.length]);
 const selected=changes.find(c=>c.id===changeId)||[...changes].reverse().find(c=>c.path===filePath)||changes.at(-1);
 return <aside className="context-inspector">
  <div className="inspector-tabs">{(['Files','Changes','Terminal'] as InspectorTab[]).map(t=><button key={t} className={tab===t?'selected':''} onClick={()=>onTab(t)}>{t}</button>)}<button className="icon-button" title="Close panel" aria-label="Close contextual panel" onClick={onClose}><X size={15}/></button></div>
  <div className="inspector-content">
   {tab==='Files'&&<><div className="file-browser">
    <div className="file-browser-heading"><Folder size={14}/>{project.name}{session?.worktree&&<em className="branch-chip" title={session.worktree.path}>{session.worktree.branch}</em>}<small>{tree.data?.length||0} files</small></div>
    {tree.error?<p className="thread-error">{tree.error.message}</p>:tree.isPending?<p className="inspector-empty">Reading project files…</p>:!tree.data?.length?<p className="inspector-empty">This folder is empty. Files the agent creates appear here.</p>:<FileTree files={tree.data} changes={changes} selected={filePath} onSelect={onFile}/>}
   </div>{filePath&&<div className="file-preview">
    <div className="file-preview-title"><FileCode2 size={14}/><span>{filePath}</span><button className="icon-button" aria-label="Download open file" title="Download file" disabled={!fileQ.data} onClick={()=>fileQ.data&&download(filePath,fileQ.data.content)}><Download size={13}/></button></div>
    {fileQ.error?<p className="thread-error">{fileQ.error.message}</p>:fileQ.data?<CodeView text={fileQ.data.content}/>:<p className="inspector-empty">Opening file…</p>}
   </div>}</>}
   {tab==='Changes'&&<GitPanel projectId={project.id} sessionId={session?.worktree?session.id:undefined} busy={busy}/>}
   {tab==='Changes'&&(!changes.length?<p className="inspector-empty">Files the agent writes in this conversation appear here, with what they looked like before.</p>:<>
    <div className="git-section-label turn-changes"><span>Turn changes</span><small>{changes.length}</small></div>
    <div className="change-picker">
     <select aria-label="Changed file" value={selected?.id||''} onChange={e=>setChangeId(e.target.value)}>{changes.map(c=><option key={c.id} value={c.id}>{c.path} · {c.status}</option>)}</select>
     <button className="icon-button" title={split?'Single view':'Before and after'} aria-label="Toggle split view" onClick={()=>setSplit(!split)}>{split?<AlignLeft size={15}/>:<SplitSquareHorizontal size={15}/>}</button>
    </div>
    {selected&&<><div className="diff-file-label"><span className={selected.status==='added'?'file-added':'file-modified'}>{selected.status[0].toUpperCase()}</span>{selected.path}<small>{selected.status}</small></div>
    {split&&selected.before!==null?<div className="split-diff"><div><span>Before</span><CodeView text={selected.before}/></div><div><span>After</span><CodeView text={selected.after||''}/></div></div>
     :<CodeView text={selected.after??selected.before??''}/>}</>}
   </>)}
   {tab==='Terminal'&&<>
    <div className="terminal-output">{!commands.length&&<p>Your own project checks and their actual output appear here.<br/>The agent runs its own commands through its tool.</p>}
     {commands.map(c=><div className="terminal-command" key={c.id}><strong><span>❯</span> {c.command}</strong><pre>{c.output||'Process started…'}</pre><small className={c.exit_code===0?'file-added':c.status==='running'?'muted':'file-modified'}>{c.status==='running'?'Running…':c.status==='completed'?`Process exited with code ${c.exit_code}`:`${c.status} · exit ${c.exit_code??'unavailable'}`}</small></div>)}
    </div>
    {session&&<form className="terminal-input" onSubmit={async e=>{e.preventDefault();if(!command.trim())return;setRunning(true);setError('');try{await api.post(path(`/projects/${project.id}/sessions/${session.id}/commands`),{command});setCommand('');void refresh()}catch(e){setError((e as Error).message)}finally{setRunning(false)}}}>
     <Terminal size={13}/><input aria-label="Project check command" placeholder="python -m unittest discover" value={command} onChange={e=>setCommand(e.target.value)} disabled={running}/>
     <button disabled={running||!command.trim()||busy} className="icon-button" aria-label="Run project check"><Play size={13}/></button>
    </form>}
    {running&&<p className="terminal-pending">Executing project check…</p>}
    {error&&<p className="thread-error">{error}</p>}
    <p className="terminal-help">Checks run locally in this project. Supported: pytest, unittest, compileall, npm test, npm run build / test / lint / typecheck.</p>
   </>}
  </div>
 </aside>;
}

function CodeView({text}:{text:string}){return <div className="code-view">{text.split('\n').map((line,i)=><div key={i}><span>{i+1}</span><code>{line||' '}</code></div>)}</div>}
function FileTree({files,changes,selected,onSelect,prefix=''}:{files:ProjectFile[];changes:FileChange[];selected:string|null;onSelect:(p:string)=>void;prefix?:string}){
 const folders=[...new Set(files.filter(f=>f.path.startsWith(prefix)&&f.path.slice(prefix.length).includes('/')).map(f=>f.path.slice(prefix.length).split('/')[0]))];
 const direct=files.filter(f=>f.path.startsWith(prefix)&&!f.path.slice(prefix.length).includes('/'));
 return <div className="file-tree">{folders.map(folder=><details key={folder} open><summary><ChevronRight size={11}/><Folder size={13}/>{folder}</summary><FileTree files={files} changes={changes} selected={selected} onSelect={onSelect} prefix={prefix+folder+'/'}/></details>)}
 {direct.map(f=>{const change=[...changes].reverse().find(c=>c.path===f.path);return <button key={f.path} className={selected===f.path?'selected':''} onClick={()=>onSelect(f.path)} title={f.path}><FileCode2 size={13}/><span>{f.path.slice(prefix.length)}</span>{change&&<small className={change.status==='added'?'file-added':'file-modified'}>{change.status[0].toUpperCase()}</small>}</button>})}</div>;
}
