import {useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {FileUp,Moon,Search,Sun} from 'lucide-react';
import {useWorkspace} from '../../app/context';
import {Modal} from '../../components/ui';
import {api} from '../../lib/api';
import {fromVsCode,parseJsonc} from '../../lib/vscodeTheme';

type Installed={label:string;editor:string;dark:boolean;extension:string;path:string};

/** Pick a colour theme installed in VS Code (or Cursor, Windsurf, VSCodium), or a theme .json file. */
export function ThemeImport({open,onClose}:{open:boolean;onClose:()=>void}){
 const {path,setCustomTheme,setTheme,notify}=useWorkspace();
 const list=useQuery({queryKey:['vscode-themes'],enabled:open,staleTime:60000,queryFn:({signal})=>api.get<Installed[]>(path('/themes/vscode'),signal)});
 const [query,setQuery]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState('');
 function use(theme:{name?:string;type?:string;colors?:Record<string,string>},fallback:string){
  if(!theme.colors||!Object.keys(theme.colors).length)throw new Error('That theme has no colours Frontier can use.');
  const custom=fromVsCode(theme,fallback);setCustomTheme(custom);setTheme('custom');notify(`Using ${custom.name}`);onClose();
 }
 async function pick(t:Installed){
  setBusy(t.path);setError('');
  try{use(await api.get(path(`/themes/vscode/theme?path=${encodeURIComponent(t.path)}`)),t.label)}catch(e){setError((e as Error).message)}finally{setBusy('')}
 }
 async function file(f?:File){
  if(!f)return;setError('');
  try{use(parseJsonc(await f.text()),f.name.replace(/\.json$/i,''))}catch(e){setError(`Could not use ${f.name}: ${(e as Error).message}`)}
 }
 const shown=(list.data||[]).filter(t=>`${t.label} ${t.extension} ${t.editor}`.toLowerCase().includes(query.toLowerCase()));
 return <Modal open={open} onClose={onClose} title="Import a VS Code theme" description="Frontier takes the theme's colours for its own backgrounds, text, borders and accent." wide>
  <div className="theme-import-bar"><label className="file-search"><Search size={12}/><input aria-label="Filter themes" placeholder="Filter installed themes…" value={query} onChange={e=>setQuery(e.target.value)}/></label>
   <label className="button"><FileUp size={13}/>Choose a theme file…<input type="file" accept=".json,application/json" hidden onChange={e=>void file(e.target.files?.[0])}/></label></div>
  {error&&<p className="error-text">{error}</p>}
  {list.isPending?<p className="muted">Looking for installed themes…</p>:!list.data?.length?<p className="muted">No VS Code, Cursor, Windsurf or VSCodium themes were found on this computer. Choose a theme's .json file instead; extension themes live in its <code>themes</code> folder.</p>:
   <div className="theme-import-list">{shown.map(t=><button key={t.path} disabled={!!busy} onClick={()=>void pick(t)} title={t.path}>{t.dark?<Moon size={13}/>:<Sun size={13}/>}<strong>{t.label}</strong><small>{t.extension} · {t.editor}</small></button>)}</div>}
 </Modal>;
}
