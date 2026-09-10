import {useEffect} from 'react';
import {useQueryClient} from '@tanstack/react-query';
import {useResource,useWorkspace} from './context';
import {terminal,type Run} from '../types';
export function useRun(id?:string){
 const {tenant,path}=useWorkspace();const client=useQueryClient();const query=useResource<Run>(`/runs/${id}`,!!id);
 useEffect(()=>{if(!id||!tenant||!query.data||terminal(query.data.status))return;let timer:ReturnType<typeof setTimeout>|undefined;let alive=true;const stream=new EventSource('/api'+path(`/runs/${id}/events`));
 const refresh=()=>{if(alive)void client.invalidateQueries({queryKey:['tenant',tenant.id,`/runs/${id}`]})};
 stream.onmessage=e=>{if(!timer)timer=setTimeout(()=>{timer=undefined;refresh()},160);try{const event=JSON.parse(e.data);if(event.type==='file.changed'||event.type==='workflow.complete')void client.invalidateQueries({queryKey:['tenant',tenant.id,'/projects']})}catch{/* A malformed notification is recovered by polling the authoritative run. */}};
 stream.addEventListener('done',()=>{stream.close();refresh();void client.invalidateQueries({queryKey:['tenant',tenant.id]})});
 const interval=setInterval(refresh,3000);return()=>{alive=false;stream.close();clearInterval(interval);if(timer)clearTimeout(timer)};
 },[id,tenant?.id,query.data?.status]);return query;
}
