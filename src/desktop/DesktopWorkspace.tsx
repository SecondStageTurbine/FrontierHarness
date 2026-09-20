import {useState,useEffect,useRef,lazy,Suspense} from 'react';
import {useNavigate,useLocation} from 'react-router-dom';
import {open as openDialog} from '@tauri-apps/plugin-dialog';
import {isTauri} from '@tauri-apps/api/core';
import {Plus,Folder,FolderOpen,Search,MessageSquare,Settings2,PanelLeftClose,PanelRightOpen,Paperclip,ArrowUp,Square,LoaderCircle,X,ChevronDown,GitCompareArrows,Terminal,Code2,ListTree,ShieldCheck,ArrowRight,Eye,Pencil,Zap,ArrowRightLeft} from 'lucide-react';
import {useWorkspace,useResource,useRefresh} from '../app/context';
import {useLiveSession} from '../app/useLiveSession';
import {Conversation,type InspectorTab} from './Conversation';
import {Inspector} from './Inspector';
import {Dictate} from '../components/Dictate';
import {Field,Modal,Confirm} from '../components/ui';
import {api} from '../lib/api';
import {ADAPTIVE,ADAPTIVE_HINT,agentProviders,modes,modeLabels,modeHints,providerNames,working,type Mode,type Model,type Project,type Session} from '../types';
const Models=lazy(()=>import('../features/models/Models'));
const Tenants=lazy(()=>import('../features/tenants/Tenants'));
const Settings=lazy(()=>import('../features/settings/Settings'));
const SETTINGS_TABS=['General','Agents & Providers','Workspaces','Security','Developer'];
const modeIcon={read:Eye,edit:Pencil,auto:Zap};

