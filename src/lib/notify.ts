import {isPermissionGranted,requestPermission,sendNotification} from '@tauri-apps/plugin-notification';
import {isTauri} from '@tauri-apps/api/core';
export {isTauri};
export type NotifyLevel='off'|'silent'|'sound';
export const notifyLevel=():NotifyLevel=>(localStorage.getItem('frontier.notify') as NotifyLevel)||'sound';
export const setNotifyLevel=(level:NotifyLevel)=>localStorage.setItem('frontier.notify',level);
/** A short two-tone chime from the audio API, so no sound file ships with the app. */
export function chime(){try{const ctx=new AudioContext();[[880,0],[1175,.12]].forEach(([hz,at])=>{const o=ctx.createOscillator(),g=ctx.createGain();o.frequency.value=hz;o.connect(g);g.connect(ctx.destination);g.gain.setValueAtTime(.0001,ctx.currentTime+at);g.gain.exponentialRampToValueAtTime(.15,ctx.currentTime+at+.02);g.gain.exponentialRampToValueAtTime(.0001,ctx.currentTime+at+.35);o.start(ctx.currentTime+at);o.stop(ctx.currentTime+at+.4)});setTimeout(()=>void ctx.close(),800)}catch{/* no audio device */}}
/** Tell the user a turn finished while Frontier was in the background. Silent when the window has focus. */
export async function notifyTurnDone(title:string,body:string){
 const level=notifyLevel();if(level==='off'||document.hasFocus())return;
 if(level==='sound')chime();
 try{
  if(isTauri()){if(!(await isPermissionGranted())&&(await requestPermission())!=='granted')return;sendNotification({title,body});return}
  if('Notification' in window){if(Notification.permission==='default')await Notification.requestPermission();if(Notification.permission==='granted')new Notification(title,{body})}
 }catch{/* notifications unavailable on this platform */}
}
