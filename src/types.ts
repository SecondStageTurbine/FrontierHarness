export type Mode='read'|'edit'|'auto';
export const modes:Mode[]=['read','edit','auto'];
export const modeLabels:Record<Mode,string>={read:'Read only',edit:'Edit files',auto:'Full auto'};
export const modeHints:Record<Mode,string>={read:'The agent can read the project and answer. It cannot change anything.',edit:'The agent can read and write files in this project.',auto:'The agent can read, write, and run commands in this project as you.'};
// Only a local agent command line tool can take a turn; an API key reaches a model, not an agent.
export const agentProviders=['claude_cli','codex_cli','opencode_cli'];
export const providerNames:Record<string,string>={claude_cli:'Claude',codex_cli:'Codex',opencode_cli:'OpenCode',anthropic:'Anthropic',openai:'OpenAI',custom_openai:'OpenAI Compatible',ollama:'Ollama / local',typesafe:'TypeSafe'};
export const ADAPTIVE='adaptive';
export const ADAPTIVE_HINT='Picks the least expensive agent that can do this message, and hands it to a stronger one if that agent fails.';
// The capability registry, as far as the interface sees it: what a row may say it is good at.
export const capabilities=['coding','reasoning','planning','debugging','architecture','review','tool_use','repository','instruction_following','speed'] as const;
export interface Model {id:string; name:string; provider:string; model_name:string; account:string|null; base_url:string|null; input_price:number|null; output_price:number|null; key_hint:string; status:string; latency_ms?:number; capabilities?:Record<string,number>|null; cost_class?:string|null; enabled?:boolean; online?:boolean|null}
export interface Routing {mode:'adaptive'|'manual'; status?:string; classified_by?:string; chosen?:{id:string;name:string;because:string}; requirements?:{task_type:string;complexity:string;risk:string;reason:string}; candidates?:{id:string;name:string;cost_class:string;sufficient:boolean}[]; attempts?:{id:string;name:string;outcome:string}[]; escalations?:number}
export interface FileChange {id:string; path:string; status:'added'|'modified'|'removed'; before:string|null; after:string|null}
export interface CommandResult {id:string; command:string; started_at:string; finished_at:string|null; status:string; output:string; exit_code:number|null}
export interface Message {id:string; role:'user'|'assistant'; content:string; created_at:string;
 status?:'running'|'complete'|'failed'|'cancelled'; error?:string|null; finished_at?:string|null;
 model_id?:string; model_name?:string; provider?:string; mode?:Mode; switched_from?:string|null;
 changes?:FileChange[]; input_tokens?:number|null; output_tokens?:number|null; cost?:number|null; routing?:Routing}
export interface QueuedMessage {id:string; content:string; model_id:string; mode:Mode; created_at:string}
export interface Session {id:string; project_id:string; name:string; messages:Message[]; commands:CommandResult[]; created_at:string; updated_at:string; pinned?:boolean; archived?:boolean; queue?:QueuedMessage[]}
export interface Project {id:string; name:string; root:string; created_at:string; last_session_id?:string; last_model_id?:string; last_mode?:Mode}
export interface ProjectFile {path:string; size:number; text:boolean}
export interface SessionEvent {seq:number; type:string; message:string; time:string; message_id?:string}
export interface Tenant {id:string; name:string; environment:string; created_at:string; router_model_id?:string|null}
export const working=(m?:Message)=>m?.role==='assistant'&&m.status==='running';
