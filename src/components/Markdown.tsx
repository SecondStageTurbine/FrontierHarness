import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import type {ComponentProps} from 'react';
// A relative path with an extension, optionally :line, as agents write them: `src/app.py:42`.
const FILE_REF=/^(?:[\w@.+-]+\/)*[\w@+-][\w@.+-]*\.[A-Za-z0-9]{1,8}(?::(\d+))?$/;
export function parseFileRef(text:string){const m=text.match(FILE_REF);if(!m||text.startsWith('http')||!text.includes('.'))return null;const line=m[1]?Number(m[1]):undefined;return {path:line?text.slice(0,text.lastIndexOf(':')):text,line}}
export function MarkdownOutput({text,onFile}:{text:string;onFile?:(path:string,line?:number)=>void}){
 const code=(props:ComponentProps<'code'>)=>{const {children,className,...rest}=props;const raw=typeof children==='string'?children:Array.isArray(children)&&typeof children[0]==='string'&&children.length===1?children[0]:null;const ref=onFile&&!className&&raw&&!raw.includes('\n')?parseFileRef(raw.trim()):null;
  return ref?<button type="button" className="file-link" title={`Open ${ref.path}${ref.line?` at line ${ref.line}`:''}`} onClick={()=>onFile!(ref.path,ref.line)}>{raw}</button>:<code className={className} {...rest}>{children}</code>};
 return <div className="markdown"><Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]} components={{code}}>{text}</Markdown></div>;
}
