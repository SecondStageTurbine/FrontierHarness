import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
export function MarkdownOutput({text}:{text:string}){return <div className="markdown"><Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>{text}</Markdown></div>}
