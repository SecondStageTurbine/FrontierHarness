export type Mode='read'|'edit'|'auto';
export const modes:Mode[]=['read','edit','auto'];
export const modeLabels:Record<Mode,string>={read:'Read only',edit:'Edit files',auto:'Full auto'};
export const modeHints:Record<Mode,string>={read:'The agent can read the project and answer. It cannot change anything.',edit:'The agent can read and write files in this project.',auto:'The agent can read, write, and run commands in this project as you.'};
// Only a local agent command line tool can take a turn; an API key reaches a model, not an agent.
export const agentProviders=['claude_cli','codex_cli','opencode_cli','gemini_cli'];
export const providerNames:Record<string,string>={claude_cli:'Claude',codex_cli:'Codex',opencode_cli:'OpenCode',gemini_cli:'Gemini',anthropic:'Anthropic',openai:'OpenAI',custom_openai:'OpenAI Compatible',ollama:'Ollama / local',typesafe:'TypeSafe'};
export const CLI_NAMES:Record<string,string>={claude_cli:'claude',codex_cli:'codex',opencode_cli:'opencode',gemini_cli:'gemini'};
export const ADAPTIVE='adaptive';
export const ADAPTIVE_HINT='Picks the least expensive agent that can do this message, and hands it to a stronger one if that agent fails.';
// The capability registry, as far as the interface sees it: what a row may say it is good at.
export const capabilities=['coding','reasoning','planning','debugging','architecture','review','tool_use','repository','instruction_following','speed'] as const;
export interface Model {id:string; name:string; provider:string; model_name:string; account:string|null; base_url:string|null; input_price:number|null; output_price:number|null; key_hint:string; status:string; latency_ms?:number; capabilities?:Record<string,number>|null; cost_class?:string|null; enabled?:boolean; online?:boolean|null; track?:string}
export interface Routing {mode:'adaptive'|'manual'; status?:string; classified_by?:string; chosen?:{id:string;name:string;because:string}; requirements?:{task_type:string;complexity:string;risk:string;reason:string}; candidates?:{id:string;name:string;cost_class:string;sufficient:boolean}[]; attempts?:{id:string;name:string;outcome:string}[]; escalations?:number}
export interface FileChange {id:string; path:string; status:'added'|'modified'|'removed'; before:string|null; after:string|null}
export interface CommandResult {id:string; command:string; started_at:string; finished_at:string|null; status:string; output:string; exit_code:number|null}
export interface ContextChip {kind:'file'|'terminal'|'diff'|'selection'; path?:string|null; start?:number|null; end?:number|null; text?:string|null; label?:string|null}
export interface TeamTask {id:string; title:string; instructions:string; needs:string[]; agent?:string|null; parallel:boolean; depends_on:string[]; status:'pending'|'working'|'fixing'|'done'|'failed'; session_id?:string|null; model_id?:string|null; model_name?:string|null; report:string; merge?:string|null; changed?:string[]; cross_review?:boolean}
export interface Team {status:'planning'|'working'|'reviewing'|'fixing'|'done'; lead:string; summary:string; tasks:TeamTask[]; agents?:string[]}
export interface Message {sandbox_blocked?:string[]; id:string; role:'user'|'assistant'; content:string; created_at:string; context?:ContextChip[]; team?:Team;
 status?:'running'|'complete'|'failed'|'cancelled'; error?:string|null; finished_at?:string|null;
 model_id?:string; model_name?:string; provider?:string; mode?:Mode; switched_from?:string|null;
 changes?:FileChange[]; input_tokens?:number|null; output_tokens?:number|null; cost?:number|null; routing?:Routing; checkpoint?:{before:string;after:string}|null; reverted_at?:string|null}
export interface QueuedMessage {id:string; content:string; model_id:string; mode:Mode; created_at:string}
export interface Session {id:string; project_id:string; name:string; messages:Message[]; commands:CommandResult[]; created_at:string; updated_at:string; pinned?:boolean; archived?:boolean; queue?:QueuedMessage[]; worktree?:{path:string;branch:string}|null; approvals?:Approval[]; automation_id?:string; auto_named?:boolean; team_parent?:string|null; pull_request?:{number:number;url:string;state:string;title:string}|null; snoozed_until?:string|null; setup?:{command:string;exit_code:number|null;output:string}|null; imported_from?:string; summary?:{text:string;through:string;created_at:string;model_name:string;count:number}|null; context?:{chars:number;limit:number;dropped:number;compacted:number}}
export interface Approval {id:string; session_id:string; message_id:string; tool_name:string; input:Record<string,unknown>; tool_use_id?:string|null; created_at:string; kind?:'permission'|'question'}
export interface McpServer {id:string; name:string; transport:'stdio'|'http'; command?:string|null; args?:string[]; env?:Record<string,string>; url?:string|null; enabled?:boolean}
export interface Skill {name:string; kind:'skill'|'command'; scope:'project'|'user'; provider:string; description:string; path:string}
export interface Automation {id:string; name:string; project_id:string; prompt:string; model_id:string; mode:Mode; every?:number|null; daily_at?:string|null; enabled:boolean; secret:string; next_run_at?:string|null; last_run_at?:string|null; last_outcome?:string; last_trigger?:string; last_session_id?:string; runs?:number}
export interface GitEntry {path:string; status:string; staged:boolean; unstaged:boolean}
export interface GitStatus {repo:boolean; available:boolean; root?:string; branch?:string; upstream?:string|null; ahead?:number; behind?:number; entries?:GitEntry[]; has_head?:boolean; worktree?:{path:string;branch:string}|null; output?:string}
export interface Project {id:string; name:string; root:string; created_at:string; last_session_id?:string; last_model_id?:string; last_mode?:Mode; default_mode?:Mode|null; default_model_id?:string|null; worktree_setup?:string|null; worktree_copy?:string[]; protect_env?:boolean; dev_command?:string|null; memory?:string|null; turn_minutes?:number|null; agent_browser?:boolean; agent_browser_visible?:boolean; agent_browser_mode?:'fresh'|'mine'; agent_browser_channel?:'chrome'|'msedge'; agent_browser_token_set?:boolean; sandbox?:{files:boolean;network:'open'|'agent'|'allowlist';allow:string[]}|null}
export interface ProjectFile {path:string; size:number; text:boolean}
export interface Tenant {id:string; name:string; environment:string; created_at:string; router_model_id?:string|null; rules?:string|null; auto_archive_days?:number|null; memory_auto?:boolean; worktree_cleanup_days?:number|null}
export interface PullRequest {number:number; title:string; url:string; state:string; draft:boolean; review?:string|null; base?:string; head?:string; checks:{success:number;failure:number;pending:number}; additions?:number; deletions?:number; author?:string; labels?:string[]; requested?:string[]; reviews?:{author:string|null;state:string}[]; auto_merge?:string|null; mergeable?:string|null; merge_state?:string|null;
 stack?:{below:{number:number;title:string;url:string;headRefName:string}[];above:{number:number;title:string;url:string;headRefName:string}[];root_base?:string}}
export const working=(m?:Message)=>m?.role==='assistant'&&m.status==='running';