export default function DesktopWorkspace(){
 const {tenant,tenants,switchTenant,path,notify}=useWorkspace();const refresh=useRefresh(),navigate=useNavigate(),location=useLocation();
 const projects=useResource<Project[]>('/projects'),models=useResource<Model[]>('/models');
 const selectionKey=`frontier.project.${tenant?.id}`;
 const [projectId,setProjectId]=useState(()=>localStorage.getItem(selectionKey)||'');
 const project=projects.data?.find(p=>p.id===projectId)||projects.data?.[0];
 const sessions=useResource<Session[]>(`/projects/${project?.id}/sessions`,!!project);
 const [sessionId,setSessionId]=useState('');
 const [newSession,setNewSession]=useState(false);
 const selectedSessionId=newSession?'':sessionId||localStorage.getItem(`frontier.session.${project?.id}`)||project?.last_session_id||sessions.data?.[0]?.id||'';
 const sessionQ=useLiveSession(project?.id,selectedSessionId||undefined);
 const session=sessionQ.data;
 // Only an agent command line tool can take a turn. An API key reaches a model, not an agent,
 // so those stay configured for dictation and are never offered here.
 const agents=(models.data||[]).filter(m=>agentProviders.includes(m.provider));
 const [modelId,setModelId]=useState('');
 const [mode,setMode]=useState<Mode>('edit');
 // Adaptive is a selection like any other and is remembered per project; a manual pick is
 // never routed, and Adaptive is only ever consulted when it is what the selector says.
 // A local agent whose server is down stays listed, greyed, and is never chosen by default.
 const up=(a:Model)=>a.online!==false;
 const remembered=project?.last_model_id;
 const activeModelId=(modelId===ADAPTIVE||agents.some(a=>a.id===modelId&&up(a)))?modelId:remembered===ADAPTIVE?ADAPTIVE:(agents.find(a=>a.id===remembered&&up(a))?.id||agents.find(up)?.id||agents[0]?.id||'');
 const activeAgent=activeModelId===ADAPTIVE?{name:'Adaptive'}:agents.find(a=>a.id===activeModelId);
 const last=session?.messages.at(-1);const busy=working(last);
 const [input,setInput]=useState(''),[attachments,setAttachments]=useState<{id:string;name:string}[]>([]);
 const [sending,setSending]=useState(false),[uploading,setUploading]=useState(false),[error,setError]=useState(''),[cancel,setCancel]=useState(false);
 const [projectOpen,setProjectOpen]=useState(false),[projectName,setProjectName]=useState(''),[projectRoot,setProjectRoot]=useState('');
 const [panel,setPanel]=useState<InspectorTab|null>(null),[filePath,setFilePath]=useState<string|null>(null),[panelWidth,setPanelWidth]=useState(430),[sidebarWidth,setSidebarWidth]=useState(235),[sidebarClosed,setSidebarClosed]=useState(false),[search,setSearch]=useState(''),[settings,setSettings]=useState(false),[settingsTab,setSettingsTab]=useState('General');
 const composer=useRef<HTMLTextAreaElement>(null),scroll=useRef<HTMLDivElement>(null),uploadInput=useRef<HTMLInputElement>(null),follow=useRef(true);
 useEffect(()=>{setProjectId(localStorage.getItem(selectionKey)||'');setSessionId('');setNewSession(false);setPanel(null);setFilePath(null);setInput('');setAttachments([]);setError('')},[tenant?.id]);
 useEffect(()=>{if(!project)return;setMode(project.last_mode||'edit');setModelId(project.last_model_id||'');setInput(sessionStorage.getItem(`frontier.composer.${project.id}`)||'')},[project?.id]);
 useEffect(()=>{if(project)sessionStorage.setItem(`frontier.composer.${project.id}`,input)},[input,project?.id]);
 useEffect(()=>{if(follow.current)scroll.current?.scrollTo({top:scroll.current.scrollHeight,behavior:'smooth'})},[session?.messages.length,last?.status,last?.content.length]);
 useEffect(()=>{const routes:Record<string,string>={'/models':'Agents & Providers','/tenants':'Workspaces','/settings':'General'};if(routes[location.pathname]){setSettingsTab(routes[location.pathname]);setSettings(true)}},[location.pathname]);
 useEffect(()=>{const handler=(e:KeyboardEvent)=>{if((e.ctrlKey||e.metaKey)&&e.key==='n'){e.preventDefault();startNewSession()}if((e.ctrlKey||e.metaKey)&&e.key==='k'){e.preventDefault();document.getElementById('project-session-search')?.focus()}if(e.key==='Escape'&&!settings&&!projectOpen)setPanel(null)};window.addEventListener('keydown',handler);return()=>window.removeEventListener('keydown',handler)},[project,settings,projectOpen]);
 function chooseProject(id:string){if(id===project?.id)return;setProjectId(id);localStorage.setItem(selectionKey,id);setSessionId('');setNewSession(false);setPanel(null);setFilePath(null);setAttachments([]);setError('');follow.current=true}
 function chooseSession(s:Session){setNewSession(false);setSessionId(s.id);localStorage.setItem(`frontier.session.${project?.id}`,s.id);setPanel(null);setFilePath(null);follow.current=true}
 function startNewSession(){setNewSession(true);setSessionId('');setInput('');setPanel(null);setError('');composer.current?.focus()}
 function inspect(tab:InspectorTab,file?:string){setPanel(tab);if(file)setFilePath(file)}
 function openSettings(tab='General'){setSettingsTab(tab);setSettings(true)}
 async function createProject(e:React.FormEvent){e.preventDefault();setSending(true);setError('');try{const p=await api.post<Project>(path('/projects'),{name:projectName,root:projectRoot||null});await refresh();setProjectId(p.id);localStorage.setItem(selectionKey,p.id);setNewSession(true);setSessionId('');setProjectOpen(false);setProjectName('');setProjectRoot('');notify('Project opened');composer.current?.focus()}catch(e){setError((e as Error).message)}finally{setSending(false)}}
 async function send(){
  if(!project||!input.trim()||sending||busy)return;
  if(!activeModelId){setError('Connect a Claude, Codex or OpenCode agent in Agents & Providers first.');openSettings('Agents & Providers');return}
  setSending(true);setError('');
  try{
   let current=session;
   if(!current||newSession)current=await api.post<Session>(path(`/projects/${project.id}/sessions`),{name:'New session'});
   const body=attachments.length?`${input}\n\nAttached for context:\n`+attachments.map(a=>a.name).join('\n'):input;
   await api.post<Session>(path(`/projects/${project.id}/sessions/${current.id}/instructions`),{content:body,model_id:activeModelId,mode});
   setSessionId(current.id);setNewSession(false);localStorage.setItem(`frontier.session.${project.id}`,current.id);
   setInput('');setAttachments([]);follow.current=true;await refresh();
  }catch(e){setError((e as Error).message)}finally{setSending(false)}
 }
 async function attach(files:FileList|null){if(!files)return;setUploading(true);try{for(const f of Array.from(files)){const data=new FormData();data.append('file',f);const a=await api.upload<{id:string;name:string}>(path('/attachments'),data);setAttachments(v=>[...v,a])}}catch(e){setError((e as Error).message)}finally{setUploading(false)}}
 function resize(which:'left'|'right',e:React.PointerEvent){e.preventDefault();const start=e.clientX,initial=which==='left'?sidebarWidth:panelWidth;const move=(m:PointerEvent)=>{const width=initial+(m.clientX-start)*(which==='left'?1:-1);if(which==='left')setSidebarWidth(Math.max(185,Math.min(330,width)));else setPanelWidth(Math.max(320,Math.min(window.innerWidth*.6,width)))};const end=()=>{document.removeEventListener('pointermove',move);document.removeEventListener('pointerup',end)};document.addEventListener('pointermove',move);document.addEventListener('pointerup',end)}
 const shownSessions=(sessions.data||[]).filter(s=>s.name.toLowerCase().includes(search.toLowerCase()));
 const ModeIcon=modeIcon[mode];
 const switching=activeModelId!==ADAPTIVE&&!!last&&last.role==='assistant'&&!!last.model_id&&last.model_id!==activeModelId;
 return <div className="desktop-workspace" style={{'--sidebar-width':`${sidebarClosed?0:sidebarWidth}px`,'--inspector-width':`${panelWidth}px`} as React.CSSProperties}>
 {!sidebarClosed&&<aside className="project-sidebar"><div className="desktop-brand"><span>✳</span><strong>Frontier</strong><button className="icon-button" title="Collapse sidebar" aria-label="Collapse sidebar" onClick={()=>setSidebarClosed(true)}><PanelLeftClose size={15}/></button></div><button className="new-session-button" onClick={startNewSession} disabled={!project}><Plus size={15}/><span>New session</span><kbd>Ctrl N</kbd></button><div className="desktop-search"><Search size={13}/><input id="project-session-search" aria-label="Search sessions" placeholder="Search sessions" value={search} onChange={e=>setSearch(e.target.value)}/></div><div className="sidebar-section-label"><span>Projects</span><button className="icon-button" title="New project" aria-label="New project" onClick={()=>{setError('');setProjectOpen(true)}}><Plus size={14}/></button></div><div className="project-list">{projects.data?.map(p=><button key={p.id} className={project?.id===p.id?'active':''} onClick={()=>chooseProject(p.id)}><FolderOpen size={15}/><span>{p.name}</span>{project?.id===p.id&&<ChevronDown size={12}/>}</button>)}{!projects.data?.length&&<button onClick={()=>setProjectOpen(true)}><Folder size={14}/><span>Create your first project</span></button>}</div><div className="sidebar-section-label"><span>Recent sessions</span><small>{shownSessions.length}</small></div><div className="session-list">{shownSessions.map(s=><button key={s.id} className={!newSession&&selectedSessionId===s.id?'active':''} onClick={()=>chooseSession(s)}><MessageSquare size={13}/><span>{s.name}</span></button>)}{!shownSessions.length&&<p>Your conversations will appear here.</p>}</div>{project&&<div className="project-shortcuts"><button onClick={()=>inspect('Files')}><Folder size={14}/>Project files</button><button onClick={()=>inspect('Changes')}><GitCompareArrows size={14}/>Changes</button></div>}<div className="project-sidebar-footer"><button onClick={()=>openSettings()}><Settings2 size={15}/>Settings</button><label><span className="workspace-mini-avatar">{tenant?.name.slice(0,1)||'F'}</span><select aria-label="Current workspace" value={tenant?.id||''} onChange={e=>switchTenant(e.target.value)}>{tenants.map(t=><option key={t.id} value={t.id}>{t.name}</option>)}</select><span className="local-dot" title="Workspace isolation active"/></label></div></aside>}
 {!sidebarClosed&&<div className="panel-resizer left" role="separator" aria-label="Resize project sidebar" aria-orientation="vertical" tabIndex={0} onPointerDown={e=>resize('left',e)} onKeyDown={e=>{if(e.key==='ArrowRight')setSidebarWidth(v=>Math.min(330,v+10));if(e.key==='ArrowLeft')setSidebarWidth(v=>Math.max(185,v-10))}}/>}
 <section className="workspace-center">
  <header className="session-header"><div className="session-title">{sidebarClosed&&<button className="icon-button" aria-label="Show project sidebar" onClick={()=>setSidebarClosed(false)}><PanelRightOpen size={16}/></button>}<Folder size={14}/><span>{project?.name||'Frontier'}</span><span className="session-divider">/</span><strong>{newSession?'New session':session?.name||'New session'}</strong></div><div className="session-tools"><button className={panel==='Files'?'selected':''} title="Project files" aria-label="Open project files" onClick={()=>inspect('Files')}><Folder size={15}/></button><button className={panel==='Changes'?'selected':''} title="Changes" aria-label="Open changes" onClick={()=>inspect('Changes')}><GitCompareArrows size={15}/></button><button className={panel==='Terminal'?'selected':''} title="Terminal" aria-label="Open terminal" onClick={()=>inspect('Terminal')}><Terminal size={15}/></button></div></header>
  <div className="conversation-scroll" ref={scroll} onScroll={()=>{if(scroll.current)follow.current=scroll.current.scrollHeight-scroll.current.scrollTop-scroll.current.clientHeight<160}}>
  {projects.error&&<div className="thread-error">{projects.error.message}<button onClick={()=>projects.refetch()}>Retry</button></div>}
  {!project?<div className="workspace-empty"><span className="empty-frontier-mark">✳</span><h1>Open a project.<br/>Start a conversation.</h1><p>One conversation, any agent. Switch between them whenever you like without losing it.</p><div className="empty-project-actions"><button onClick={()=>{setProjectRoot('');setProjectOpen(true)}}><Plus size={16}/>New project</button><button onClick={async()=>{if(isTauri()){const folder=await openDialog({directory:true,multiple:false,title:'Open project folder'});if(typeof folder==='string'){setProjectRoot(folder);setProjectName(folder.split(/[\\/]/).at(-1)||'Project');setProjectOpen(true)}}else{setProjectOpen(true)}}}><FolderOpen size={16}/>Open folder</button></div><small>Files and conversations stay with your project, on this device.</small></div>
   :!session||newSession||!session.messages.length?<div className="workspace-empty with-project"><span className="empty-frontier-mark">✳</span><h1>What are we working on?</h1><p>{project.name} is open. {activeAgent?`${activeAgent.name} is ready.`:'Connect an agent to begin.'}</p><div className="suggestion-grid">{[{icon:Code2,title:'Build something',text:'Add an authentication endpoint with tests, then run them.'},{icon:ListTree,title:'Explore this project',text:'Read this project and explain its architecture and entry points.'},{icon:ShieldCheck,title:'Review the code',text:'Review this project for correctness and security issues, and fix what you find.'}].map(s=><button key={s.title} onClick={()=>{setInput(s.text);composer.current?.focus()}}><s.icon size={16}/><strong>{s.title}</strong><ArrowRight size={13}/></button>)}</div>{!agents.length&&<div className="empty-team"><span>NO AGENT CONNECTED</span><button onClick={()=>openSettings('Agents & Providers')}><Settings2 size={13}/>Connect Claude, Codex or OpenCode</button></div>}</div>
   :<Conversation session={session} onInspect={inspect}/>}
  {sessionQ.error&&<div className="thread-error">{sessionQ.error.message}<button onClick={startNewSession}>Start a new session</button></div>}
  </div>
  <div className="composer-region">
   {error&&!projectOpen&&<div className="composer-error" role="alert"><span>{error}</span><button className="icon-button" aria-label="Dismiss error" onClick={()=>setError('')}><X size={13}/></button></div>}
   {switching&&!busy&&<div className="continue-strip"><ArrowRightLeft size={14}/><span>Next turn goes to {activeAgent?.name}. It gets this conversation and the project folder.</span></div>}
   <div className="composer" onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();void attach(e.dataTransfer.files)}}>
    {attachments.length>0&&<div className="composer-attachments">{attachments.map(a=><span key={a.id}><Paperclip size={11}/>{a.name}<button aria-label={`Remove ${a.name}`} onClick={()=>setAttachments(v=>v.filter(x=>x.id!==a.id))}><X size={11}/></button></span>)}</div>}
    <textarea ref={composer} aria-label="Ask Frontier" placeholder={project?'Ask the agent to build, fix, review, or explain…':'Create or open a project to get started…'} value={input} disabled={!project||sending} onChange={e=>setInput(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();void send()}}} rows={2}/>
    <div className="composer-toolbar"><div className="composer-context-controls">
     <Dictate onText={t=>{setInput(v=>[v.trim(),t].filter(Boolean).join(' '));composer.current?.focus()}}/>
     <button title="Attach file" aria-label="Attach file" onClick={()=>uploadInput.current?.click()} disabled={!project||uploading}><Paperclip size={15}/></button>
     <input type="file" multiple hidden ref={uploadInput} onChange={e=>attach(e.target.files)}/>
     <span className="composer-control-divider"/>
     <select aria-label="Agent" value={activeModelId} onChange={e=>setModelId(e.target.value)} disabled={!agents.length}>
      {!agents.length&&<option value="">No agent connected</option>}
      {agents.length>0&&<option value={ADAPTIVE} title={ADAPTIVE_HINT}>Adaptive</option>}
      {agentProviders.filter(provider=>agents.some(a=>a.provider===provider)).map(provider=>
       <optgroup key={provider} label={providerNames[provider]}>{agents.filter(a=>a.provider===provider).map(a=><option key={a.id} value={a.id} disabled={a.online===false}>{a.name}{a.online===false?' · offline':''}</option>)}</optgroup>)}
     </select>
     <select aria-label="What the agent may do" value={mode} onChange={e=>setMode(e.target.value as Mode)} title={modeHints[mode]}>
      {modes.map(m=><option key={m} value={m}>{modeLabels[m]}</option>)}
     </select>
    </div>
    {busy
     ?<button className="send-button stop" aria-label="Stop this turn" title="Stop this turn" onClick={()=>setCancel(true)}><Square size={13}/></button>
     :<button className="send-button" title="Send (Enter)" aria-label="Send" disabled={!project||!input.trim()||sending||uploading} onClick={()=>send()}>{sending?<LoaderCircle size={16} className="spin"/>:<ArrowUp size={18}/>}</button>}
    </div>
   </div>
   <div className="composer-footnote">
    <span>{uploading?'Uploading context…':busy?`${last?.model_name} is working`:activeModelId===ADAPTIVE?<><ArrowRightLeft size={12}/> {ADAPTIVE_HINT}</>:<><ModeIcon size={12}/> {modeHints[mode]}</>}</span>
    <span>{last?.input_tokens||last?.output_tokens?`${((last.input_tokens||0)+(last.output_tokens||0)).toLocaleString()} tokens last turn`:'Enter to send · Shift Enter for a new line'}</span>
   </div>
  </div>
 </section>
 {panel&&project&&<><div className="panel-resizer right" role="separator" aria-label="Resize contextual panel" aria-orientation="vertical" tabIndex={0} onPointerDown={e=>resize('right',e)} onKeyDown={e=>{if(e.key==='ArrowLeft')setPanelWidth(v=>Math.min(800,v+10));if(e.key==='ArrowRight')setPanelWidth(v=>Math.max(320,v-10))}}/><Inspector key={`${project.id}-${selectedSessionId}`} project={project} session={session} tab={panel} onTab={setPanel} onClose={()=>setPanel(null)} filePath={filePath} onFile={setFilePath} busy={busy}/></>}
 <Modal open={projectOpen} onClose={()=>setProjectOpen(false)} title={projectRoot?'Open project':'New project'} description="A local folder and the conversations about it."><form onSubmit={createProject}>{error&&<p className="error-text">{error}</p>}<Field label="Project name"><input required value={projectName} onChange={e=>setProjectName(e.target.value)} placeholder="e.g. Authentication service" autoFocus/></Field><Field label="Project folder" hint="Leave empty to create a new managed project folder."><input value={projectRoot} onChange={e=>setProjectRoot(e.target.value)} placeholder="New managed folder"/></Field>{isTauri()&&<button type="button" onClick={async()=>{const folder=await openDialog({directory:true,multiple:false,title:'Choose project folder'});if(typeof folder==='string'){setProjectRoot(folder);if(!projectName)setProjectName(folder.split(/[\\/]/).at(-1)||'Project')}}}><FolderOpen size={14}/>Choose folder</button>}<p className="project-permission-note">The agent works inside this folder as your local user. Read only lets it look without changing anything; Full auto also lets it run commands.</p><div className="actions end"><button className="primary" disabled={sending||!tenant}>{sending?'Opening…':projectRoot?'Open project':'Create project'}</button></div></form></Modal>
 <Confirm open={cancel} onClose={()=>setCancel(false)} title="Stop this turn?" description="Anything the agent has already written to the folder stays there." onConfirm={async()=>{setCancel(false);try{await api.post(path(`/projects/${project?.id}/sessions/${selectedSessionId}/cancel`));void refresh()}catch(e){setError((e as Error).message)}}}/>
 <Modal open={settings} onClose={()=>{setSettings(false);navigate('/')}} title="Settings" description="Agents, workspaces, and application preferences." wide><div className="desktop-settings"><nav>{SETTINGS_TABS.map(t=><button key={t} className={settingsTab===t?'selected':''} onClick={()=>{setSettingsTab(t);if(location.pathname!=='/')navigate('/')}}>{t}</button>)}</nav><div className="desktop-settings-body"><Suspense fallback={<p>Loading settings…</p>}>{settingsTab==='Agents & Providers'?<Models/>:settingsTab==='Workspaces'?<Tenants/>:<Settings section={settingsTab}/>}</Suspense></div></div></Modal>
 </div>;
}
