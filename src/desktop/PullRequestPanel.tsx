import {useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {GitPullRequest,MessageSquarePlus,ExternalLink,CheckCircle2,XCircle,CircleDashed,LoaderCircle,RefreshCw,Sparkles,X,Layers,GitMerge,Tag,UserPlus} from 'lucide-react';
import {useWorkspace} from '../app/context';
import {api} from '../lib/api';
import type {PullRequest} from '../types';

type Options={repo:string;default_branch:string;labels:{name:string;color:string}[];people:string[];bases:{branch:string;title:string;number:number|null}[]};
type Event='approve'|'comment'|'request_changes';
const EVENTS:[Event,string][]=[['approve','Approve'],['comment','Comment'],['request_changes','Request changes']];

/** The branch's pull request: open one (on the default branch or stacked on another), review it, request reviewers, label it, and merge or auto-merge it. */
export function PullRequests({projectId,sessionId,branch,upstream,onCompose}:{projectId:string;sessionId?:string;branch:string;upstream:string|null;onCompose?:(text:string)=>void}){
 const {tenant,path,notify}=useWorkspace();const client=useQueryClient();
 const q=sessionId?`?session_id=${sessionId}`:'';
 const key=['tenant',tenant?.id,`/projects/${projectId}/pr${q}`];
 const pr=useQuery({queryKey:key,enabled:!!tenant,refetchInterval:60000,queryFn:({signal})=>api.get<{available:boolean;pr:PullRequest|null;error?:string}>(path(`/projects/${projectId}/pr${q}`),signal)});
 const [wantOptions,setWantOptions]=useState(false);
 const options=useQuery({queryKey:['tenant',tenant?.id,`/projects/${projectId}/pr/options${q}`],enabled:!!tenant&&wantOptions,staleTime:120000,queryFn:({signal})=>api.get<Options>(path(`/projects/${projectId}/pr/options${q}`),signal)});
 const [open,setOpen]=useState(false),[title,setTitle]=useState(''),[body,setBody]=useState(''),[draft,setDraft]=useState(false),[base,setBase]=useState(''),[reviewers,setReviewers]=useState(''),[labels,setLabels]=useState<string[]>([]);
 const [panel,setPanel]=useState<''|'review'|'merge'|'people'>(''),[event,setEvent]=useState<Event>('comment'),[review,setReview]=useState('');
 const [method,setMethod]=useState<'squash'|'merge'|'rebase'>('squash'),[deleteBranch,setDeleteBranch]=useState(false),[person,setPerson]=useState('');
 const [working,setWorking]=useState(''),[error,setError]=useState('');
 async function act<T>(what:string,run:()=>Promise<T>){setWorking(what);setError('');try{return await run()}catch(e){setError((e as Error).message)}finally{setWorking('')}}
 const refresh=()=>client.invalidateQueries({queryKey:key});
 const post=(route:string,data:unknown)=>api.post(path(`/projects/${projectId}/pr${route}${q}`),data).then(refresh);
 const spin=(what:string,icon:React.ReactNode)=>working===what?<LoaderCircle size={13} className="spin"/>:icon;
 if(pr.isPending)return null;
 const d=pr.data;
 if(!d?.available)return <div className="pr-section"><GitPullRequest size={13}/><span>Install the GitHub CLI and run gh auth login to open pull requests from here.</span></div>;
 if(d.error&&!d.pr)return <div className="pr-section"><GitPullRequest size={13}/><span>{d.error}</span></div>;
 if(d.pr){const p=d.pr;const checks=p.checks;const open_=p.state==='open';
  const reviewWord=p.review==='approved'?'approved':p.review==='changes_requested'?'changes requested':p.review==='review_required'?'review required':p.review||'no review yet';
  const stack=p.stack;
  return <div className={`pr-section has-pr ${p.state}`}>
   <div className="pr-head"><GitPullRequest size={13}/><a href={p.url} target="_blank" rel="noreferrer"><strong>#{p.number}</strong> {p.title}</a><span className={`pr-state ${p.state}`}>{p.draft?'draft':p.state}</span></div>
   <div className="pr-meta"><span title="Checks">{checks.failure?<XCircle size={12} className="file-removed"/>:checks.pending?<CircleDashed size={12}/>:<CheckCircle2 size={12} className="file-added"/>}{checks.success+checks.failure+checks.pending?`${checks.success} passed${checks.failure?`, ${checks.failure} failed`:''}${checks.pending?`, ${checks.pending} pending`:''}`:'no checks'}</span><span>{reviewWord}</span>{p.additions!=null&&<span><em className="file-added">+{p.additions}</em> <em className="file-removed">−{p.deletions}</em></span>}{p.auto_merge&&<span className="pr-auto"><GitMerge size={11}/>auto-merge ({p.auto_merge}) on</span>}</div>
   {stack&&(stack.below.length>0||stack.above.length>0)&&<div className="pr-stack" aria-label="Pull request stack"><Layers size={12}/>
    <span>{stack.root_base}</span>{[...stack.below].reverse().map(s=><span key={s.number}>→ <a href={s.url} target="_blank" rel="noreferrer">#{s.number}</a></span>)}<span>→ <strong>#{p.number}</strong></span>{stack.above.map(s=><span key={s.number}>→ <a href={s.url} target="_blank" rel="noreferrer">#{s.number}</a></span>)}</div>}
   <div className="pr-chips">{p.labels?.map(l=><span key={l} className="pr-chip"><Tag size={10}/>{l}{open_&&<button aria-label={`Remove label ${l}`} onClick={()=>act('label',()=>post('/edit',{remove_labels:[l]}))}><X size={10}/></button>}</span>)}
    {p.requested?.map(r=><span key={r} className="pr-chip person"><UserPlus size={10}/>{r}{open_&&<button aria-label={`Remove reviewer ${r}`} onClick={()=>act('people',()=>post('/edit',{remove_reviewers:[r]}))}><X size={10}/></button>}</span>)}
    {p.reviews?.filter(r=>r.author).map(r=><span key={r.author!} className={`pr-chip review ${r.state}`} title={`${r.author}: ${r.state.replace('_',' ')}`}>{r.state==='approved'?<CheckCircle2 size={10}/>:r.state==='changes_requested'?<XCircle size={10}/>:<MessageSquarePlus size={10}/>}{r.author}</span>)}</div>
   {error&&<p className="thread-error">{error}</p>}
   {open_&&<div className="git-actions">
    <button className={panel==='review'?'selected':''} onClick={()=>setPanel(panel==='review'?'':'review')}>Review…</button>
    <button className={panel==='people'?'selected':''} onClick={()=>{setWantOptions(true);setPanel(panel==='people'?'':'people')}}>Reviewers & labels…</button>
    <button className={panel==='merge'?'selected':''} onClick={()=>{setWantOptions(true);setPanel(panel==='merge'?'':'merge')}}><GitMerge size={13}/>Merge…</button>
    {onCompose&&<button disabled={working!==''} onClick={()=>act('comments',async()=>{const r=await api.get<{prompt:string;comments:unknown[];reviews:unknown[]}>(path(`/projects/${projectId}/pr/comments${q}`));if(!r.comments.length&&!r.reviews.length){notify('No review comments yet');return}onCompose(r.prompt);notify('Review comments are in the composer')})}>{spin('comments',<MessageSquarePlus size={13}/>)}Address review comments</button>}
    <button onClick={()=>window.open(p.url,'_blank')}><ExternalLink size={13}/>GitHub</button><button className="icon-button" title="Refresh" aria-label="Refresh pull request" onClick={()=>pr.refetch()}><RefreshCw size={12} className={pr.isFetching?'spin':''}/></button></div>}
   {open_&&panel==='review'&&<div className="pr-form">
    <div className="pr-events" role="radiogroup" aria-label="Review verdict">{EVENTS.map(([v,l])=><label key={v}><input type="radio" name="pr-event" checked={event===v} onChange={()=>setEvent(v)}/>{l}</label>)}</div>
    <textarea aria-label="Review" rows={6} placeholder={event==='approve'?'Optional note with the approval':'What should change, and why (markdown)'} value={review} onChange={e=>setReview(e.target.value)}/>
    <div className="git-actions"><button disabled={working!==''} title="An agent reads the diff under Read only and drafts a review for you to edit" onClick={()=>act('draft-review',async()=>{const r=await api.post<{event:Event;body:string;model_name:string}>(path(`/projects/${projectId}/pr/review/draft${q}`));setEvent(r.event);setReview(r.body);notify(`${r.model_name} drafted a review`)})}>{spin('draft-review',<Sparkles size={13}/>)}Draft with an agent</button>
     <button className="primary" disabled={working!==''||(event!=='approve'&&!review.trim())} onClick={()=>act('review',async()=>{await post('/review',{event,body:review});setReview('');setPanel('');notify('Review submitted')})}>{spin('review',<CheckCircle2 size={13}/>)}Submit review</button></div>
    <small className="muted">Submitted on GitHub as you, through gh. GitHub does not let you approve or request changes on your own pull request.</small></div>}
   {open_&&panel==='people'&&<div className="pr-form">
    <div className="pr-inline"><input aria-label="Reviewer" list="pr-people" placeholder="GitHub login or org/team" value={person} onChange={e=>setPerson(e.target.value)}/><datalist id="pr-people">{options.data?.people.map(x=><option key={x} value={x}/>)}</datalist>
     <button disabled={!person.trim()||working!==''} onClick={()=>act('people',async()=>{await post('/edit',{add_reviewers:[person.trim()]});setPerson('');notify('Review requested')})}>{spin('people',<UserPlus size={13}/>)}Request review</button></div>
    <div className="pr-labels">{options.isPending?<span className="muted">Reading labels…</span>:options.data?.labels.map(l=>{const on=p.labels?.includes(l.name);return <button key={l.name} className={on?'selected':''} style={{borderColor:`#${l.color}`}} onClick={()=>act('label',()=>post('/edit',on?{remove_labels:[l.name]}:{add_labels:[l.name]}))}><i style={{background:`#${l.color}`}}/>{l.name}</button>})}</div>
    {options.data&&<div className="pr-inline"><label>Base branch</label><select aria-label="Base branch" value={p.base||''} onChange={e=>act('base',async()=>{await post('/edit',{base:e.target.value});notify(`Now based on ${e.target.value}`)})}>{[...new Set([p.base||'',...options.data.bases.map(b=>b.branch)])].filter(b=>b&&b!==p.head).map(b=>{const o=options.data!.bases.find(x=>x.branch===b);return <option key={b} value={b}>{b}{o?.number?` (#${o.number} ${o.title})`:''}</option>})}</select></div>}</div>}
   {open_&&panel==='merge'&&<div className="pr-form">
    <div className="pr-inline"><select aria-label="Merge method" value={method} onChange={e=>setMethod(e.target.value as typeof method)}><option value="squash">Squash and merge</option><option value="merge">Merge commit</option><option value="rebase">Rebase and merge</option></select>
     <label className="pr-draft"><input type="checkbox" checked={deleteBranch} onChange={e=>setDeleteBranch(e.target.checked)}/>Delete the branch after</label></div>
    {p.merge_state&&<small className="muted">GitHub says this is {p.merge_state.replace('_',' ')}{p.mergeable==='conflicting'?' and has conflicts with its base':''}.{stack?.above.length?` ${stack.above.length} pull request${stack.above.length===1?' is':'s are'} stacked on it; GitHub moves them onto this one's base when its branch is deleted.`:''}</small>}
    <div className="git-actions">{p.auto_merge?<button disabled={working!==''} onClick={()=>act('auto',async()=>{await post('/merge',{disable_auto:true});notify('Auto-merge turned off')})}>{spin('auto',<GitMerge size={13}/>)}Turn off auto-merge</button>
     :<button disabled={working!==''} title="GitHub merges it once required checks and reviews pass" onClick={()=>act('auto',async()=>{await post('/merge',{method,auto:true,delete_branch:deleteBranch});notify('Auto-merge is on')})}>{spin('auto',<GitMerge size={13}/>)}Merge when ready</button>}
     <button className="primary" disabled={working!==''} onClick={()=>act('merge',async()=>{await post('/merge',{method,delete_branch:deleteBranch});setPanel('');notify('Merged')})}>{spin('merge',<GitMerge size={13}/>)}Merge now</button></div></div>}
  </div>;}
 return <div className="pr-section"><div className="pr-head"><GitPullRequest size={13}/><span>No pull request for <code>{branch}</code>{upstream?'':' (not pushed yet)'}</span>{!open&&<button onClick={()=>{setOpen(true);setWantOptions(true)}} disabled={!branch||branch==='HEAD'}>Create pull request</button>}</div>
  {open&&<div className="pr-form">{error&&<p className="thread-error">{error}</p>}<input aria-label="Pull request title" placeholder="Title" value={title} onChange={e=>setTitle(e.target.value)}/><textarea aria-label="Pull request description" rows={4} placeholder="Description (markdown)" value={body} onChange={e=>setBody(e.target.value)}/>
   <div className="pr-inline"><label>Base</label><select aria-label="Base branch" value={base} onChange={e=>setBase(e.target.value)}><option value="">{options.data?`${options.data.default_branch} (default)`:'The default branch'}</option>{options.data?.bases.filter(b=>b.number&&b.branch!==branch).map(b=><option key={b.branch} value={b.branch}>Stack on #{b.number} {b.title} ({b.branch})</option>)}</select></div>
   <div className="pr-inline"><input aria-label="Reviewers" list="pr-people-new" placeholder="Reviewers, comma separated" value={reviewers} onChange={e=>setReviewers(e.target.value)}/><datalist id="pr-people-new">{options.data?.people.map(x=><option key={x} value={x}/>)}</datalist></div>
   {!!options.data?.labels.length&&<div className="pr-labels">{options.data.labels.map(l=>{const on=labels.includes(l.name);return <button key={l.name} type="button" className={on?'selected':''} style={{borderColor:`#${l.color}`}} onClick={()=>setLabels(v=>on?v.filter(x=>x!==l.name):[...v,l.name])}><i style={{background:`#${l.color}`}}/>{l.name}</button>})}</div>}
   <div className="git-actions"><button disabled={working!==''} title="Ask the agent to draft the title and description from the branch's commits and diff" onClick={()=>act('draft',async()=>{const r=await api.post<{title:string;body:string;model_name:string}>(path(`/projects/${projectId}/pr/description${q}`));setTitle(r.title);setBody(r.body);notify(`${r.model_name} drafted the description`)})}>{spin('draft',<Sparkles size={13}/>)}Write description</button>
    <label className="pr-draft"><input type="checkbox" checked={draft} onChange={e=>setDraft(e.target.checked)}/>Draft</label>
    <button className="primary" disabled={!title.trim()||working!==''} onClick={()=>act('create',async()=>{await api.post(path(`/projects/${projectId}/pr${q}`),{title:title.trim(),body,draft,base:base||null,reviewers:reviewers.split(',').map(r=>r.trim()).filter(Boolean),labels});setOpen(false);await refresh();notify('Pull request opened')})}>{spin('create',<GitPullRequest size={13}/>)}Push and open</button><button onClick={()=>setOpen(false)}>Cancel</button></div></div>}
 </div>;
}
