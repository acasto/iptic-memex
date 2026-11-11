"""Read-only incremental task activity, without model sessions or terminal ownership."""
from collections import deque
from datetime import datetime, timezone
import json

from workspace.state import read_json
from workspace.tasks import TaskStore


def text(value, limit=240):
    """Keep log values on one line and prevent terminal control sequences."""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    value = ''.join(char if char.isprintable() else ' ' for char in value)
    return value if len(value) <= limit else value[:limit] + ' … [shortened]'


def event_line(event, verbose=False):
    """Render execution evidence; omit noisy context/usage bookkeeping."""
    kind = event.get('event')
    limit = 4000 if verbose else 240
    name = event.get('name') or event.get('tool') or '?'
    if kind in ('tool_started', 'tool_start'):
        args = event.get('args') or {}
        detail = ' '.join(f'{key}={text(value, limit)}' for key, value in args.items())
        if event.get('content'):
            detail += ' content=' + text(event['content'], limit)
        body = f'Tool {name}: {detail or "arguments not recorded"}'
    elif kind in ('tool_finished', 'tool_end'):
        body = f'Tool {name}: {event.get("status", "finished")}'
        if event.get('result') is not None:
            body += ' — ' + text(event['result'], limit)
    elif kind == 'assistant_message' and event.get('text'):
        body = 'Assistant: ' + text(event['text'], limit)
    elif kind == 'provider_started':
        body = 'Calling model'
    elif kind == 'run_started':
        body = 'Started execution'
    elif kind == 'run_finished':
        body = 'Execution ' + str(event.get('status', 'finished'))
    elif kind == 'interaction_requested':
        body = 'Waiting for input: ' + text((event.get('spec') or {}).get('prompt', ''), limit)
    elif kind in ('error', 'warning'):
        body = kind.capitalize() + ': ' + text(event.get('message', event), limit)
    elif kind == 'published':
        body = 'Published: ' + text(event.get('title', event.get('note', event)), limit)
    else:
        return None
    return f'{text(event.get("time", ""), 32)}  {text(event.get("worker", "?"), 100)}  {body}'


class TaskWatch:
    """Read new complete JSONL records; never mutate task records or acquire ownership."""

    def __init__(self, store, task_id, *, tail=30, verbose=False):
        self.store, self.tasks, self.task_id = store, TaskStore(store), task_id
        self.tail, self.verbose = tail, verbose
        self.run_id = None
        self.offsets = {}
        self.assignments = set()
        self.summary = None
        self.first = True

    def _events(self, directory, initial):
        rows = deque(maxlen=self.tail) if initial else []
        for path in sorted(directory.glob('*/events.jsonl')):
            if not path.resolve().is_relative_to(self.store.root):
                raise ValueError('Event log escapes workspace')
            offset = self.offsets.get(path, 0)
            if path.stat().st_size < offset:
                offset = 0
            with path.open('rb') as stream:
                stream.seek(offset)
                pending = deque(maxlen=self.tail) if initial else []
                while True:
                    start = stream.tell()
                    line = stream.readline()
                    if not line or not line.endswith(b'\n'):
                        self.offsets[path] = start
                        break
                    self.offsets[path] = stream.tell()
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        pending.append(('', f'{path.parent.name}  [unreadable event record]'))
                        continue
                    if not isinstance(event, dict):
                        continue
                    rendered = event_line(event, self.verbose)
                    if rendered:
                        pending.append((str(event.get('time', '')), rendered))
                # Retain the newest events across agents, not merely the last file.
                combined = sorted([*rows, *pending], key=lambda row: row[0])
                if initial:
                    rows = deque(combined[-self.tail:] if self.tail else [], maxlen=self.tail)
                else:
                    rows = combined
        return [row[1] for row in rows]

    def poll(self, *, heartbeat=False):
        """Return new lines and whether the observed task has stopped."""
        task = self.tasks.inspect(self.task_id)
        lines = []
        if self.first:
            lines.extend([f'Task: {self.task_id}', 'Brief: ' + text(task['brief'], 4000),
                          f'Records: {self.store.root}',
                          'Read-only watch; Ctrl-C exits the watcher, leaving the task running.'])
            self.first = False
        if not task['attempts']:
            summary = f'Task {task["status"]}; no attempt yet'
            if summary != self.summary or heartbeat:
                lines.append(summary)
            self.summary = summary
            return lines, task['status'] != 'pending'
        attempt = task['attempts'][-1]
        initial = self.run_id != attempt['run_id']
        if initial:
            self.run_id = attempt['run_id']
            self.offsets.clear()
            self.assignments.clear()
            lines.append(f'Attempt: {self.run_id}')
        directory = self.store.run_dir(self.run_id)
        state = read_json(directory / 'task-state.json')
        for agent_id, node in state.get('agents', {}).items():
            generation = node.get('generations', 1)
            key = (agent_id, generation)
            path = directory / agent_id / f'input-{generation}.md'
            if not path.resolve().is_relative_to(directory):
                raise ValueError('Agent input escapes attempt')
            if key not in self.assignments and path.exists():
                lines.append(f'{text(agent_id)}  Assignment: ' +
                             text(path.read_text(encoding='utf-8'), 4000 if self.verbose else 240))
                self.assignments.add(key)
        lines.extend(self._events(directory, initial))
        agents = state.get('agents', {})
        phases = []
        for agent_id, node in agents.items():
            status = read_json(directory / agent_id / 'status.json')
            phase = status.get('phase', '')
            phases.append(f'{agent_id}={node.get("status", "?")}/{phase}')
        start = attempt.get('started_at')
        end = attempt.get('finished_at')
        elapsed = ''
        if start:
            finish = datetime.fromisoformat(end) if end else datetime.now(timezone.utc)
            seconds = max(0, int((finish - datetime.fromisoformat(start)).total_seconds()))
            elapsed = f' · {seconds}s elapsed'
        summary = (f'Task {task["status"]} · calls {state.get("calls_used", "?")}/'
                   f'{state.get("calls_limit", "?")} · ' + ', '.join(phases))
        if initial or summary != self.summary or heartbeat:
            lines.append(text(summary, 4000) + elapsed)
            self.summary = summary
        if task['status'] == 'needs_input' and (initial or lines):
            request = read_json(directory / 'input-needed.json')
            lines.append('Input needed: ' + text(request.get('question', ''), 4000))
        done = task['status'] not in ('pending', 'running')
        if done and (initial or lines):
            result = read_json(directory / 'task-result.json')
            if result.get('answer'):
                lines.append('Result: ' + text(result['answer'], 4000 if self.verbose else 240))
            if task.get('error') or result.get('error'):
                lines.append('Notice: ' + text(task.get('error') or result['error']))
        return lines, done
