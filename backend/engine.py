"""Deterministic, transport-independent orchestration with durable append-only history.

One execution per tenant prevents concurrent runs from racing monthly budget checks.
The application intentionally runs a single worker; interrupted calls are never replayed
automatically, because a provider may already have billed them.
"""
import asyncio
import json
from datetime import datetime, timezone
from pydantic import ValidationError
from .schemas import PlanArtifact, ReviewReport, BuildArtifact, WorkflowInput
from .store import now, uid, public_model
from .broker import ModelBroker, ProviderError
from .projects import ProjectFiles

TERMINAL = {'complete', 'failed', 'cancelled', 'limit_reached', 'interrupted'}
ALLOWED = {
    'QUEUED': {'DISCUSSION'}, 'DISCUSSION': {'DISCUSSION', 'PLANNING'},
    'PLANNING': {'BUILDING'}, 'BUILDING': {'BUILDING', 'REVIEW'},
    'REVIEW': {'REPLAN', 'COMPLETE'}, 'REPLAN': {'PLANNING'},
}

class WorkflowIterationLimitExceeded(RuntimeError):
    pass

class ExecutionLimitError(RuntimeError):
    pass

class Engine:
    def __init__(self, store, broker=None):
        self.store = store
        self.broker = broker or ModelBroker(store)
        self.files = ProjectFiles(store)
        self.tasks: dict[tuple[str, str], asyncio.Task] = {}

    def validate_workflow(self, tenant_id, workflow):
        w = WorkflowInput.model_validate({k:v for k,v in workflow.items() if k in WorkflowInput.model_fields})
        tenant = self.store.tenant_internal(tenant_id)
        if w.max_workflow_iterations > tenant['max_workflow_iterations']:
            raise ValueError('Workflow iterations exceed this workspace’s maximum.')
        for stage in w.stages:
            self.store.get(tenant_id, 'models', stage.model_id)
            if stage.fallback_model_id:
                self.store.get(tenant_id, 'models', stage.fallback_model_id)
            if stage.agent_id:
                agent = self.store.get(tenant_id, 'agents', stage.agent_id)
                if agent['role'] != stage.role:
                    raise ValueError('The agent role must match the stage.')
        for aid in w.attachment_ids:
            self.store.get(tenant_id, 'attachments', aid)
        if w.project_id:
            self.store.get(tenant_id,'projects',w.project_id)
            if w.session_id:
                session=self.store.get(tenant_id,'sessions',w.session_id)
                if session['project_id']!=w.project_id:
                    raise ValueError('Session does not belong to this project.')
        return w

    def ensure_available(self, tenant_id):
        if any(r['status'] not in TERMINAL for r in self.store.list(tenant_id, 'runs')):
            raise ValueError('This workspace already has an active run. Wait for it or cancel it first.')

    def start(self, tenant_id, workflow_id):
        self.ensure_available(tenant_id)
        workflow = self.store.get(tenant_id, 'workflows', workflow_id)
        self.validate_workflow(tenant_id, workflow)
        if workflow.get('archived'):
            raise ValueError('Restore this workflow before running it.')
        tenant = self.store.tenant_internal(tenant_id)
        model_ids = {s['model_id'] for s in workflow['stages']} | {s['fallback_model_id'] for s in workflow['stages'] if s.get('fallback_model_id')}
        run = dict(id=uid(), workflow_id=workflow_id, name=workflow['name'], workflow=workflow,
                   models={m:public_model(self.store.get(tenant_id,'models',m)) for m in model_ids},
                   corporate_rules=tenant['corporate_rules'], status='queued', phase='QUEUED', iteration=0,
                   max_workflow_iterations=workflow['max_workflow_iterations'], stages=[],
                   transcript=[], cost=0.0, cost_complete=True, input_tokens=0, output_tokens=0,
                   usage_complete=True, created_at=now(), started_at=now(), finished_at=None,
                   error=None, error_code=None, changes=[], commands=[], file_hashes={})
        self.store.put(tenant_id, 'runs', run)
        self.store.event(tenant_id, run['id'], 'workflow.started', 'Workflow queued for execution.')
        self.schedule(tenant_id, run['id'])
        return self.store.get(tenant_id, 'runs', run['id'])

    def schedule(self, tenant_id, run_id):
        key = (tenant_id, run_id)
        task = asyncio.create_task(self.execute(tenant_id, run_id))
        self.tasks[key] = task
        task.add_done_callback(lambda _: self.tasks.pop(key, None))

    def continue_run(self, tenant_id, run_id, limit):
        self.ensure_available(tenant_id)
        run = self.store.get(tenant_id, 'runs', run_id)
        tenant = self.store.tenant_internal(tenant_id)
        if run['status'] != 'limit_reached':
            raise ValueError('Only a run stopped at its review iteration limit can continue.')
        if not run['iteration'] < limit <= tenant['max_workflow_iterations']:
            raise ValueError('Choose a higher limit within the workspace maximum.')
        run.update(max_workflow_iterations=limit, status='queued', phase='REPLAN', error=None, error_code=None, finished_at=None)
        self.store.put(tenant_id, 'runs', run)
        self.store.event(tenant_id, run_id, 'workflow.replanning', 'Iteration limit increased. Continuing with the previous review feedback.', iteration=run['iteration']+1)
        self.schedule(tenant_id, run_id)
        return run

    async def cancel(self, tenant_id, run_id):
        run = self.store.get(tenant_id, 'runs', run_id)
        if run['status'] in TERMINAL:
            raise ValueError('This run has already finished.')
        task = self.tasks.get((tenant_id, run_id))
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        run = self.store.get(tenant_id, 'runs', run_id)
        if run['status'] not in TERMINAL:
            self.finish(tenant_id, run, 'cancelled', 'Run cancelled. In-flight provider charges may still apply.')
        return self.store.get(tenant_id, 'runs', run_id)

    def transition(self, tenant_id, run, phase):
        if phase not in ALLOWED.get(run['phase'], set()):
            raise RuntimeError(f'Invalid state transition {run["phase"]} → {phase}')
        run['phase'] = phase
        self.store.put(tenant_id, 'runs', run)

    def finish(self, tenant_id, run, status, error=None, code=None):
        run.update(status=status, finished_at=now(), error=error, error_code=code)
        for stage in run['stages']:
            if stage['status'] == 'running':
                stage.update(status='cancelled' if status == 'cancelled' else 'failed', finished_at=now(), error=error)
                run['cost_complete'] = False
                run['usage_complete'] = False
        self.store.put(tenant_id, 'runs', run)
        message = 'Project check complete.' if run['phase'] == 'COMMAND' else 'Workflow complete. Approved by the reviewer.'
        self.store.event(tenant_id, run['id'], f'workflow.{status}', error or message, iteration=run['iteration'])

    def budget_check(self, tenant_id, run, config, system, prompt, stage):
        tenant = self.store.tenant_internal(tenant_id)
        if run['iteration'] > min(run['max_workflow_iterations'], tenant['max_workflow_iterations']):
            raise WorkflowIterationLimitExceeded('The workspace iteration limit was reached. No additional model call was made.')
        if len(prompt.encode()) > 600000:
            raise ExecutionLimitError('The accumulated context is too large. Reduce attachments or start a smaller workflow.')
        limits = tenant.get('max_cost_per_run'), tenant.get('monthly_budget')
        if not any(limits):
            return
        if config.get('input_price') is None or config.get('output_price') is None or not run['cost_complete']:
            raise ExecutionLimitError('A budget is configured, but usage or model pricing is unknown. Set verified per-million-token prices in Models before running.')
        # UTF-8 bytes provide a deliberately conservative text token bound, with
        # room for message/schema overhead. Prices are administrator-configured estimates.
        estimate = ((len((system+prompt).encode()) + 12000) * config['input_price'] + stage['max_tokens'] * config['output_price']) / 1e6
        month = datetime.now(timezone.utc).strftime('%Y-%m')
        monthly = [r for r in self.store.list(tenant_id, 'runs') if r['created_at'].startswith(month)]
        if limits[1] and any(not r['cost_complete'] for r in monthly):
            raise ExecutionLimitError('Monthly usage includes unknown charges. Reconcile usage before running with a budget.')
        if limits[0] and run['cost'] + estimate > limits[0]:
            raise ExecutionLimitError('The next model call could exceed the run budget. No call was made.')
        if limits[1] and sum(r['cost'] for r in monthly) + estimate > limits[1]:
            raise ExecutionLimitError('The next model call could exceed the monthly workspace budget. No call was made.')

    async def call_stage(self, tenant_id, run, stage):
        role = stage['role']
        self.transition(tenant_id, run, role.upper())
        entry = dict(id=uid(), stage_id=stage['id'], role=role, iteration=run['iteration'], model_id=stage['model_id'], status='running', started_at=now(), finished_at=None, output='', artifact=None, input_tokens=None, output_tokens=None, cost=None)
        run['stages'].append(entry)
        self.store.put(tenant_id, 'runs', run)
        self.store.event(tenant_id, run['id'], 'stage.started', f'{role.capitalize()} started.', iteration=run['iteration'], stage_id=entry['id'], model_id=stage['model_id'])
        contracts = {'planning':PlanArtifact, 'building':BuildArtifact, 'review':ReviewReport}
        contract = contracts.get(role)
        instructions = {
            'discussion':'Surface the objective, constraints, assumptions, and acceptance criteria. Use the whole user discussion. Be concise; make assumptions explicit.',
            'planning':'Create a precise implementation plan from the entire discussion and all prior reviewer feedback. Address each rejection.',
            'building':'Produce the complete deliverable described by the validated plan. Return a summary and files with full text contents using paths relative to the project. Only include files you need to add or modify. Never abbreviate file contents. Include a commands array of relevant verification commands. Supported commands: python -m pytest, python -m unittest discover, python -m compileall, npm test, npm run build/test/lint/typecheck. Never claim to have executed a command; the harness records actual results after your response.',
            'review':'Perform a zero-tolerance review of the plan AND actual build outputs against the objective, corporate rules and approval rules. Reject unresolved vulnerabilities and alignment failures. Explain every rejection. Never approve merely because a prior model claims success. If approved is true, detected_vulnerabilities and rejection_reasons MUST both be empty arrays. If approved is false, explain the unresolved issues in these arrays. Do not put resolved issues, informational notes, or praise in rejection_reasons.',
        }
        system = instructions[role] + '\nMandatory corporate rules:\n' + run['corporate_rules'] + '\nApproval rules:\n' + run['workflow']['approval_rules'] + f'\nMinimum approval quality: {run["workflow"]["minimum_quality"]}.'
        if stage.get('agent_id'):
            agent = self.store.get(tenant_id,'agents',stage['agent_id'])
            system += '\nAgent instructions:\n' + agent['system_prompt'] + '\nContext rules:\n' + agent['context_rules']
        system += '\nAdditional stage instructions:\n' + stage['prompt']
        system += '\nExecution mode: '+run['workflow'].get('execution_mode','propose')+'.'
        prompt = '\n\n'.join(run['transcript'])
        for attempt, model_id in enumerate([stage['model_id']] + ([stage['fallback_model_id']] if stage.get('fallback_model_id') else [])):
            config = self.store.get(tenant_id, 'models', model_id)
            self.budget_check(tenant_id, run, config, system, prompt, stage)
            entry['model_id'] = model_id
            self.store.put(tenant_id, 'runs', run)
            self.store.event(tenant_id, run['id'], 'model.request.started', f'{config["name"]} is working.', model_id=model_id, iteration=run['iteration'])
            try:
                result = await self.broker.invoke(tenant_id, model_id, system, prompt, stage, contract.model_json_schema() if contract else None)
                break
            except ProviderError as exc:
                # Failed requests may have been billed. Preserve uncertainty rather than $0.
                run['cost_complete'] = False
                run['usage_complete'] = False
                if attempt == 0 and stage.get('fallback_model_id') and exc.retryable:
                    self.store.event(tenant_id, run['id'], 'model.fallback', str(exc) + ' Trying the configured fallback.', iteration=run['iteration'])
                    continue
                raise
        entry.update(output=result.text, input_tokens=result.input_tokens, output_tokens=result.output_tokens, finished_at=now())
        run['input_tokens'] += result.input_tokens or 0
        run['output_tokens'] += result.output_tokens or 0
        if result.input_tokens is None or result.output_tokens is None:
            run['usage_complete'] = False
        if config.get('input_price') is not None and config.get('output_price') is not None and result.input_tokens is not None and result.output_tokens is not None:
            entry['cost'] = (result.input_tokens*config['input_price'] + result.output_tokens*config['output_price'])/1e6
            run['cost'] += entry['cost']
        else:
            run['cost_complete'] = False
        self.store.put(tenant_id, 'runs', run)  # Preserve raw output and usage even if validation fails.
        self.store.event(tenant_id, run['id'], 'model.request.completed', f'{config["name"]} returned its output.', iteration=run['iteration'], model_id=model_id)
        if result.error:
            raise ProviderError(result.error)
        if contract:
            text = result.text.strip()
            if text.startswith('```') and text.endswith('```'):
                text = text.split('\n',1)[1].rsplit('```',1)[0].strip()
            artifact = contract.model_validate_json(text)
            entry['artifact'] = artifact.model_dump()
            if role == 'review' and artifact.approved and artifact.quality_score < run['workflow']['minimum_quality']:
                entry['artifact']['approved'] = False
                entry['artifact']['rejection_reasons'] = ['Quality score is below the workflow’s required threshold.']
            if role == 'review' and entry['artifact']['approved'] and any(c['iteration']==run['iteration'] and c['status']!='completed' for c in run.get('commands',[])):
                entry['artifact']['approved']=False
                entry['artifact']['rejection_reasons']=['The project verification commands did not all pass. Address the recorded command failures.']
            if role == 'review':
                self.store.event(tenant_id, run['id'], 'review.completed', 'Review approved.' if entry['artifact']['approved'] else 'Changes required. Review feedback saved.', iteration=run['iteration'])
            elif role == 'planning':
                self.store.event(tenant_id, run['id'], 'plan.validated', 'Structured plan validated successfully.', iteration=run['iteration'])
        run['transcript'].append(f'{role.upper()} · iteration {run["iteration"]}\n' + (json.dumps(entry['artifact']) if entry['artifact'] else result.text))
        if role=='building' and run['workflow'].get('project_id'):
            self.files.apply(tenant_id,run,entry,entry['artifact']['files'])
            if run['workflow'].get('execution_mode')=='execute':
                for command in entry['artifact'].get('commands',[]):
                    result=await self.files.run_command(tenant_id,run,command)
                    run['transcript'].append('ACTUAL COMMAND RESULT\n'+json.dumps(result))
        entry.update(status='completed', finished_at=now())
        self.store.put(tenant_id, 'runs', run)
        self.store.event(tenant_id, run['id'], 'stage.completed', f'{role.capitalize()} completed.', iteration=run['iteration'], stage_id=entry['id'])
        return entry

    async def execute(self, tenant_id, run_id):
        run = self.store.get(tenant_id, 'runs', run_id)
        try:
            run['status'] = 'running'
            if not run['transcript']:
                run['transcript'] = ['USER OBJECTIVE\n'+run['workflow']['objective'], 'USER CONTEXT\n'+run['workflow']['context']]
                if run['workflow'].get('project_id'):
                    context,run['file_hashes']=self.files.snapshot(tenant_id,run['workflow']['project_id'])
                    run['transcript'].append('PROJECT FILES\n'+'\n\n'.join(context))
                    self.store.event(tenant_id,run_id,'files.read',f'Read {len(context)} project files into context.',paths=list(run['file_hashes']))
                for aid in run['workflow']['attachment_ids']:
                    a = self.store.get(tenant_id,'attachments',aid)
                    run['transcript'].append('ATTACHMENT '+a['name']+'\n'+a['content'])
                for stage in run['workflow']['stages']:
                    if stage['role'] == 'discussion':
                        await self.call_stage(tenant_id, run, stage)
            while True:
                tenant = self.store.tenant_internal(tenant_id)
                if run['iteration'] >= min(run['max_workflow_iterations'], tenant['max_workflow_iterations']):
                    raise WorkflowIterationLimitExceeded(f'The workflow completed {run["iteration"]} review cycles without approval. No additional model calls were made.')
                run['iteration'] += 1
                for stage in run['workflow']['stages']:
                    if stage['role'] != 'discussion':
                        entry = await self.call_stage(tenant_id, run, stage)
                if entry['artifact']['approved']:
                    self.transition(tenant_id, run, 'COMPLETE')
                    self.finish(tenant_id, run, 'complete')
                    return
                if run['iteration'] >= min(run['max_workflow_iterations'], tenant['max_workflow_iterations']):
                    raise WorkflowIterationLimitExceeded(f'The workflow completed {run["iteration"]} review cycles without approval. No additional model calls were made.')
                self.transition(tenant_id, run, 'REPLAN')
                self.store.event(tenant_id, run_id, 'workflow.replanning', 'Reviewer requested changes. Sending the full feedback to the planner.', iteration=run['iteration']+1)
        except asyncio.CancelledError:
            self.finish(tenant_id, run, 'cancelled', 'Run cancelled. In-flight provider charges may still apply.')
            raise
        except WorkflowIterationLimitExceeded as exc:
            self.finish(tenant_id, run, 'limit_reached', str(exc), type(exc).__name__)
        except ValidationError as exc:
            run['validation_errors']=[{'path':list(e['loc']),'message':e['msg']} for e in exc.errors()]
            self.finish(tenant_id, run, 'failed', 'The model returned an invalid structured artifact. Inspect its output, then revise the stage prompt or model and retry.', 'ArtifactValidationError')
        except (ProviderError, ExecutionLimitError, ValueError) as exc:
            self.finish(tenant_id, run, 'failed', str(exc), type(exc).__name__)
        except Exception:
            self.finish(tenant_id, run, 'failed', 'Execution stopped safely because of an internal error. Inspect the execution trace.', 'InternalExecutionError')

    def recover(self):
        with self.store.db() as db:
            tenant_ids = [r['id'] for r in db.execute('SELECT id FROM tenants').fetchall()]
        for tenant_id in tenant_ids:
            for run in self.store.list(tenant_id, 'runs'):
                if run['status'] not in TERMINAL:
                    self.finish(tenant_id, run, 'interrupted', 'The server restarted during execution. Previous outputs are preserved. Start a new run to retry; in-flight calls were not replayed.', 'ServerRestart')
