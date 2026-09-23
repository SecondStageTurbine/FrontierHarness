import {createContext,useContext,useState,useEffect,type ReactNode} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {useNavigate} from 'react-router-dom';
import {api} from '../lib/api';
import type {Tenant} from '../types';

interface WorkspaceContext {tenant:Tenant|null;tenants:Tenant[];switchTenant:(id:string)=>void;refreshTenants:()=>void;path:(p:string)=>string;notify:(text:string)=>void;theme:string;setTheme:(s:string)=>void;accent:string;setAccent:(s:string)=>void;advanced:boolean;setAdvanced:(v:boolean)=>void}
export const THEMES:{id:string;name:string;blurb:string;dark:boolean}[]=[{id:'dark',name:'Dark',blurb:'Focused and understated',dark:true},{id:'midnight',name:'Midnight',blurb:'Deep blue, like the icon',dark:true},{id:'warm',name:'Warm',blurb:'Amber on charcoal',dark:true},{id:'light',name:'Light',blurb:'Clear and open',dark:false}];
/** Mix a hex colour toward black or white by a fraction. */
function mix(hex:string,to:number,amount:number){const n=parseInt(hex.replace('#',''),16);const c=[n>>16&255,n>>8&255,n&255].map(v=>Math.round(v+(to-v)*amount));return '#'+c.map(v=>v.toString(16).padStart(2,'0')).join('')}
const Workspace=createContext<WorkspaceContext>(null!);
export function WorkspaceProvider({children}:{children:ReactNode}){
 const client=useQueryClient(),navigate=useNavigate();
 const [id,setId]=useState(localStorage.getItem('frontier.tenant')||'');
 const [switchingTo,setSwitchingTo]=useState<string|null>(null);
 const [theme,setTheme]=useState(localStorage.getItem('frontier.theme')||'dark');
 const [accent,setAccent]=useState(localStorage.getItem('frontier.accent')||'');
 const [advanced,setAdvanced]=useState(localStorage.getItem('frontier.advanced')==='true');
 const [toast,setToast]=useState('');
 const query=useQuery({queryKey:['tenants'],queryFn:({signal})=>api.get<Tenant[]>('/tenants',signal)});
 const tenants=query.data||[];const tenant=tenants.find(t=>t.id===id)||tenants[0]||null;
 useEffect(()=>{if(switchingTo!==null&&tenants.some(t=>t.id===switchingTo))setSwitchingTo(null)},[switchingTo,tenants]);
 useEffect(()=>{document.documentElement.dataset.theme=theme;localStorage.setItem('frontier.theme',theme)},[theme]);
 useEffect(()=>{const root=document.documentElement.style;localStorage.setItem('frontier.accent',accent);if(!/^#[0-9a-fA-F]{6}$/.test(accent)){['--primary','--primary-bg','--accent-surface'].forEach(v=>root.removeProperty(v));return}const dark=THEMES.find(t=>t.id===theme)?.dark!==false;root.setProperty('--primary',dark?mix(accent,255,.15):mix(accent,0,.1));root.setProperty('--primary-bg',accent);root.setProperty('--accent-surface',dark?mix(accent,0,.78):mix(accent,255,.88))},[accent,theme]);
 useEffect(()=>{localStorage.setItem('frontier.advanced',String(advanced))},[advanced]);
 useEffect(()=>{if(!toast)return;const timer=setTimeout(()=>setToast(''),4500);return()=>clearTimeout(timer)},[toast]);
 function switchTenant(next:string){if(next===tenant?.id)return;client.cancelQueries({queryKey:['tenant']});client.removeQueries({queryKey:['tenant']});setSwitchingTo(next&&!tenants.some(t=>t.id===next)?next:null);setId(next);localStorage.setItem('frontier.tenant',next);navigate('/');setToast('Switched workspace. All resources have been refreshed.')}
 return <Workspace.Provider value={{tenant,tenants,switchTenant,refreshTenants:()=>{void client.invalidateQueries({queryKey:['tenants']})},path:p=>`/t/${tenant?.id}${p}`,notify:setToast,theme,setTheme,accent,setAccent,advanced,setAdvanced}}>{query.isPending||switchingTo!==null?<div className="loading-page">Opening your workspace…</div>:query.error?<div className="loading-page">{query.error.message}<button onClick={()=>query.refetch()}>Retry</button></div>:children}{toast&&<div role="status" className="toast">{toast}</div>}</Workspace.Provider>
}
export const useWorkspace=()=>useContext(Workspace);
export function useResource<T>(resource:string,enabled=true){const {tenant,path}=useWorkspace();return useQuery({queryKey:['tenant',tenant?.id,resource],queryFn:({signal})=>api.get<T>(path(resource),signal),enabled:!!tenant&&enabled})}
export function useRefresh(){const client=useQueryClient();const {tenant}=useWorkspace();return()=>client.invalidateQueries({queryKey:['tenant',tenant?.id]})}
