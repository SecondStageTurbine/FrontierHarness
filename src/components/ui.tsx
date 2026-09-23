import {useId,useRef,useState,cloneElement,isValidElement,type ReactElement,type ReactNode} from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import {X,ArrowRight,AlertCircle,Workflow,LoaderCircle,Check,RotateCcw} from 'lucide-react';
import {Link} from 'react-router-dom';
import {ApiError} from '../lib/api';
/** A wide dialog can be dragged wider or narrower by either edge; the width is remembered per dialog on this device. */
export function Modal({open,onClose,title,description,children,wide=false}:{open:boolean;onClose:()=>void;title:string;description?:string;children:ReactNode;wide?:boolean}){
 const key=wide?`frontier.dialog.${title.replace(/[^A-Za-z0-9]+/g,'-').toLowerCase()}`:null;
 const [width,setWidth]=useState<number|null>(()=>{try{const v=key?localStorage.getItem(key):null;return v?Number(v):null}catch{return null}});
 const latest=useRef<number|null>(width);
 function resize(side:'left'|'right',e:React.PointerEvent<HTMLDivElement>){
  e.preventDefault();const startX=e.clientX;const initial=(e.currentTarget.parentElement as HTMLElement).getBoundingClientRect().width;
  // The dialog is centred, so the dragged edge follows the pointer when the width grows by twice the movement.
  const move=(m:PointerEvent)=>{const next=Math.round(Math.max(560,Math.min(window.innerWidth-32,initial+(m.clientX-startX)*(side==='right'?2:-2))));latest.current=next;setWidth(next)};
  const end=()=>{document.removeEventListener('pointermove',move);document.removeEventListener('pointerup',end);try{if(key&&latest.current)localStorage.setItem(key,String(latest.current))}catch{/* private mode */}};
  document.addEventListener('pointermove',move);document.addEventListener('pointerup',end);
 }
 return <Dialog.Root open={open} onOpenChange={o=>!o&&onClose()}><Dialog.Portal><Dialog.Overlay className="modal-overlay"/><Dialog.Content className={`modal ${wide?'wide':''}`} style={wide&&width?{width:`${width}px`}:undefined}>
  {wide&&<><div className="modal-resizer left" role="separator" aria-label="Resize dialog" aria-orientation="vertical" onPointerDown={e=>resize('left',e)}/><div className="modal-resizer right" role="separator" aria-label="Resize dialog" aria-orientation="vertical" onPointerDown={e=>resize('right',e)}/></>}
  <div className="section-head"><div><Dialog.Title>{title}</Dialog.Title><Dialog.Description>{description||'Configure and save your changes.'}</Dialog.Description></div><Dialog.Close className="icon-button" aria-label="Close dialog"><X size={18}/></Dialog.Close></div>{children}</Dialog.Content></Dialog.Portal></Dialog.Root>;
}
export function StatusBadge({status}:{status:string}){const label:Record<string,string>={complete:'Complete',limit_reached:'Iteration limit',interrupted:'Interrupted',running:'Running',queued:'Queued',failed:'Failed',cancelled:'Cancelled',ready:'Ready',approved:'Approved',rejected:'Changes required',connected:'Connected',untested:'Not tested',error:'Connection error'};return <span className={`status status-${status}`}>{status==='running'?<LoaderCircle size={12} className="spin"/>:['complete','approved','connected'].includes(status)?<Check size={12}/>:status==='limit_reached'?<RotateCcw size={12}/>:<span className="status-dot"/>}{label[status]||status}</span>}
export function EmptyState({title,description,action,to}:{title:string;description:string;action?:string;to?:string}){return <div className="empty"><div className="empty-icon"><Workflow size={26}/></div><h3>{title}</h3><p>{description}</p>{action&&to&&<Link className="button primary" to={to}>{action}<ArrowRight size={15}/></Link>}</div>}
export function ErrorState({error,retry}:{error:Error;retry?:()=>void}){const isolated=error instanceof ApiError&&error.code==='TenantIsolationViolationException';return <div role="alert" className="error-panel"><AlertCircle size={22}/><div><h3>{isolated?'Workspace access blocked':'Something needs your attention'}</h3><p>{error.message}</p><div className="actions">{retry&&<button onClick={retry}>Try again</button>}<Link to="/" className="button">Return to workspace</Link></div></div></div>}
export function Loading({text='Loading workspace data…'}:{text?:string}){return <div className="skeleton-panel" role="status"><LoaderCircle size={20} className="spin"/><span>{text}</span><div className="skeleton"/><div className="skeleton short"/></div>}
export function PageHeading({eyebrow,title,description,children}:{eyebrow?:string;title:string;description:string;children?:ReactNode}){return <div className="page-heading"><div>{eyebrow&&<span className="eyebrow">{eyebrow}</span>}<h1>{title}</h1><p>{description}</p></div>{children&&<div className="actions">{children}</div>}</div>}
export function Field({label,hint,children}:{label:string;hint?:string;children:ReactNode}){const id=useId();return <div className="field"><label htmlFor={id}>{label}</label>{isValidElement(children)?cloneElement(children as ReactElement<{id:string;'aria-describedby'?:string}>,{id,'aria-describedby':hint?`${id}-hint`:undefined}):children}{hint&&<small id={`${id}-hint`}>{hint}</small>}</div>}
export function Confirm({open,onClose,onConfirm,title,description,busy=false}:{open:boolean;onClose:()=>void;onConfirm:()=>void;title:string;description:string;busy?:boolean}){return <Modal open={open} onClose={onClose} title={title} description={description}><div className="actions end"><button onClick={onClose}>Keep it</button><button className="danger" disabled={busy} onClick={onConfirm}>{busy?'Working…':'Confirm'}</button></div></Modal>}
