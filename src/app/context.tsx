import {createContext,useContext,useState,useEffect,type ReactNode} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {useNavigate} from 'react-router-dom';
import {api} from '../lib/api';
import type {Tenant} from '../types';

interface WorkspaceContext {tenant:Tenant|null;tenants:Tenant[];switchTenant:(id:string)=>void;refreshTenants:()=>void;path:(p:string)=>string;notify:(text:string)=>void;theme:string;setTheme:(s:string)=>void;advanced:boolean;setAdvanced:(v:boolean)=>void}
const Workspace=createContext<WorkspaceContext>(null!);
export function WorkspaceProvider({children}:{children:ReactNode}){
 const client=useQueryClient(),navigate=useNavigate();
 const [id,setId]=useState(localStorage.getItem('frontier.tenant')||'');
 const [switchingTo,setSwitchingTo]=useState<string|null>(null);
 const [theme,setTheme]=useState(localStorage.getItem('frontier.theme')||'dark');
 const [advanced,setAdvanced]=useState(localStorage.getItem('frontier.advanced')==='true');
 const [toast,setToast]=useState('');
 const query=useQuery({queryKey:['tenants'],queryFn:({signal})=>api.get<Tenant[]>('/tenants',signal)});
 const tenants=query.data||[];const tenant=tenants.find(t=>t.id===id)||tenants[0]||null;
 useEffect(()=>{if(switchingTo!==null&&tenants.some(t=>t.id===switchingTo))setSwitchingTo(null)},[switchingTo,tenants]);
 useEffect(()=>{document.documentElement.dataset.theme=theme;localStorage.setItem('frontier.theme',theme)},[theme]);
 useEffect(()=>{localStorage.setItem('frontier.advanced',String(advanced))},[advanced]);
 useEffect(()=>{if(!toast)return;const timer=setTimeout(()=>setToast(''),4500);return()=>clearTimeout(timer)},[toast]);
 function switchTenant(next:string){if(next===tenant?.id)return;client.cancelQueries({queryKey:['tenant']});client.removeQueries({queryKey:['tenant']});setSwitchingTo(next&&!tenants.some(t=>t.id===next)?next:null);setId(next);localStorage.setItem('frontier.tenant',next);navigate('/');setToast('Switched workspace. All resources have been refreshed.')}
 return <Workspace.Provider value={{tenant,tenants,switchTenant,refreshTenants:()=>{void client.invalidateQueries({queryKey:['tenants']})},path:p=>`/t/${tenant?.id}${p}`,notify:setToast,theme,setTheme,advanced,setAdvanced}}>{query.isPending||switchingTo!==null?<div className="loading-page">Opening your workspace…</div>:query.error?<div className="loading-page">{query.error.message}<button onClick={()=>query.refetch()}>Retry</button></div>:children}{toast&&<div role="status" className="toast">{toast}</div>}</Workspace.Provider>
}
export const useWorkspace=()=>useContext(Workspace);
export function useResource<T>(resource:string,enabled=true){const {tenant,path}=useWorkspace();return useQuery({queryKey:['tenant',tenant?.id,resource],queryFn:({signal})=>api.get<T>(path(resource),signal),enabled:!!tenant&&enabled})}
export function useRefresh(){const client=useQueryClient();const {tenant}=useWorkspace();return()=>client.invalidateQueries({queryKey:['tenant',tenant?.id]})}
