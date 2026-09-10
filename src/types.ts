export type Role = 'discussion'|'planning'|'building'|'review';
export type Status = 'queued'|'running'|'complete'|'failed'|'cancelled'|'limit_reached'|'interrupted';
export interface Tenant {id:string; name:string; environment:string; max_workflow_iterations:number; max_cost_per_run:number|null; monthly_budget:number|null; model_routing_table:Partial<Record<Role,string>>; corporate_rules:string; created_at:string}
export interface Model {id:string; name:string; provider:string; model_name:string; base_url:string|null; input_price:number|null; output_price:number|null; key_hint:string; status:string; latency_ms?:number}
export interface Stage {id:string; role:Role; model_id:string; prompt:string; temperature:number|null; max_tokens:number; timeout:number; fallback_model_id:string|null; agent_id:string|null}
export interface Workflow {project_id?:string|null;session_id?:string|null;execution_mode?:ExecutionMode;id:string; name:string; objective:string; context:string; attachment_ids:string[]; stages:Stage[]; max_workflow_iterations:number; minimum_quality:number; approval_rules:string; archived:boolean; created_at:string; latest_run?:RunSummary|null}
export interface PlanArtifact {title:string; execution_steps:string[]; technical_dependencies:Record<string,string>[]; risk_assessment:string}
export interface ReviewReport {approved:boolean; quality_score:number; detected_vulnerabilities:string[]; rejection_reasons:string[]}
export interface BuildArtifact {summary:string; files:{name:string;content:string}[]}
export interface StageResult {id:string; stage_id:string; role:Role; iteration:number; model_id:string; status:string; started_at:string; finished_at:string|null; output:string; artifact:PlanArtifact|ReviewReport|BuildArtifact|null; input_tokens:number|null; output_tokens:number|null; cost:number|null; error?:string}
export interface RunEvent {seq:number;type:string;message:string;time:string;iteration?:number;model_id?:string}
export interface RunSummary {id:string;workflow_id:string;name:string;status:Status;phase:string;iteration:number;max_workflow_iterations:number;cost:number;cost_complete:boolean;input_tokens:number;output_tokens:number;usage_complete:boolean;created_at:string;started_at:string;finished_at:string|null;error:string|null;error_code:string|null;quality_score:number|null}
export interface Run extends RunSummary {workflow:Workflow;models:Record<string,Model>;stages:StageResult[];events:RunEvent[];transcript:string[];changes:FileChange[];commands:CommandResult[]}
export interface Agent {id:string;name:string;role:Role;system_prompt:string;model_id:string;context_rules:string}
export interface Prompt {id:string;name:string;role:Role;content:string}
export interface Usage {run_id:string;workflow_id:string;workflow_name:string;date:string;role:Role;model_id:string;model_name:string;provider:string;cost:number|null;input_tokens:number|null;output_tokens:number|null}
export const roles:Role[]=['discussion','planning','building','review'];
export const roleNames:Record<Role,string>={discussion:'Discussion',planning:'Planner',building:'Builder',review:'Reviewer'};
export const terminal=(s:string)=>['complete','failed','cancelled','limit_reached','interrupted'].includes(s);
export interface Project {id:string;name:string;root:string;created_at:string;team?:Partial<Record<Role,string>>;agent_ids?:Partial<Record<Role,string>>;prompt_ids?:Partial<Record<Role,string>>;execution_mode?:ExecutionMode;last_session_id?:string}
export type ExecutionMode='propose'|'edit'|'execute';
export interface Session {id:string;project_id:string;name:string;messages:{id:string;role:string;content:string;run_id:string;created_at:string}[];run_ids:string[];created_at:string;updated_at:string}
export interface FileChange {id:string;stage_id:string;iteration:number;path:string;before:string|null;after:string;status:string;kind:string;diff:string}
export interface CommandResult {id:string;command:string;started_at:string;finished_at:string|null;status:string;output:string;exit_code:number|null;iteration:number}
export interface ProjectFile {path:string;size:number;text:boolean}
