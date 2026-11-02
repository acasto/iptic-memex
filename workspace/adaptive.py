"""Foreground adaptive research with one continuing lead and one optional branch."""

import hashlib
import json
from pathlib import Path
import shutil
import signal
import threading
import time

from workspace.engine import WorkspaceEngine
from workspace.state import TERMINAL_STATES, atomic_write, now, read_json, write_json


class AdaptiveEngine(WorkspaceEngine):
    """Keep research decisions in the lead; supervise its whole process group."""

    def __init__(self, *args, total_calls=32, strategy='adaptive', contexts=(),
                 lead_prompt='workspace/research', **kwargs):
        super().__init__(*args, **kwargs)
        if strategy not in ('adaptive', 'single'):
            raise ValueError('Research strategy must be adaptive or single')
        if total_calls < 3:
            raise ValueError('Research needs at least 3 total calls, including synthesis')
        self.total_calls, self.strategy = total_calls, strategy
        self.contexts = tuple(contexts)
        self.lead_prompt = lead_prompt

    def build_synthesis_input(self, run_id, question):
        evidence = super().build_synthesis_input(run_id, question)
        research = read_json(self.store.run_dir(run_id) / 'research.json')
        evidence['research'] = research
        evidence['message'] += (
            '\n\nResearch goal, questions, coverage assessment and budget (unverified records):\n' +
            json.dumps(research, ensure_ascii=False) +
            '\nA sufficient assessment is the lead\'s judgment, not independent verification. '
            'Describe consequential unresolved questions or missing assessment explicitly.')
        evidence['sha256'] = hashlib.sha256(evidence['message'].encode()).hexdigest()
        return evidence

    def reconcile(self, run_id):
        """After lead exit, charge unreconciled branch reservations conservatively."""
        path = self.store.run_dir(run_id) / 'research.json'
        research = read_json(path)
        for branch in research['branches']:
            if branch['status'] != 'running':
                continue
            saved = read_json(self.store.run_dir(run_id) / branch['worker'] / 'status.json')
            calls = saved.get('workflow_calls', saved.get('turns'))
            charged = branch['allowance'] if calls is None else min(branch['allowance'], calls)
            research['budget']['used'] += charged
            research['budget']['reserved'] -= branch['allowance']
            saved_status = saved.get('status')
            branch.update(status=saved_status if saved_status in TERMINAL_STATES else 'interrupted',
                          calls=charged,
                          accounting='reserved_ceiling' if calls is None else 'observed')
        write_json(path, research)
        return research

    def run(self, question, *, on_started=None):
        settings = self.settings
        settings.validate()
        if not question.strip():
            raise ValueError('Question must not be empty')
        for _, source in self.contexts:
            if not Path(source).expanduser().is_file():
                raise ValueError(f'Attachment does not exist: {source}')
        if any(process.poll() is None for process, _ in self._children.values()):
            raise ValueError('This engine already has an active run')
        self._children.clear()
        record = self.store.create_run(question, dict(
            vars(settings), kind='research', strategy=self.strategy, total_calls=self.total_calls))
        run_id = record['id']
        path = self.store.run_dir(run_id)
        contexts, manifest = [], []
        for index, (kind, source) in enumerate(self.contexts):
            source = Path(source).expanduser().resolve()
            target = path / 'inputs' / f'{index}-{source.name}'
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, target)
            contexts.append((kind, str(target)))
            manifest.append(dict(kind=kind, source=str(source), snapshot=str(target),
                                 sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
        write_json(path / 'inputs.json', {'attachments': manifest})
        state = dict(version=1, strategy=self.strategy,
                     brief=dict(original=question, version=0, interpretations=[]),
                     questions={}, branches=[], decisions=[], activity=None, assessment=None,
                     budget=dict(total_calls=self.total_calls, synthesis_reserved=1,
                                 used=0, reserved=0, lead_calls=0, synthesis_calls=0))
        write_json(path / 'research.json', state)
        atomic_write(path / 'brief.md', question + '\n')
        deadline = time.monotonic() + settings.run_timeout_seconds
        collection = deadline - settings.synthesis_timeout_seconds
        status, error, answer = 'failed', None, ''
        previous = None

        def interrupt(_signal, _frame):
            raise KeyboardInterrupt()

        try:
            if threading.current_thread() is threading.main_thread():
                previous = signal.signal(signal.SIGTERM, interrupt)
            if on_started:
                on_started(record)
            self._spawn(run_id, 'lead', question, deadline=collection, extra={
                'research': {'strategy': self.strategy}, 'steps': self.total_calls - 1,
                'prompt': self.lead_prompt, 'contexts': contexts,
                'branch_command': self.worker_command,
                'message': ('User brief:\n' + question + '\n\nRepository: ' + settings.repository +
                            '\nUse research status for the shared goal, questions and budget. '
                            'Use workspace notes for evidence. Strategy: ' + self.strategy),
            })
            self._wait(run_id, ['lead'], collection)
            self._stop(['lead'])
            for worker in self.store.snapshot(run_id)['workers']:
                if worker.get('status') not in TERMINAL_STATES:
                    self.store.update_worker(run_id, worker['worker'], status='interrupted',
                                             phase='finished', error='Lead exited before branch collection')
            state = self.reconcile(run_id)
            if self._shared_notes(run_id):
                evidence = self._synthesis_input(run_id, question)
                if not evidence['notes_included']:
                    raise ValueError('No contributions fit within synthesis_max_chars')
                state['budget']['synthesis_calls'] = 1
                write_json(path / 'research.json', state)
                self.store.update_run(run_id, phase='synthesis')
                self._spawn(run_id, 'synthesis', question, phase='synthesis',
                            message=evidence['message'])
                self._wait(run_id, ['synthesis'], deadline)
                result = read_json(path / 'synthesis' / 'status.json')
                answer = result.get('last_text') or ''
                if result.get('status') == 'completed' and answer.strip():
                    assessed = (state.get('assessment') or {}).get('status') == 'sufficient'
                    outcomes = evidence['outcomes']
                    status = ('completed' if assessed and all(w['status'] == 'completed'
                                                              for w in outcomes) else 'partial')
                    if status == 'partial':
                        answer = ('> Partial research result: coverage is incomplete or an '
                                  'investigation reached a limit.\n\n' + answer)
                else:
                    error = result.get('error') or 'Synthesis did not complete'
            else:
                error = 'Research produced no shared findings'
            if status == 'failed':
                findings = self._shared_notes(run_id)
                answer = '# Incomplete research\n\n' + (error or 'Research incomplete') + '\n\n'
                answer += '\n\n'.join(f"## {n['title']} ({n['id']})\n{n['content']}" for n in findings[:8])
                if findings:
                    status = 'partial'
        except KeyboardInterrupt:
            status, error = 'cancelled', 'Research interrupted'
            answer = '# Cancelled research\n\nPublished findings remain available.\n'
        except Exception as exc:
            error = str(exc)
            answer = '# Failed research\n\n' + error + '\n'
        finally:
            self._stop(list(self._children))
            for _, log in self._children.values():
                log.close()
            for worker in self.store.snapshot(run_id)['workers']:
                if worker.get('status') not in TERMINAL_STATES:
                    self.store.update_worker(run_id, worker['worker'], status='cancelled',
                                             phase='finished', error=error)
            self.reconcile(run_id)
            atomic_write(path / 'answer.md', answer + '\n')
            self.store.update_run(run_id, status=status, phase='finished', error=error,
                                  finished_at=now())
            if previous is not None:
                signal.signal(signal.SIGTERM, previous)
        return dict(self.store.snapshot(run_id), answer=answer)
