import {useEffect,useState} from 'react';
import {isTauri} from '@tauri-apps/api/core';
import {useResource,useWorkspace} from '../app/context';
import {useLiveSession} from '../app/useLiveSession';
import {Inspector} from './Inspector';
import type {InspectorTab} from './Conversation';
import {working,type Project} from '../types';

/** Open a panel in its own window, for a second monitor. One window per panel; asking again focuses it. */
export async function popOut(tab:InspectorTab){
 const url=`${location.origin}/?popout=${encodeURIComponent(tab)}`;
 if(!isTauri()){window.open(url,`frontier-${tab}`,'width=960,height=820');return}
 const {WebviewWindow}=await import('@tauri-apps/api/webviewWindow');
 const label=`popout-${tab.toLowerCase()}`;
 const existing=await WebviewWindow.getByLabel(label);
 if(existing){await existing.setFocus();return}
 new WebviewWindow(label,{url,title:`Frontier · ${tab}`,width:960,height:820});
}

/** A popped-out panel follows whatever project and session the main window has open. */
export default function Popout({initial}:{initial:InspectorTab}){
 const {tenant}=useWorkspace();
 const read=()=>{let pid='',sid='';try{pid=localStorage.getItem(`frontier.project.${tenant?.id}`)||'';sid=pid?localStorage.getItem(`frontier.session.${pid}`)||'':''}catch{/* private mode */}return {pid,sid}};
 const [selection,setSelection]=useState(read);
 const [tab,setTab]=useState<InspectorTab>(initial);
 const [filePath,setFilePath]=useState<string|null>(null),[fileLine,setFileLine]=useState<number|undefined>();
 useEffect(()=>{const sync=()=>setSelection(read());sync();window.addEventListener('storage',sync);return()=>window.removeEventListener('storage',sync)},[tenant?.id]);
 useEffect(()=>{document.title=`Frontier · ${tab}`},[tab]);
 const projects=useResource<Project[]>('/projects');
 const project=projects.data?.find(p=>p.id===selection.pid);
 const session=useLiveSession(project?.id,selection.sid||undefined).data;
 if(!project)return <div className="loading-page">{projects.isPending?'Opening…':'Open a project in the main Frontier window; this panel follows it.'}</div>;
 return <div className="popout-shell"><Inspector key={`${project.id}-${session?.id||''}`} project={project} session={session} tab={tab} onTab={setTab} onClose={()=>{if(isTauri())void import('@tauri-apps/api/window').then(w=>w.getCurrentWindow().close());else window.close()}}
  filePath={filePath} fileLine={fileLine} onFile={(f,l)=>{setFilePath(f);setFileLine(l)}} busy={working(session?.messages.at(-1))} popped/></div>;
}
