import {useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {GitBranch,GitCommitHorizontal,Upload,Sparkles,LoaderCircle,RefreshCw,ChevronRight,GitPullRequest,MessageSquarePlus,ExternalLink,CheckCircle2,XCircle,CircleDashed} from 'lucide-react';
import {useWorkspace} from '../app/context';
import {api} from '../lib/api';
import type {GitStatus,GitEntry,ContextChip,PullRequest} from '../types';

/** The repository behind this conversation: its branch, what is staged, and the user's own commit and push. */
export function GitPanel({projectId,sessionId,busy,onChip,onCompose}:{projectId:string;sessionId?:string;busy:boolean;onChip?:(chip:ContextChip)=>void;onCompose?:(text:string)=>void}){
 const {tenant,path,notify}=useWorkspace();const client=useQueryClient();
 const q=sessionId?`?session_id=${sessionId}`:'';
 const key=['tenant',tenant?.id,`/projects/${projectId}/git${q}`];
 const status=useQuery({queryKey:key,enabled:!!tenant,refetchInterval:busy?3000:8000,queryFn:({signal})=>api.get<GitStatus>(path(`/projects/${projectId}/git${q}`),signal)});
 const [selected,setSelected]=useState<{path:string;staged:boolean}|null>(null);
 const diff=useQuery({queryKey:[...key,'diff',selected?.path,selected?.staged],enabled:!!selected,queryFn:({signal})=>api.get<{diff:string}>(path(`/projects/${projectId}/git/diff?path=${encodeURIComponent(selected!.path)}&staged=${selected!.staged}${sessionId?`&session_id=${sessionId}`:''}`),signal)});
 const [message,setMessage]=useState(''),[working,setWorking]=useState<''|'stage'|'message'|'commit'|'push'>(''),[error,setError]=useState('');
 const refresh=()=>client.invalidateQueries({queryKey:key});
 async function act<T>(what:typeof working,run:()=>Promise<T>){setWorking(what);setError('');try{const r=await run();await refresh();return r}catch(e){setError((e as Error).message)}finally{setWorking('')}}
 const stage=(paths:string[],staged:boolean)=>act('stage',()=>api.post(path(`/projects/${projectId}/git/stage${q}`),{paths,staged}));
 const s=status.data;
 if(status.isPending)return <p className="inspector-empty">Reading the repository…</p>;
 if(!s?.repo)return <div className="git-none"><GitBranch size={14}/><span>{s?.available===false?'Git is not installed, so there is nothing to stage or commit here.':'This folder is not a git repository. Turn changes below are still recorded and can be reverted.'}</span></div>;
 const staged=(s.entries||[]).filter(e=>e.staged),unstaged=(s.entries||[]).filter(e=>e.unstaged);
 const Row=({e,isStaged}:{e:GitEntry;isStaged:boolean})=><div className={`git-row ${selected?.path===e.path&&selected.staged===isStaged?'selected':''}`}>
  <input type="checkbox" aria-label={`${isStaged?'Unstage':'Stage'} ${e.path}`} checked={isStaged} disabled={working!==''} onChange={()=>stage([e.path],!isStaged)}/>
  <button onClick={()=>setSelected({path:e.path,staged:isStaged})} title={e.path}><span className={e.status==='added'||e.status==='untracked'?'file-added':e.status==='removed'?'file-removed':'file-modified'}>{e.status[0].toUpperCase()}</span><span>{e.path}</span></button>
 </div>;
 return <div className="git-panel">
  <div className="git-head"><GitBranch size={13}/><strong>{s.branch}</strong>{s.worktree&&<em title={s.worktree.path}>worktree</em>}<small>{s.upstream?`${s.ahead?`↑${s.ahead} `:''}${s.behind?`↓${s.behind}`:''}`.trim()||'up to date':'no upstream'}</small><button className="icon-button" title="Refresh" aria-label="Refresh repository status" onClick={()=>status.refetch()}><RefreshCw size={12} className={status.isFetching?'spin':''}/></button></div>
  {error&&<p className="thread-error">{error}</p>}
  <div className="git-section"><div className="git-section-label"><span>Staged</span><small>{staged.length}</small>{unstaged.length>0&&<button onClick={()=>stage(unstaged.map(e=>e.path),true)} disabled={working!==''}>Stage all</button>}</div>
   {staged.length?staged.map(e=><Row key={'s'+e.path} e={e} isStaged/>):<p>Nothing staged. Tick a change below.</p>}</div>
  <div className="git-section"><div className="git-section-label"><span>Changes</span><small>{unstaged.length}</small>{staged.length>0&&<button onClick={()=>stage(staged.map(e=>e.path),false)} disabled={working!==''}>Unstage all</button>}</div>
   {unstaged.length?unstaged.map(e=><Row key={'u'+e.path} e={e} isStaged={false}/>):<p>The working tree is clean.</p>}</div>
  <div className="git-commit">
   <textarea aria-label="Commit message" placeholder="Commit message" rows={3} value={message} onChange={e=>setMessage(e.target.value)} disabled={working==='commit'}/>
   <div className="git-actions">
    <button title="Ask the agent to write a message from the staged diff" disabled={!staged.length||working!==''} onClick={()=>act('message',async()=>{const r=await api.post<{message:string;model_name:string}>(path(`/projects/${projectId}/git/message${q}`));setMessage(r.message);notify(`${r.model_name} wrote a message`)})}>{working==='message'?<LoaderCircle size={13} className="spin"/>:<Sparkles size={13}/>}Write message</button>
    <button className="primary" disabled={!staged.length||!message.trim()||working!==''} onClick={()=>act('commit',async()=>{await api.post(path(`/projects/${projectId}/git/commit${q}`),{message:message.trim()});setMessage('');notify('Committed')})}>{working==='commit'?<LoaderCircle size={13} className="spin"/>:<GitCommitHorizontal size={13}/>}Commit</button>
    <button disabled={working!==''||(!s.ahead&&!!s.upstream)} title={s.upstream?`Push to ${s.upstream}`:'Push and set the upstream'} onClick={()=>act('push',async()=>{await api.post(path(`/projects/${projectId}/git/push${q}`));notify('Pushed')})}>{working==='push'?<LoaderCircle size={13} className="spin"/>:<Upload size={13}/>}Push{s.ahead?` ${s.ahead}`:''}</button>
   </div>
  </div>
  <PullRequests projectId={projectId} sessionId={sessionId} branch={s.branch||''} upstream={s.upstream||null} onCompose={onCompose}/>
  {selected&&<div className="git-diff"><div className="diff-file-label"><span>{selected.path}</span><small>{selected.staged?'staged':'working tree'}</small>{onChip&&diff.data?.diff&&<button className="icon-button" title="Reference this diff in the prompt" aria-label="Reference this diff in the prompt" onClick={()=>onChip({kind:'diff',path:selected.path,text:diff.data!.diff.slice(0,20000),label:`diff ${selected.path}`})}><MessageSquarePlus size={13}/></button>}<button className="icon-button" aria-label="Close diff" onClick={()=>setSelected(null)}><ChevronRight size={13}/></button></div>
   {diff.isPending?<p className="inspector-empty">Reading diff…</p>:diff.error?<p className="thread-error">{diff.error.message}</p>:<UnifiedDiff text={diff.data?.diff||''}/>}</div>}
 </div>;
}

/** The branch's pull request through gh: open one, watch its checks and review, hand review comments to the agent. */
function PullRequests({projectId,sessionId,branch,upstream,onCompose}:{projectId:string;sessionId?:string;branch:string;upstream:string|null;onCompose?:(text:string)=>void}){
 const {tenant,path,notify}=useWorkspace();const client=useQueryClient();
 const q=sessionId?`?session_id=${sessionId}`:'';
 const key=['tenant',tenant?.id,`/projects/${projectId}/pr${q}`];
 const pr=useQuery({queryKey:key,enabled:!!tenant,refetchInterval:60000,queryFn:({signal})=>api.get<{available:boolean;pr:PullRequest|null;error?:string}>(path(`/projects/${projectId}/pr${q}`),signal)});
 const [open,setOpen]=useState(false),[title,setTitle]=useState(''),[body,setBody]=useState(''),[draft,setDraft]=useState(false),[working,setWorking]=useState(''),[error,setError]=useState('');
 async function act<T>(what:string,run:()=>Promise<T>){setWorking(what);setError('');try{return await run()}catch(e){setError((e as Error).message)}finally{setWorking('')}}
 if(pr.isPending)return null;
 const d=pr.data;
 if(!d?.available)return <div className="pr-section"><GitPullRequest size={13}/><span>Install the GitHub CLI and run gh auth login to open pull requests from here.</span></div>;
 if(d.error&&!d.pr)return <div className="pr-section"><GitPullRequest size={13}/><span>{d.error}</span></div>;
 if(d.pr){const p=d.pr;const checks=p.checks;const review=p.review==='approved'?'approved':p.review==='changes_requested'?'changes requested':p.review==='review_required'?'review required':p.review||'no review yet';
  return <div className={`pr-section has-pr ${p.state}`}><div className="pr-head"><GitPullRequest size={13}/><a href={p.url} target="_blank" rel="noreferrer"><strong>#{p.number}</strong> {p.title}</a><span className={`pr-state ${p.state}`}>{p.draft?'draft':p.state}</span></div>
   <div className="pr-meta"><span title="Checks">{checks.failure?<XCircle size={12} className="file-removed"/>:checks.pending?<CircleDashed size={12}/>:<CheckCircle2 size={12} className="file-added"/>}{checks.success+checks.failure+checks.pending?`${checks.success} passed${checks.failure?`, ${checks.failure} failed`:''}${checks.pending?`, ${checks.pending} pending`:''}`:'no checks'}</span><span>{review}</span>{p.additions!=null&&<span><em className="file-added">+{p.additions}</em> <em className="file-removed">−{p.deletions}</em></span>}</div>
   {error&&<p className="thread-error">{error}</p>}
   <div className="git-actions">{onCompose&&p.state==='open'&&<button disabled={working!==''} onClick={()=>act('comments',async()=>{const r=await api.get<{prompt:string;comments:unknown[];reviews:unknown[]}>(path(`/projects/${projectId}/pr/comments${q}`));if(!r.comments.length&&!r.reviews.length){notify('No review comments yet');return}onCompose(r.prompt);notify('Review comments are in the composer')})}>{working==='comments'?<LoaderCircle size={13} className="spin"/>:<MessageSquarePlus size={13}/>}Address review comments</button>}<button onClick={()=>window.open(p.url,'_blank')}><ExternalLink size={13}/>Open on GitHub</button><button className="icon-button" title="Refresh" aria-label="Refresh pull request" onClick={()=>pr.refetch()}><RefreshCw size={12} className={pr.isFetching?'spin':''}/></button></div>
  </div>;}
 return <div className="pr-section"><div className="pr-head"><GitPullRequest size={13}/><span>No pull request for <code>{branch}</code>{upstream?'':' (not pushed yet)'}</span>{!open&&<button onClick={()=>setOpen(true)} disabled={!branch||branch==='HEAD'}>Create pull request</button>}</div>
  {open&&<div className="pr-form">{error&&<p className="thread-error">{error}</p>}<input aria-label="Pull request title" placeholder="Title" value={title} onChange={e=>setTitle(e.target.value)}/><textarea aria-label="Pull request description" rows={4} placeholder="Description (markdown)" value={body} onChange={e=>setBody(e.target.value)}/>
   <div className="git-actions"><button disabled={working!==''} title="Ask the agent to draft the title and description from the branch's commits and diff" onClick={()=>act('draft',async()=>{const r=await api.post<{title:string;body:string;model_name:string}>(path(`/projects/${projectId}/pr/description${q}`));setTitle(r.title);setBody(r.body);notify(`${r.model_name} drafted the description`)})}>{working==='draft'?<LoaderCircle size={13} className="spin"/>:<Sparkles size={13}/>}Write description</button>
    <label className="pr-draft"><input type="checkbox" checked={draft} onChange={e=>setDraft(e.target.checked)}/>Draft</label>
    <button className="primary" disabled={!title.trim()||working!==''} onClick={()=>act('create',async()=>{await api.post(path(`/projects/${projectId}/pr${q}`),{title:title.trim(),body,draft});setOpen(false);await client.invalidateQueries({queryKey:key});notify('Pull request opened')})}>{working==='create'?<LoaderCircle size={13} className="spin"/>:<GitPullRequest size={13}/>}Push and open</button><button onClick={()=>setOpen(false)}>Cancel</button></div></div>}
 </div>;
}

export function UnifiedDiff({text}:{text:string}){
 const lines=text.split('\n');
 if(!text.trim())return <p className="inspector-empty">No differences.</p>;
 return <div className="unified-diff">{lines.map((line,i)=>{const kind=line.startsWith('+++')||line.startsWith('---')?'diff-meta':line.startsWith('@@')?'diff-hunk':line.startsWith('+')?'diff-add':line.startsWith('-')?'diff-remove':'';return <div key={i} className={kind}><span>{i+1}</span><code>{line||' '}</code></div>})}</div>;
}
