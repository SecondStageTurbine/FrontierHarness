import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import type {ComponentProps} from 'react';
import type {ExtraProps} from 'react-markdown';
import {Copy} from 'lucide-react';
import {useWorkspace} from '../app/context';
// A relative path with an extension, optionally :line, as agents write them: `src/app.py:42`.
const FILE_REF=/^(?:[\w@.+-]+\/)*[\w@+-][\w@.+-]*\.[A-Za-z0-9]{1,8}(?::(\d+))?$/;
function parseFileRef(text:string){const m=text.match(FILE_REF);if(!m||text.startsWith('http')||!text.includes('.'))return null;const line=m[1]?Number(m[1]):undefined;return {path:line?text.slice(0,text.lastIndexOf(':')):text,line}}
// A code block carries its own copy button, so a snippet can be taken without selecting it by hand.
function Pre({node:_node,...props}:ComponentProps<'pre'>&ExtraProps){const {copy}=useWorkspace();
 return <div className="code-wrap"><pre {...props}/><button type="button" className="code-copy" title="Copy code" aria-label="Copy code" onClick={e=>copy((e.currentTarget.previousElementSibling?.textContent||'').replace(/\n$/,''))}><Copy size={13}/></button></div>}
export function MarkdownOutput({text,onFile}:{text:string;onFile?:(path:string,line?:number)=>void}){
 const code=(props:ComponentProps<'code'>)=>{const {children,className,...rest}=props;const raw=typeof children==='string'?children:Array.isArray(children)&&typeof children[0]==='string'&&children.length===1?children[0]:null;const ref=onFile&&!className&&raw&&!raw.includes('\n')?parseFileRef(raw.trim()):null;
  return ref?<button type="button" className="file-link" title={`Open ${ref.path}${ref.line?` at line ${ref.line}`:''}`} onClick={()=>onFile!(ref.path,ref.line)}>{raw}</button>:<code className={className} {...rest}>{children}</code>};
 return <div className="markdown"><Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]} components={{code,pre:Pre}}>{text}</Markdown></div>;
}
