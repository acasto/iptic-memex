"""Task-owned live agents over application sessions; independent of research strategy."""

from dataclasses import dataclass, replace
from threading import Event, RLock, Thread
import hashlib
import time

from app.execution_scope import ExecutionScope
from app.run_helpers import child_options
from app.session_host import SessionHost
from app.session_services import HeadlessServices
from core.cancellation import CancellationToken
from core.runtime import RunRequest
from core.runtime.interaction import InteractionService
from workspace.questions import Questions
from core.runtime.contracts import RunStopped
from workspace.state import atomic_write, now, write_json
from workspace.tools import WorkspaceAccess
from workspace.scope import task_roots


@dataclass(frozen=True)
class AgentAccess:
    service: object
    agent_id: str


class TaskService:
    """Own sessions, common budgets, records, and communication within one attempt.

    The caller owns this service's lifetime. No agent role, fixed branch size, or
    mandatory synthesis is built into it. A task-wide process supervisor supplies
    hard timeout enforcement outside these cooperatively cancelled threads.
    """

    def __init__(self, store, run_id, builder, project, profile, prompt, *, deadline=None, user_input=False):
        self.user_input = user_input
        self.store, self.run_id, self.builder = store, run_id, builder
        from workspace.tasks import TaskStore
        TaskStore(store).bind_project(project.root)
        self.project, self.profile, self.prompt = project, profile, prompt
        self.deadline = deadline if deadline is not None else time.monotonic() + profile.timeout
        self.cancellation = CancellationToken()
        self._lock = RLock()
        self._agents = {}
        self._used = 0
        self._charges = []
        self._closed = False
        self._persistence_error = None
        self._save()

    def _save(self):
        write_json(self.store.run_dir(self.run_id) / 'task-state.json', {
            'version': 1, 'calls_used': self._used, 'calls_limit': self.profile.calls,
            'charges': self._charges, 'updated_at': now(),
            'agents': {key: dict(
                {k: v for k, v in node.items() if k not in (
                    'host', 'handle', 'done', 'watcher', 'session')},
                pending_inputs=[{'id': item.id, 'source': item.source}
                                for item in node['host'].pending()] if node.get('host') else [])
                for key, node in self._agents.items()},
        })

    def _check(self):
        if self._closed or self.cancellation.is_cancelled():
            raise RunStopped('cancelled', self.cancellation.reason() or 'Task closed')
        if self._persistence_error:
            raise RunStopped('persistence_failed', self._persistence_error)
        if time.monotonic() >= self.deadline:
            raise RunStopped('deadline', 'Task time allowance exhausted')
        if self._used >= self.profile.calls:
            raise RunStopped('budget', 'Shared task call allowance exhausted')

    def _charge(self, session, index):
        with self._lock:
            self._check()
            self._used += 1
            access = getattr(session, 'agents', None)
            self._charges.append({'session_id': session.session_uid,
                                  'agent_id': access.agent_id if access else None,
                                  'call': index, 'time': now()})
            self._save()

    def _configure(self, agent_id, root=False):
        def configure(session):
            session.set_flag('hooks_disabled', True)
            session.workspace = WorkspaceAccess(self.store, self.run_id, agent_id)
            session.agents = AgentAccess(self, agent_id)
            session.before_provider_call = self._charge
            session.child_call_boundary = self._charge
            if root:
                # Explicit task authority: only the selected project, no ambient
                # memory/skill roots. Blocklists still apply in the tool registry.
                allowed = frozenset((*self.profile.tools, 'workspace', 'agents')) - {'memory', 'ragsearch'}
                session.execution_scope = ExecutionScope(
                    str(self.project.root), task_roots(self.project.root, self.store),
                    allowed, self.profile.writes,
                    allowed_models=frozenset({session.get_params()['model']}))
        return configure

    def _build(self, agent_id, parent_id):
        if parent_id is None:
            options = dict(project_context=self.project, model=self.profile.model,
                           base_directory=str(self.project.root),
                           active_tools_agent=','.join(self.profile.tools) or '__none__',
                           agent_writes=self.profile.writes, max_tokens=self.profile.max_tokens,
                           cmd_tool='assistant_docker_tool', docker_always_ephemeral=True,
                           use_mcp=False, prompt=self.prompt, stream=False)
            services = HeadlessServices(file_memory=False, required_tools=('workspace', 'agents'),
                                        configure=self._configure(agent_id, root=True),
                                        before_call=self._charge)
        else:
            parent = self._agents[parent_id]['host'].session
            options = child_options(parent, {'prompt': self.prompt, 'model': self.profile.model,
                                             'max_tokens': self.profile.max_tokens})
            services = replace(options.pop('services'), required_tools=('workspace', 'agents'),
                               configure=self._configure(agent_id))
        return self.builder.build(mode='internal', services=services, **options)

    def _capacity(self):
        self._check()
        active = sum(node['status'] in ('starting', 'running') for node in self._agents.values())
        if active >= self.profile.concurrency:
            raise ValueError('Task concurrency ceiling reached; inspect or wait for existing work')

    def start(self, message, *, parent_id=None, contexts=()):
        """Start isolated work in the same project and shared task allowance."""
        if not isinstance(message, str) or not message.strip() or len(message) > 32000:
            raise ValueError('Agent task must contain 1–32000 characters')
        with self._lock:
            self._capacity()
            if len(self._agents) >= self.profile.max_sessions:
                raise ValueError('Task retained-session ceiling reached')
            if parent_id is not None:
                if parent_id not in self._agents:
                    raise ValueError('Unknown parent agent')
                parent_handle = self._agents[parent_id].get('handle')
                if parent_handle and parent_handle.cancellation.is_cancelled():
                    raise ValueError('Parent investigation is cancelled')
            agent_id = 'agent-' + str(len(self._agents) + 1)
            self._agents[agent_id] = dict(id=agent_id, parent_id=parent_id, status='starting',
                                         generations=0, outcomes=[], done=Event())
            self._save()
        session = None
        try:
            session = self._build(agent_id, parent_id)
            for kind, data in contexts:
                if session.add_context(kind, data) is None:
                    raise ValueError('Could not attach task input context')
            host = SessionHost(session)
            with self._lock:
                self._agents[agent_id]['host'] = host
            self._submit(agent_id, message)
        except BaseException as exc:
            if session is not None:
                session.close()
            with self._lock:
                self._agents[agent_id].update(status='failed', error=str(exc))
                self._agents[agent_id]['done'].set()
                self._save()
            raise
        return agent_id

    def _observe(self, agent_id, generation, event):
        try:
            with self._lock:
                if event.type != 'assistant_delta':
                    self.store.event(self.run_id, agent_id, event.type, dict(
                        event.data, execution_id=event.run_id, sequence=event.sequence,
                        generation=generation, session_id=event.session_id))
                self.store.update_worker(self.run_id, agent_id, phase=event.type,
                                         execution_id=event.run_id, generation=generation)
                if event.type == 'input_attached':
                    self._save()
        except Exception as exc:
            self._persistence_error = str(exc)
            self.cancellation.cancel('Task event persistence failed')
            raise

    def _submit(self, agent_id, message):
        with self._lock:
            node = self._agents[agent_id]
            node['status'] = 'running'
            node['generations'] += 1
            generation = node['generations']
            node['done'] = Event()
            node.pop('error', None)
            host = node['host']
            session = host.session
            params = session.get_params()
            self.store.update_worker(self.run_id, agent_id, status='running', phase='starting',
                                     model=params.get('model'), session_id=session.session_uid,
                                     parent_id=node['parent_id'], generation=generation,
                                     started_at=node.setdefault('started_at', now()),
                                     model_name=params.get('model_name'), max_tokens=params.get('max_tokens'),
                                     generation_settings={key: params[key] for key in (
                                         'max_tokens', 'max_completion_tokens', 'temperature',
                                         'reasoning_effort', 'top_p', 'seed') if params.get(key) is not None})
            prompt = session.get_context('prompt')
            prompt_text = prompt.get().get('content', '') if prompt else ''
            for filename in ('system-prompt.md', f'system-prompt-{generation}.md'):
                atomic_write(self.store.run_dir(self.run_id) / agent_id / filename, prompt_text)
            prompt_hash = hashlib.sha256(prompt_text.encode()).hexdigest()
            node.setdefault('prompt_hashes', []).append(prompt_hash)
            self.store.update_worker(self.run_id, agent_id, prompt_sha256=prompt_hash)
            atomic_write(self.store.run_dir(self.run_id) / agent_id / f'input-{generation}.md', message)
            self._save()
            parent = self._agents.get(node['parent_id'], {})
            parent_handle = parent.get('handle')
            limit = session.get_option('AGENT', 'large_input_limit', fallback=None)
            limit = int(limit) if limit is not None and str(limit).strip() else None
            owner_token = CancellationToken()
            self.cancellation.register_cleanup(lambda: owner_token.cancel('Task cancelled'))
            if parent_handle:
                parent_handle.cancellation.register_cleanup(
                    lambda: owner_token.cancel('Parent investigation cancelled'))
            handle = host.submit(
                RunRequest(message=message, execute_tools=True, max_calls=self.profile.calls,
                           input_limit=limit),
                on_event=lambda event: self._observe(agent_id, generation, event),
                interactions=InteractionService(Questions(self.store, self.run_id).handler(
                    owner_token, self.deadline)) if self.user_input else None,
                parent_run_id=(parent_handle.snapshot()['run_id'] if parent_handle else None),
                parent_cancellation=owner_token)
            node['handle'] = handle
            watcher = Thread(target=self._finish, args=(agent_id, generation, handle), daemon=True)
            node['watcher'] = watcher
            watcher.start()

    def _finish(self, agent_id, generation, handle):
        try:
            outcome = handle.result()
            summary = {key: getattr(outcome, key) for key in (
                'run_id', 'status', 'stop_reason', 'error', 'turns', 'tool_calls',
                'tool_failures', 'usage', 'cost', 'last_text')}
            with self._lock:
                node = self._agents[agent_id]
                response = summary.get('last_text') or ''
                atomic_write(self.store.run_dir(self.run_id) / agent_id /
                             f'response-{generation}.md', response)
                excerpt = response if len(response) <= 12000 else (
                    response[:11600] + '\n\n[Shortened response. Full text: ' +
                    str(self.store.run_dir(self.run_id) / agent_id / f'response-{generation}.md') + ']')
                if response.strip():
                    self.store.publish(self.run_id, agent_id,
                                       f'{agent_id} response {generation}', excerpt, [],
                                       publication_id=f'response-{generation}', kind='summary',
                                       provenance={'method': 'Model response; not independently verified'})
                node['status'] = summary['status']
                node['error'] = summary.get('error')
                node['outcomes'].append(summary)
                totals = {key: sum(out.get(key) or 0 for out in node['outcomes'])
                          for key in ('turns', 'tool_calls', 'tool_failures')}
                self.store.update_worker(self.run_id, agent_id, **{
                    key: val for key, val in summary.items() if key not in ('run_id', *totals)},
                    **totals, phase='finished', finished_at=now())
                self._save()
                parent = self._agents.get(node['parent_id'], {})
                if parent.get('host') and not self._closed:
                    try:
                        receipt = parent['host'].enqueue(
                            f"Agent {agent_id} finished ({summary['status']}). "
                            'This is a contribution, not independently verified evidence.\n' + excerpt,
                            source=agent_id)
                        self.store.event(self.run_id, agent_id, 'result_queued', {'input_id': receipt})
                        self._save()
                    except (ValueError, RuntimeError) as exc:
                        self.store.event(self.run_id, agent_id, 'result_not_queued', {'error': str(exc)})
        except BaseException as exc:
            with self._lock:
                self._agents[agent_id].update(status='failed', error=str(exc))
                self._persistence_error = str(exc)
            self.cancellation.cancel('Task result persistence failed')
        finally:
            self._agents[agent_id]['done'].set()

    def _descendant(self, caller, target):
        node = self._agents.get(target)
        while node and node['parent_id'] is not None:
            if node['parent_id'] == caller:
                return True
            node = self._agents.get(node['parent_id'])
        return False

    def followup(self, caller, target, message):
        """Continue a finished descendant's live session, preserving its history."""
        if not isinstance(message, str) or not message.strip() or len(message) > 32000:
            raise ValueError('Follow-up must contain 1–32000 characters')
        with self._lock:
            if not self._descendant(caller, target):
                raise ValueError('Only the owning ancestor may restart an agent')
            node = self._agents[target]
            if not node['done'].is_set():
                raise ValueError('Agent still running; send input or wait instead')
            self._capacity()
            self._submit(target, message)
        return self.status(target)

    def send(self, caller, target, message):
        with self._lock:
            if caller not in self._agents or target not in self._agents:
                raise ValueError('Unknown agent')
            receipt = self._agents[target]['host'].enqueue(message, source=caller)
            self.store.event(self.run_id, caller, 'message_queued',
                             {'target': target, 'input_id': receipt, 'content': message})
            self._save()
            return {'input_id': receipt, 'delivery': 'queued for a future provider call'}

    def cancel(self, caller, target):
        with self._lock:
            if not self._descendant(caller, target):
                raise ValueError('Only the owning ancestor may cancel an agent')
            handles = [node.get('handle') for agent_id, node in self._agents.items()
                       if agent_id == target or self._descendant(target, agent_id)]
        for handle in handles:
            if handle:
                handle.cancel('Cancelled by owning agent')
        return self.status(target)

    def status(self, target=None):
        with self._lock:
            if target is not None and target not in self._agents:
                raise ValueError('Unknown agent')
            nodes = [self._agents[target]] if target else self._agents.values()
            return {'calls_used': self._used, 'calls_remaining': self.profile.calls - self._used,
                    'seconds_remaining': max(0, self.deadline - time.monotonic()),
                    'agents': [dict(id=n['id'], parent_id=n['parent_id'], status=n['status'],
                                    generations=n['generations'], error=n.get('error'),
                                    stop_reason=n['outcomes'][-1]['stop_reason'] if n['outcomes'] else None,
                                    result=(n['outcomes'][-1]['last_text'] or '')[:12000]
                                    if n['outcomes'] else None,
                                    result_truncated=bool(n['outcomes'] and len(
                                        n['outcomes'][-1]['last_text'] or '') > 12000),
                                    result_path=str(self.store.run_dir(self.run_id) / n['id'] /
                                                    f"response-{n['generations']}.md"),
                                    pending_inputs=len(n['host'].pending()) if n.get('host') else 0)
                               for n in nodes]}

    def wait(self, target, *, seconds=30, caller=None):
        """Wait without model polling, bounded by time and caller cancellation."""
        if target == caller:
            raise ValueError('An agent cannot wait on itself')
        with self._lock:
            if target not in self._agents:
                raise ValueError('Unknown agent')
            done = self._agents[target]['done']
            token = self._agents[caller]['handle'].cancellation if caller else self.cancellation
        until = min(self.deadline, time.monotonic() + max(0, min(float(seconds), 30)))
        while not done.is_set() and time.monotonic() < until and not token.is_cancelled():
            done.wait(min(0.1, max(0, until - time.monotonic())))
        return self.status(target)

    def close(self):
        """Stop task-owned work; the external process supervisor enforces hard exit."""
        with self._lock:
            self._closed = True
            hosts = [n['host'] for n in self._agents.values() if n.get('host')]
        self.cancellation.cancel('Task service closed')
        deadline = time.monotonic() + 2
        for host in hosts:
            try:
                host.close(timeout=max(0, deadline - time.monotonic()))
            except TimeoutError:
                pass
        for node in list(self._agents.values()):
            watcher = node.get('watcher')
            if watcher:
                watcher.join(timeout=max(0, deadline - time.monotonic()))
