import {useState} from 'react';
import {Plug,Plus,Trash2,Pencil,Sparkles,Terminal,Copy} from 'lucide-react';
import {useWorkspace,useResource,useRefresh} from '../../app/context';
import {Field,Modal,Confirm,PageHeading} from '../../components/ui';
import {api} from '../../lib/api';
import type {McpServer,Skill,Project} from '../../types';
import {SkillCatalog} from './SkillCatalog';
import {useQuery} from '@tanstack/react-query';

type Form={name:string;transport:'stdio'|'http';command:string;args:string;env:string;url:string;enabled:boolean};
/** Well-known servers, filled into the form for the user to complete: a token, a path, a connection string. */
const CATALOG:{name:string;title:string;blurb:string;command:string;args:string;env:string}[]=[
 {name:'github',title:'GitHub',blurb:'Issues, pull requests and repositories through a personal access token.',command:'npx',args:'-y @modelcontextprotocol/server-github',env:'GITHUB_PERSONAL_ACCESS_TOKEN='},
 {name:'filesystem',title:'Filesystem',blurb:'Read and write a folder outside the project. Put the folder path in the arguments.',command:'npx',args:'-y @modelcontextprotocol/server-filesystem C:\\path\\to\\folder',env:''},
 {name:'postgres',title:'Postgres',blurb:'Read-only SQL against a database. Put the connection string in the arguments.',command:'npx',args:'-y @modelcontextprotocol/server-postgres postgresql://user:pass@localhost/db',env:''},
 {name:'playwright',title:'Playwright browser',blurb:'Let the agent open pages, click and read them in a real browser.',command:'npx',args:'-y @playwright/mcp@latest',env:''},
 {name:'slack',title:'Slack',blurb:'Read channels and post messages with a bot token.',command:'npx',args:'-y @modelcontextprotocol/server-slack',env:'SLACK_BOT_TOKEN=\nSLACK_TEAM_ID='},
 {name:'fetch',title:'Fetch',blurb:'Fetch a URL and hand the page to the agent as text. Needs uv.',command:'uvx',args:'mcp-server-fetch',env:''},
 {name:'memory',title:'Memory',blurb:'A knowledge graph the agent can write to and read back across turns.',command:'npx',args:'-y @modelcontextprotocol/server-memory',env:''},
 {name:'sequential-thinking',title:'Sequential thinking',blurb:'A scratchpad tool for step-by-step reasoning.',command:'npx',args:'-y @modelcontextprotocol/server-sequential-thinking',env:''},
 {name:'context7',title:'Context7 docs',blurb:'Current library documentation looked up by name.',command:'npx',args:'-y @upstash/context7-mcp',env:''},
];
const blank:Form={name:'',transport:'stdio',command:'',args:'',env:'',url:'',enabled:true};

/** MCP servers the workspace hands to every agent that can take them, and the skills the agents already see. */
/** Frontier itself as an MCP server: how another agent, an editor or a script adds it. */
function SelfServer(){
 const {path,copy}=useWorkspace();
 const q=useQuery({queryKey:['mcp-self'],queryFn:({signal})=>api.get<{command:string;args:string[];claude:string;codex:string;json:unknown}>(path('/mcp-self'),signal)});
 if(!q.data)return null;
 return <section className="self-mcp"><h3><Plug size={16}/> Use Frontier from other agents</h3>
  <p className="muted">Frontier is also an MCP server. Another agent, an editor or a script can list your projects and conversations, send a message to any agent here and wait for its reply, read a project's git changes, and catch up on what happened. It works while Frontier is running, and only for programs run by you on this computer.</p>
  {([['Claude Code',q.data.claude],['Codex',q.data.codex],['Any MCP client (JSON)',JSON.stringify(q.data.json,null,2)]] as [string,string][]).map(([label,value])=><div key={label} className="self-mcp-row"><strong>{label}</strong><pre>{value}</pre><button className="icon-button" aria-label={`Copy ${label} setup`} onClick={()=>copy(value)}><Copy size={13}/></button></div>)}
 </section>;
}

