import {useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {useForm} from 'react-hook-form';
import {z} from 'zod';
import {zodResolver} from '@hookform/resolvers/zod';
import {Workflow,ArrowRight,ShieldCheck,ArrowRightLeft} from 'lucide-react';
import {api} from '../lib/api';
import {Field,Loading,ErrorState} from '../components/ui';
const schema=z.object({username:z.string().min(3,'Use at least 3 characters.'),password:z.string().min(12,'Use at least 12 characters.')});
export function AuthGate({children}:{children:React.ReactNode}){
 const client=useQueryClient(),[error,setError]=useState('');
 const q=useQuery({queryKey:['auth'],queryFn:()=>api.get<{setup_required:boolean;user:{username:string}|null}>('/auth/status')});
 const form=useForm<z.infer<typeof schema>>({resolver:zodResolver(schema)});
 if(q.isPending)return <Loading text="Opening Frontier…"/>;
 if(q.error)return <ErrorState error={q.error} retry={()=>q.refetch()}/>;
 if(q.data.user)return children;
 return <div className="auth-page"><div className="auth-story"><div className="brand"><span className="brand-mark"><Workflow size={23}/></span>frontier<span className="brand-tag">STUDIO</span></div><span className="eyebrow">A BETTER WAY TO BUILD WITH AI</span><h1>One conversation.<br/><span>Any agent.</span></h1><p>Work on a project with Claude, Codex or OpenCode, and switch between them whenever you like. The conversation and the folder stay with you, not with the tool.</p><div className="auth-pipeline"><span>Claude</span><ArrowRightLeft size={14}/><span>Codex</span><ArrowRightLeft size={14}/><span>OpenCode</span></div><small>YOUR SUBSCRIPTIONS. YOUR PROJECTS. ON THIS DEVICE.</small></div><form className="auth-form" onSubmit={form.handleSubmit(async data=>{setError('');try{await api.post(`/auth/${q.data.setup_required?'setup':'login'}`,data);await client.invalidateQueries({queryKey:['auth']})}catch(e){setError((e as Error).message)}})}><div className="eyebrow">WELCOME TO FRONTIER</div><h2>{q.data.setup_required?'Create your workspace':'Welcome back'}</h2><p>{q.data.setup_required?'Set up your local administrator account to get started.':'Sign in to your workspace.'}</p><Field label="Username"><input autoComplete="username" {...form.register('username')}/></Field>{form.formState.errors.username&&<small className="error-text">{form.formState.errors.username.message}</small>}<Field label="Password" hint="At least 12 characters."><input type="password" autoComplete={q.data.setup_required?'new-password':'current-password'} {...form.register('password')}/></Field>{form.formState.errors.password&&<small className="error-text">{form.formState.errors.password.message}</small>}{error&&<p role="alert" className="error-text">{error}</p>}<button className="primary full" disabled={form.formState.isSubmitting}>{form.formState.isSubmitting?'Opening…':q.data.setup_required?'Create account':'Sign in'}<ArrowRight size={17}/></button><small className="auth-note"><ShieldCheck size={15}/>Private workspaces. Encrypted provider credentials.</small></form></div>
}
