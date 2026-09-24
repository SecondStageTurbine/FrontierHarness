/** Keyboard shortcuts: each action has a default, and the user may rebind or clear it in Settings. */
export type Action='newSession'|'search'|'focusComposer'|'toggleSidebar'|'files'|'changes'|'terminal'|'preview'|'tasks'|'dashboard'|'resume'|'settings';

export const ACTIONS:{id:Action;label:string;default:string}[]=[
 {id:'newSession',label:'New session',default:'Ctrl+N'},
 {id:'search',label:'Search sessions',default:'Ctrl+K'},
 {id:'focusComposer',label:'Focus the composer',default:'Ctrl+L'},
 {id:'toggleSidebar',label:'Show or hide the sidebar',default:'Ctrl+B'},
 {id:'files',label:'Files panel',default:'Ctrl+Shift+E'},
 {id:'changes',label:'Changes panel',default:'Ctrl+Shift+G'},
 {id:'terminal',label:'Terminal panel',default:'Ctrl+`'},
 {id:'preview',label:'Preview panel',default:'Ctrl+Shift+V'},
 {id:'tasks',label:'Tasks panel',default:'Ctrl+Shift+K'},
 {id:'dashboard',label:'All projects',default:'Ctrl+Shift+D'},
 {id:'resume',label:'Resume from CLI',default:'Ctrl+Shift+H'},
 {id:'settings',label:'Settings',default:'Ctrl+,'},
];

const STORE='frontier.keys';
const CODES:Record<string,string>={Backquote:'`',Comma:',',Period:'.',Slash:'/',Backslash:'\\',Semicolon:';',Quote:"'",BracketLeft:'[',BracketRight:']',Minus:'-',Equal:'=',Space:'Space'};

/** The combination a key press makes, in the same spelling the bindings use; null for a bare modifier. */
export function comboFromEvent(e:{ctrlKey:boolean;metaKey:boolean;altKey:boolean;shiftKey:boolean;code:string;key:string}){
 if(['Control','Meta','Alt','Shift'].includes(e.key))return null;
 const key=e.code.startsWith('Key')?e.code.slice(3):e.code.startsWith('Digit')?e.code.slice(5):CODES[e.code]||e.code;
 return [e.ctrlKey||e.metaKey?'Ctrl':'',e.altKey?'Alt':'',e.shiftKey?'Shift':'',key].filter(Boolean).join('+');
}

function overrides():Partial<Record<Action,string>>{try{return JSON.parse(localStorage.getItem(STORE)||'{}')}catch{return {}}}

export function bindings():Record<Action,string>{
 const custom=overrides();
 return Object.fromEntries(ACTIONS.map(a=>[a.id,custom[a.id]??a.default])) as Record<Action,string>;
}

export function setBinding(id:Action,combo:string|null){
 const custom=overrides();
 if(combo===null)delete custom[id];else custom[id]=combo;
 try{localStorage.setItem(STORE,JSON.stringify(custom))}catch{/* private mode: the change lasts this session */}
 window.dispatchEvent(new Event('frontier:keys'));
}

export function resetBindings(){try{localStorage.removeItem(STORE)}catch{/* nothing stored */}window.dispatchEvent(new Event('frontier:keys'))}

export function actionFor(combo:string|null):Action|null{
 if(!combo)return null;
 const all=bindings();
 return (Object.keys(all) as Action[]).find(id=>all[id]===combo)||null;
}

/** How a binding reads on a key cap: "Ctrl Shift E". */
export const keyLabel=(combo:string)=>combo?combo.split('+').join(' '):'None';
