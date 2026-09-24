/** Which machine this window works on: this computer, or another Frontier paired in Settings → Environments.
 *  On another machine every API call goes through /api/env/<id>, except the few that are about this computer itself. */
type Env={id:string;name:string;url:string};
const KEY='frontier.environment';
function read():Env|null{try{return JSON.parse(localStorage.getItem(KEY)||'null')}catch{return null}}
export const environment=read();
const LOCAL=['/environments','/env/','/auth/','/logout','/remote','/push','/tray','/password'];
export const apiBase=(path:string)=>environment&&!LOCAL.some(p=>path.startsWith(p))?`/api/env/${environment.id}`:'/api';
/** Things that act on the computer in front of you (terminal, folder picker, opening an editor) are off while on another machine. */
export const onThisComputer=!environment;
export function switchEnvironment(next:Env|null){
 try{if(next)localStorage.setItem(KEY,JSON.stringify({id:next.id,name:next.name,url:next.url}));else localStorage.removeItem(KEY)}catch{/* private mode */}
 window.location.assign('/');
}
