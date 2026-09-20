import {useState,useRef} from 'react';
import {Mic,Square,Loader} from 'lucide-react';
import {useWorkspace,useResource} from '../app/context';
import {api} from '../lib/api';
import type {Model} from '../types';

/** Dictate into a field, through a model this workspace already connected.
 *
 * The recording is posted, transcribed, and dropped: it is how a prompt was typed, not an
 * artifact of the workspace. Nothing renders until an OpenAI or compatible model exists to
 * transcribe with, so the control never promises something the workspace cannot do.
 */
export function Dictate({onText,label}:{onText:(text:string)=>void;label?:string}){
 const {path,notify}=useWorkspace();const models=useResource<Model[]>('/models');
 const [state,setState]=useState<'idle'|'recording'|'working'>('idle');
 const recorder=useRef<MediaRecorder|null>(null);
 const model=(models.data||[]).find(m=>['openai','custom_openai'].includes(m.provider));
 if(!model)return null;
 async function start(){
  try{
   const stream=await navigator.mediaDevices.getUserMedia({audio:true});
   const chunks:Blob[]=[],rec=new MediaRecorder(stream);recorder.current=rec;
   rec.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)};
   rec.onstop=async()=>{
    stream.getTracks().forEach(t=>t.stop());setState('working');
    try{
     const data=new FormData();data.append('model_id',model!.id);data.append('file',new Blob(chunks,{type:rec.mimeType||'audio/webm'}),'speech.webm');
     const {text}=await api.upload<{text:string}>(path('/transcribe'),data);
     if(text.trim())onText(text.trim());else notify('Nothing was heard in that recording.');
    }catch(e){notify((e as Error).message)}finally{setState('idle')}
   };
   rec.start();setState('recording');
  }catch{notify('The microphone is unavailable. Allow access for this app, then try again.')}
 }
 const title=state==='recording'?'Stop and transcribe':`Dictate with ${model.name}`;
 return <button type="button" className="ghost" aria-label={title} title={title} disabled={state==='working'} onClick={()=>state==='recording'?recorder.current?.stop():void start()}>
  {state==='working'?<Loader size={15} className="spin"/>:state==='recording'?<Square size={15}/>:<Mic size={15}/>}
  {label&&<span>{state==='working'?'Transcribing…':state==='recording'?'Stop dictation':label}</span>}
 </button>;
}