export default function Mcp({project}:{project?:Project}){
 const {path,notify}=useWorkspace(),refresh=useRefresh();
 const servers=useResource<McpServer[]>('/mcp');
 const skills=useResource<Skill[]>(`/projects/${project?.id}/skills`,!!project);
 const [open,setOpen]=useState(false),[editing,setEditing]=useState<McpServer|null>(null),[form,setForm]=useState<Form>(blank),[error,setError]=useState(''),[remove,setRemove]=useState<McpServer|null>(null),[busy,setBusy]=useState(false),[catalog,setCatalog]=useState(false);
 function fromCatalog(c:typeof CATALOG[number]){setEditing(null);setForm({name:c.name,transport:'stdio',command:c.command,args:c.args,env:c.env,url:'',enabled:true});setError('');setCatalog(false);setOpen(true)}
 function edit(s?:McpServer){setEditing(s||null);setForm(s?{name:s.name,transport:s.transport,command:s.command||'',args:(s.args||[]).join(' '),env:Object.entries(s.env||{}).map(([k,v])=>`${k}=${v}`).join('\n'),url:s.url||'',enabled:s.enabled!==false}:blank);setError('');setOpen(true)}
 async function save(e:React.FormEvent){e.preventDefault();setBusy(true);setError('');try{
  const env=Object.fromEntries(form.env.split('\n').map(l=>l.trim()).filter(Boolean).map(l=>{const i=l.indexOf('=');return [l.slice(0,i).trim(),l.slice(i+1)]}).filter(([k])=>k));
  const payload={name:form.name.trim(),transport:form.transport,command:form.transport==='stdio'?form.command.trim():null,args:form.transport==='stdio'?form.args.split(/\s+/).filter(Boolean):[],env,url:form.transport==='http'?form.url.trim():null,enabled:form.enabled};
  if(editing)await api.put(path(`/mcp/${editing.id}`),payload);else await api.post(path('/mcp'),payload);
  setOpen(false);await refresh();notify('MCP server saved')}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <>
  <PageHeading eyebrow="TOOLS THE AGENTS CAN CALL" title="MCP servers & skills" description="Servers listed here are handed to Claude, Codex and OpenCode on every turn. Skills and commands are what the agents already find in this project and your home folder."><button onClick={()=>setCatalog(v=>!v)}><Sparkles size={16}/>Catalog</button><button className="primary" onClick={()=>edit()}><Plus size={16}/>Add server</button></PageHeading>
  {catalog&&<div className="mcp-catalog">{CATALOG.map(c=>{const have=servers.data?.some(s=>s.name===c.name);return <button key={c.name} type="button" disabled={have} onClick={()=>fromCatalog(c)}><strong>{c.title}</strong><span>{c.blurb}</span><code>{c.command} {c.args.split(' ').slice(0,2).join(' ')}</code>{have&&<small>added</small>}</button>})}</div>}
  {servers.error&&<p className="error-text">{servers.error.message}</p>}
  {!servers.data?.length?<p className="muted">No MCP servers yet. A server is a command that speaks the Model Context Protocol over stdio, or an HTTP endpoint that does.</p>:
   <div className="mcp-list">{servers.data.map(s=><article key={s.id} className={`mcp-card ${s.enabled===false?'off':''}`}><Plug size={16}/><div><strong>{s.name}</strong><code>{s.transport==='http'?s.url:[s.command,...(s.args||[])].join(' ')}</code>{s.enabled===false&&<small>Disabled</small>}</div><label className="toggle-row compact"><input type="checkbox" role="switch" checked={s.enabled!==false} onChange={async e=>{try{await api.put(path(`/mcp/${s.id}`),{name:s.name,transport:s.transport,command:s.command,args:s.args||[],env:s.env||{},url:s.url,enabled:e.target.checked});await refresh()}catch(err){notify((err as Error).message)}}}/></label><button className="icon-button" aria-label={`Edit ${s.name}`} onClick={()=>edit(s)}><Pencil size={14}/></button><button className="icon-button" aria-label={`Remove ${s.name}`} onClick={()=>setRemove(s)}><Trash2 size={14}/></button></article>)}</div>}
  <p className="muted small">Gemini CLI reads its own settings file for MCP servers and is not configured from here.</p>
  <SelfServer/>
  <h3><Sparkles size={16}/> Skills and commands{project&&<span className="muted"> · {project.name}</span>}</h3>
  {!project?<p className="muted">Open a project to see what its agents find.</p>:skills.isPending?<p className="muted">Looking…</p>:!skills.data?.length?<p className="muted">Nothing found. Claude reads <code>.claude/skills/&lt;name&gt;/SKILL.md</code> and <code>.claude/commands/&lt;name&gt;.md</code> in the project and in your home folder; Codex and Gemini read the same layout under <code>.codex</code> and <code>.gemini</code>.</p>:
   <div className="skill-list">{skills.data.map(s=><div key={s.path} className="skill-row"><span className={`skill-kind ${s.kind}`}>{s.kind==='command'?'/':<Terminal size={11}/>}</span><div><strong>{s.kind==='command'?`/${s.name}`:s.name}</strong>{s.description&&<p>{s.description}</p>}</div><small>{s.provider} · {s.scope}</small></div>)}</div>}
  <p className="muted small">Type <code>/</code> at the start of the composer to pick a command; the agent runs it as its own slash command.</p>
  <h3 id="skills-catalog"><Sparkles size={16}/> Skills catalog{project&&<span className="muted"> · install into {project.name}</span>}</h3>
  {project?<SkillCatalog project={project}/>:<p className="muted">Open a project to install skills into it.</p>}
  <Modal open={open} onClose={()=>setOpen(false)} title={editing?'Edit MCP server':'Add MCP server'} description="Given to every agent on every turn in this workspace."><form onSubmit={save}>{error&&<p className="error-text" role="alert">{error}</p>}
   <div className="form-grid"><Field label="Name" hint="Letters, digits, dashes."><input required pattern="[A-Za-z0-9][A-Za-z0-9_\-]*" maxLength={60} value={form.name} onChange={e=>setForm({...form,name:e.target.value})} placeholder="graft"/></Field><Field label="Transport"><select value={form.transport} onChange={e=>setForm({...form,transport:e.target.value as 'stdio'|'http'})}><option value="stdio">Command (stdio)</option><option value="http">HTTP endpoint</option></select></Field></div>
   {form.transport==='stdio'?<><Field label="Command"><input required value={form.command} onChange={e=>setForm({...form,command:e.target.value})} placeholder="npx"/></Field><Field label="Arguments" hint="Space separated."><input value={form.args} onChange={e=>setForm({...form,args:e.target.value})} placeholder="-y @scope/server"/></Field><Field label="Environment" hint="One KEY=value per line. Stored in plain text on this device, so keep secrets in the tool's own config where you can."><textarea rows={3} value={form.env} onChange={e=>setForm({...form,env:e.target.value})}/></Field></>
    :<Field label="URL"><input required type="url" value={form.url} onChange={e=>setForm({...form,url:e.target.value})} placeholder="https://mcp.example.com/"/></Field>}
   <label className="toggle-row"><div><strong>Enabled</strong><p>Disabled servers stay listed but are not handed to agents.</p></div><input type="checkbox" role="switch" checked={form.enabled} onChange={e=>setForm({...form,enabled:e.target.checked})}/></label>
   <div className="actions end"><button type="button" onClick={()=>setOpen(false)}>Cancel</button><button className="primary" disabled={busy}>{busy?'Saving…':'Save server'}</button></div></form></Modal>
  <Confirm open={!!remove} onClose={()=>setRemove(null)} title={`Remove ${remove?.name}?`} description="Agents stop receiving it on their next turn." onConfirm={async()=>{if(!remove)return;try{await api.delete(path(`/mcp/${remove.id}`));await refresh()}catch(e){notify((e as Error).message)}setRemove(null)}}/>
 </>;
}
