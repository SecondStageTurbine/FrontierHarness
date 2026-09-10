import React,{Suspense,lazy} from 'react';
import ReactDOM from 'react-dom/client';
import {BrowserRouter} from 'react-router-dom';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {AuthGate} from './app/Auth';
import {WorkspaceProvider,useWorkspace} from './app/context';
import {ApiError} from './lib/api';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import 'highlight.js/styles/github-dark.css';
import './styles/global.css';
const DesktopWorkspace=lazy(()=>import('./desktop/DesktopWorkspace'));
function TenantDesktop(){const {tenant}=useWorkspace();return <DesktopWorkspace key={tenant?.id||'no-workspace'}/>}
const client=new QueryClient({defaultOptions:{queries:{staleTime:5000,retry:(count,error)=>error instanceof ApiError&&error.status<500?false:count<2,refetchOnWindowFocus:true}}});
class ErrorBoundary extends React.Component<{children:React.ReactNode},{error:Error|null}>{state={error:null as Error|null};static getDerivedStateFromError(error:Error){return {error}}render(){return this.state.error?<div className="loading-page"><h2>Frontier encountered an error</h2><p>Your projects and saved sessions are safe.</p><button onClick={()=>window.location.reload()}>Reload workspace</button></div>:this.props.children}}
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><ErrorBoundary><QueryClientProvider client={client}><BrowserRouter><AuthGate><WorkspaceProvider><Suspense fallback={<div className="loading-page">Opening Frontier…</div>}><TenantDesktop/></Suspense></WorkspaceProvider></AuthGate></BrowserRouter></QueryClientProvider></ErrorBoundary></React.StrictMode>);
