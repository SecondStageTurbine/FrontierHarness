import {useEffect,useState} from 'react';
import {Keyboard,RotateCcw} from 'lucide-react';
import {ACTIONS,bindings,comboFromEvent,keyLabel,resetBindings,setBinding,type Action} from '../../lib/keys';

/** Every shortcut, rebindable: press Change, then the new keys. */
export function KeyboardShortcuts(){
 const [keys,setKeys]=useState(bindings),[recording,setRecording]=useState<Action|null>(null);
 useEffect(()=>{const sync=()=>setKeys(bindings());window.addEventListener('frontier:keys',sync);return()=>window.removeEventListener('frontier:keys',sync)},[]);
 useEffect(()=>{
  if(!recording)return;
  const capture=(e:KeyboardEvent)=>{
   e.preventDefault();e.stopPropagation();
   if(e.key==='Escape'){setRecording(null);return}
   const combo=comboFromEvent(e);
   // A shortcut needs Ctrl or Alt, or a function key, so it never swallows ordinary typing.
   if(!combo||!(/^(Ctrl|Alt)\+/.test(combo)||/^(Shift\+)?F\d+$/.test(combo)))return;
   setBinding(recording,combo);setRecording(null);
  };
  window.addEventListener('keydown',capture,true);return()=>window.removeEventListener('keydown',capture,true);
 },[recording]);
 const clash=(id:Action)=>keys[id]&&ACTIONS.some(a=>a.id!==id&&keys[a.id]===keys[id]);
 return <div className="shortcut-list">
  <h3><Keyboard size={16}/> Keyboard shortcuts<button type="button" className="linkish" onClick={resetBindings}>Reset all</button></h3>
  {ACTIONS.map(a=><div key={a.id} className={`shortcut-row${clash(a.id)?' clash':''}`}>
   <span>{a.label}</span>
   {recording===a.id?<kbd className="recording">Press the keys… (Esc cancels)</kbd>:<kbd>{keyLabel(keys[a.id])}</kbd>}
   <button type="button" onClick={()=>setRecording(recording===a.id?null:a.id)} aria-label={`Change shortcut for ${a.label}`}>{recording===a.id?'Cancel':'Change'}</button>
   <button type="button" className="icon-button" title="Remove this shortcut" aria-label={`Clear shortcut for ${a.label}`} onClick={()=>setBinding(a.id,'')}>×</button>
   {keys[a.id]!==a.default&&<button type="button" className="icon-button" title={`Back to ${keyLabel(a.default)}`} aria-label={`Reset shortcut for ${a.label}`} onClick={()=>setBinding(a.id,null)}><RotateCcw size={12}/></button>}
   {clash(a.id)&&<small>Also used by another action</small>}
  </div>)}
 </div>;
}
