import {useState} from 'react';
import {BarChart3} from 'lucide-react';
import {useResource} from '../../app/context';
import {PageHeading} from '../../components/ui';
import {money} from '../../lib/api';
import {UsageDock} from '../../desktop/UsageDock';

type Row={key:string;label:string;turns:number;input_tokens:number;output_tokens:number;cost:number;seconds:number};
type Payload={days:number;totals:Omit<Row,'key'|'label'>;models:Row[];projects:Row[];series:Row[]};
const k=(n:number)=>n>=1_000_000?`${(n/1_000_000).toFixed(1)}M`:n>=1000?`${(n/1000).toFixed(n>=10000?0:1)}k`:String(n);
const hours=(s:number)=>s>=3600?`${(s/3600).toFixed(1)} h`:s>=60?`${Math.round(s/60)} min`:`${s} s`;

/** What the workspace's turns added up to, from the sessions themselves. Subscription turns cost 0. */
export default function Usage(){
 const [days,setDays]=useState(30);
 const q=useResource<Payload>(`/usage?days=${days}`);
 const d=q.data;
 const Table=({rows,title}:{rows:Row[];title:string})=><table className="usage-table"><thead><tr><th>{title}</th><th>Turns</th><th>In</th><th>Out</th><th>Time</th><th>Cost</th></tr></thead><tbody>{rows.map(r=><tr key={r.key}><td>{r.label}</td><td>{r.turns}</td><td>{k(r.input_tokens)}</td><td>{k(r.output_tokens)}</td><td>{hours(r.seconds)}</td><td>{r.cost?money(r.cost):'—'}</td></tr>)}</tbody></table>;
 const peak=Math.max(1,...(d?.series||[]).map(r=>r.input_tokens+r.output_tokens));
 return <>
  <PageHeading eyebrow="WHAT THE TURNS ADDED UP TO" title="Usage" description="Tokens, time and cost per agent, per project and per day, read from the conversations in this workspace."><select value={days} onChange={e=>setDays(Number(e.target.value))} aria-label="Period"><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option><option value={365}>Last year</option></select></PageHeading>
  <UsageDock inline/>
  {q.error&&<p className="error-text">{q.error.message}</p>}
  {d&&<><div className="metrics"><div><span>Turns</span><strong>{d.totals.turns}</strong></div><div><span>Tokens in</span><strong>{k(d.totals.input_tokens)}</strong></div><div><span>Tokens out</span><strong>{k(d.totals.output_tokens)}</strong></div><div><span>Agent time</span><strong>{hours(d.totals.seconds)}</strong></div><div><span>Cost</span><strong>{d.totals.cost?money(d.totals.cost):'$0.00'}</strong></div></div>
   {d.series.length>0&&<div className="usage-chart" role="img" aria-label="Tokens per day">{d.series.map(r=><div key={r.key} title={`${r.key}: ${k(r.input_tokens+r.output_tokens)} tokens, ${r.turns} turns`}><i style={{height:`${Math.max(2,Math.round((r.input_tokens+r.output_tokens)/peak*100))}%`}}/><span>{r.key.slice(5)}</span></div>)}</div>}
   {!d.totals.turns?<p className="muted"><BarChart3 size={14}/> No finished turns in this period.</p>:<><Table rows={d.models} title="Agent"/><Table rows={d.projects} title="Project"/></>}
   <p className="muted small">Cost uses the per-million rates on each model row. Subscription logins carry no rate, so their turns show no cost; Codex reports no token counts at all.</p></>}
 </>;
}
