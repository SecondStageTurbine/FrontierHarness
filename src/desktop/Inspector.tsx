import {useState,useEffect,useRef,useDeferredValue} from 'react';
import {X,ExternalLink,FileCode2,Folder,ChevronRight,Download,Terminal,SplitSquareHorizontal,AlignLeft,Play,Save,Search,MessageSquarePlus} from 'lucide-react';
import {isTauri} from '@tauri-apps/api/core';
import {Term} from './Term';
import {Preview} from './Preview';
import {useResource,useWorkspace,useRefresh} from '../app/context';
import {api,download} from '../lib/api';
import type {ProjectFile,Project,Session,FileChange,ContextChip} from '../types';
import type {InspectorTab} from './Conversation';
import {GitPanel} from './GitPanel';
import {TaskBoard} from './TaskBoard';
import {popOut} from './Popout';

export function Inspector({tab,onTab,onClose,project,session,filePath,fileLine,onFile,busy,onChip,onCompose,agents=[],popped=false,onOpenSession}:{tab:InspectorTab;onTab:(t:InspectorTab)=>void;onClose:()=>void;project:Project;session?:Session;filePath:string|null;fileLine?:number;onFile:(s:string,line?:number)=>void;busy:boolean;onChip?:(chip:ContextChip)=>void;onCompose?:(text:string)=>void;agents?:{provider:string;name:string}[];popped?:boolean;onOpenSession?:(id:string)=>void}){
 const {path,notify}=useWorkspace(),refresh=useRefresh();
 const [query,setQuery]=useState('');const deferred=useDeferredValue(query.trim());
 // A session in its own worktree reads that worktree; the project folder otherwise.
 const scope=session?.worktree?`session_id=${session.id}`:'';
 const tree=useResource<ProjectFile[]>(`/projects/${project.id}/files${scope?`?${scope}`:''}`);
 const fileQ=useResource<{path:string;content:string}>(`/projects/${project.id}/file?path=${encodeURIComponent(filePath||'')}${scope?`&${scope}`:''}`,!!filePath&&tab==='Files');
 const found=useResource<{hits:{path:string;line:number;text:string}[];files:number;truncated:boolean}>(`/projects/${project.id}/search?q=${encodeURIComponent(deferred)}${scope?`&${scope}`:''}`,deferred.length>=2&&tab==='Files');
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
  <div className="inspector-tabs">{(['Files','Changes','Terminal','Preview','Tasks'] as InspectorTab[]).map(t=><button key={t} className={tab===t?'selected':''} onClick={()=>onTab(t)}>{t}</button>)}{!popped&&<button className="icon-button" title="Open this panel in its own window" aria-label="Pop out panel" onClick={()=>{void popOut(tab);onClose()}}><ExternalLink size={14}/></button>}<button className="icon-button" title="Close panel" aria-label="Close contextual panel" onClick={onClose}><X size={15}/></button></div>
  <div className="inspector-content">
   {tab==='Tasks'&&<TaskBoard project={project} onOpenSession={onOpenSession}/>}
   {tab==='Files'&&<><div className="file-browser">
    <div className="file-browser-heading"><Folder size={14}/>{project.name}{session?.worktree&&<em className="branch-chip" title={session.worktree.path}>{session.worktree.branch}</em>}<small>{tree.data?.length||0} files</small></div>
    <label className="file-search"><Search size={12}/><input aria-label="Search project files" placeholder="Search in files…" value={query} onChange={e=>setQuery(e.target.value)}/>{query&&<button className="icon-button" aria-label="Clear search" onClick={()=>setQuery('')}><X size={11}/></button>}</label>
    {deferred.length>=2?(found.isPending?<p className="inspector-empty">Searching…</p>:found.error?<p className="thread-error">{found.error.message}</p>:!found.data?.hits.length?<p className="inspector-empty">Nothing in {found.data?.files||0} files mentions “{deferred}”.</p>:<div className="search-hits">{found.data.hits.map((h,i)=><button key={i} onClick={()=>onFile(h.path,h.line)} title={`${h.path}:${h.line}`}><span>{h.path}<em>:{h.line}</em></span><code>{h.text}</code></button>)}{found.data.truncated&&<p>Showing the first {found.data.hits.length} matches.</p>}</div>):
    tree.error?<p className="thread-error">{tree.error.message}</p>:tree.isPending?<p className="inspector-empty">Reading project files…</p>:!tree.data?.length?<p className="inspector-empty">This folder is empty. Files the agent creates appear here.</p>:<FileTree files={tree.data} changes={changes} selected={filePath} onSelect={onFile}/>}
   </div>{filePath&&<div className="file-preview">
    <div className="file-preview-title"><FileCode2 size={14}/><span>{filePath}</span>{onChip&&<button className="icon-button" aria-label="Reference this file in the prompt" title="Reference this file in the prompt" onClick={()=>onChip({kind:'file',path:filePath,label:filePath})}><MessageSquarePlus size={13}/></button>}<button className="icon-button" aria-label="Download open file" title="Download file" disabled={!fileQ.data} onClick={()=>fileQ.data&&download(filePath,fileQ.data.content)}><Download size={13}/></button></div>
    {fileQ.error?<p className="thread-error">{fileQ.error.message}</p>:fileQ.data?<Editor key={filePath} text={fileQ.data.content} line={fileLine} onReference={onChip?(start,end)=>onChip({kind:'file',path:filePath,start,end,label:filePath}):undefined} onSave={async content=>{await api.put(path(`/projects/${project.id}/file?path=${encodeURIComponent(filePath)}${scope?`&${scope}`:''}`),{content});await fileQ.refetch();notify('Saved')}}/>:<p className="inspector-empty">Opening file…</p>}
   </div>}</>}
   {tab==='Changes'&&<GitPanel projectId={project.id} sessionId={session?.worktree?session.id:undefined} busy={busy} onChip={onChip} onCompose={onCompose}/>}
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
   {tab==='Preview'&&<Preview projectId={project.id} sessionId={session?.worktree?session.id:undefined} devCommand={project.dev_command||''} project={project}/>}
   {tab==='Terminal'&&isTauri()&&<Term cwd={session?.worktree?.path||project.root} onChip={onChip} projectId={project.id} sessionId={session?.worktree?session.id:undefined} agents={agents}/>}
   {tab==='Terminal'&&!isTauri()&&<>
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

/** A plain text editor over one project file: type, Ctrl+S or Save. Nothing fancier until it is missed. */
function Editor({text,line,onSave,onReference}:{text:string;line?:number;onSave:(content:string)=>Promise<void>;onReference?:(start:number,end:number)=>void}){
 const [draft,setDraft]=useState(text),[saving,setSaving]=useState(false),[error,setError]=useState('');
 const area=useRef<HTMLTextAreaElement>(null);
 const dirty=draft!==text;
 useEffect(()=>{if(!dirty)setDraft(text)},[text]);
 useEffect(()=>{const el=area.current;if(!el||!line)return;const offset=text.split('\n').slice(0,line-1).reduce((n,l)=>n+l.length+1,0);el.focus();el.setSelectionRange(offset,offset);const lineHeight=parseFloat(getComputedStyle(el).lineHeight)||18;el.scrollTop=Math.max(0,(line-1)*lineHeight-el.clientHeight/3)},[line,text]);
 async function save(){if(!dirty||saving)return;setSaving(true);setError('');try{await onSave(draft)}catch(e){setError((e as Error).message)}finally{setSaving(false)}}
 return <div className="file-editor">
  <textarea ref={area} spellCheck={false} aria-label="File contents" value={draft} onChange={e=>setDraft(e.target.value)} onKeyDown={e=>{if((e.ctrlKey||e.metaKey)&&e.key==='s'){e.preventDefault();void save()}if(e.key==='Tab'){e.preventDefault();const el=e.currentTarget,s=el.selectionStart,end=el.selectionEnd;setDraft(draft.slice(0,s)+'  '+draft.slice(end));requestAnimationFrame(()=>el.setSelectionRange(s+2,s+2))}}}/>
  <div className="file-editor-bar">{error?<span className="thread-error">{error}</span>:<span>{dirty?'Unsaved changes':`${draft.split('\n').length} lines`}</span>}<span className="editor-actions">{onReference&&<button type="button" title="Reference the selected lines in the prompt" onClick={()=>{const el=area.current;if(!el)return;const before=draft.slice(0,el.selectionStart);const start=before.split('\n').length;const end=draft.slice(0,Math.max(el.selectionEnd,el.selectionStart)).split('\n').length;onReference(start,end)}}><MessageSquarePlus size={12}/>Reference</button>}<button className={dirty?'primary':''} disabled={!dirty||saving} onClick={save}><Save size={12}/>{saving?'Saving…':'Save'}<kbd>Ctrl S</kbd></button></span></div>
 </div>;
}
function CodeView({text}:{text:string}){return <div className="code-view">{text.split('\n').map((line,i)=><div key={i}><span>{i+1}</span><code>{line||' '}</code></div>)}</div>}
function FileTree({files,changes,selected,onSelect,prefix=''}:{files:ProjectFile[];changes:FileChange[];selected:string|null;onSelect:(p:string)=>void;prefix?:string}){
 const folders=[...new Set(files.filter(f=>f.path.startsWith(prefix)&&f.path.slice(prefix.length).includes('/')).map(f=>f.path.slice(prefix.length).split('/')[0]))];
 const direct=files.filter(f=>f.path.startsWith(prefix)&&!f.path.slice(prefix.length).includes('/'));
 return <div className="file-tree">{folders.map(folder=><details key={folder} open><summary><ChevronRight size={11}/><Folder size={13}/>{folder}</summary><FileTree files={files} changes={changes} selected={selected} onSelect={onSelect} prefix={prefix+folder+'/'}/></details>)}
 {direct.map(f=>{const change=[...changes].reverse().find(c=>c.path===f.path);return <button key={f.path} className={selected===f.path?'selected':''} onClick={()=>onSelect(f.path)} title={f.path}><FileCode2 size={13}/><span>{f.path.slice(prefix.length)}</span>{change&&<small className={change.status==='added'?'file-added':'file-modified'}>{change.status[0].toUpperCase()}</small>}</button>})}</div>;
}
