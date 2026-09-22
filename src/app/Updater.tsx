import {useEffect,useState} from 'react';
import {Download,LoaderCircle,X} from 'lucide-react';
import {check,type Update} from '@tauri-apps/plugin-updater';
import {relaunch} from '@tauri-apps/plugin-process';
import {isTauri} from '@tauri-apps/api/core';
/** One check on launch against the GitHub release feed; the banner offers the newer version and restarts into it. */
export function useUpdate(){
 const [update,setUpdate]=useState<Update|null>(null),[state,setState]=useState<'idle'|'checking'|'installing'|'error'|'current'>('idle'),[error,setError]=useState('');
 async function lookFor(){if(!isTauri())return;setState('checking');setError('');try{const found=await check();setUpdate(found);setState(found?'idle':'current')}catch(e){setState('error');setError((e as Error).message||'Could not reach the update feed.')}}
 async function install(){if(!update)return;setState('installing');try{await update.downloadAndInstall();await relaunch()}catch(e){setState('error');setError((e as Error).message||'The update could not be installed.')}}
 useEffect(()=>{void lookFor()},[]);
 return {update,state,error,lookFor,install};
}
export function UpdateBanner({update,state,error,install,dismiss}:{update:Update|null;state:string;error:string;install:()=>void;dismiss:()=>void}){
 if(!update)return null;
 return <div className="update-banner" role="status"><Download size={14}/><span>Frontier {update.version} is available.{error&&` ${error}`}</span><button className="primary" disabled={state==='installing'} onClick={install}>{state==='installing'?<><LoaderCircle size={13} className="spin"/> Installing…</>:'Install and restart'}</button><button className="icon-button" aria-label="Not now" onClick={dismiss}><X size={13}/></button></div>;
}
