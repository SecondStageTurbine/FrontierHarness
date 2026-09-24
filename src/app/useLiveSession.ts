import {useEffect} from 'react';
import {useQueryClient} from '@tanstack/react-query';
import {useResource,useWorkspace} from './context';
import {working,type Session} from '../types';
import {apiBase} from '../lib/environment';

/** One conversation, kept live while a turn is running.
 *
 * The turn happens in a subprocess, so the only way to see it move is the event stream; the
 * conversation itself is re-read whenever something arrives. Polling stays underneath as the
 * recovery path, because a dropped stream must not leave a finished turn looking unfinished.
 */
export function useLiveSession(projectId?:string,sessionId?:string){
 const {tenant,path}=useWorkspace();const client=useQueryClient();
 const route=`/projects/${projectId}/sessions/${sessionId}`;
 const query=useResource<Session>(route,!!projectId&&!!sessionId);
 const live=working(query.data?.messages.at(-1));
 useEffect(()=>{
  if(!tenant||!projectId||!sessionId||!live)return;
  let alive=true,timer:ReturnType<typeof setTimeout>|undefined;
  const refresh=()=>{if(alive)void client.invalidateQueries({queryKey:['tenant',tenant.id,route]})};
  const events=path(`${route}/events`);
  const stream=new EventSource(apiBase(events)+events);
  stream.onmessage=()=>{if(!timer)timer=setTimeout(()=>{timer=undefined;refresh()},160)};
  stream.addEventListener('done',()=>{stream.close();refresh();
   // The agent wrote to the folder directly, so the file tree is stale the moment a turn ends.
   void client.invalidateQueries({queryKey:['tenant',tenant.id,`/projects/${projectId}/files`]});
   void client.invalidateQueries({queryKey:['tenant',tenant.id,`/projects/${projectId}/sessions`]})});
  const interval=setInterval(refresh,3000);
  return()=>{alive=false;stream.close();clearInterval(interval);if(timer)clearTimeout(timer)};
 },[tenant?.id,projectId,sessionId,live]);
 return query;
}

