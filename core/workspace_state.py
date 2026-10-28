"""File-backed workspace records, independent of model sessions and interfaces."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import uuid


TERMINAL_STATES = {'completed', 'partial', 'failed', 'cancelled', 'timed_out', 'incomplete',
                   'interrupted'}
_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}')


def now() -> str:
    """Return a sortable UTC timestamp for saved records."""
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def atomic_write(path: Path, text: str) -> None:
    """Replace a complete record without exposing partial writes to readers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path: Path) -> dict:
    """Read a record; absent records are normal while a worker starts."""
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}


def write_json(path: Path, record: dict) -> None:
    """Atomically persist a JSON object."""
    atomic_write(path, json.dumps(record, ensure_ascii=False, indent=2) + '\n')


def identifier(value: str) -> str:
    """Validate an opaque record name before using it in a filesystem path."""
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError('Invalid workspace record identifier')
    return value


class WorkspaceStore:
    """Persistent notes and per-run records; each worker owns its status and log."""

    def __init__(self, directory: str | Path):
        self.root = Path(directory).expanduser().resolve()

    def run_dir(self, run_id: str) -> Path:
        path = self.root / 'runs' / identifier(run_id)
        if not path.resolve().is_relative_to(self.root):
            raise ValueError('Run directory escapes workspace')
        return path

    def create_run(self, question: str, settings: dict) -> dict:
        """Create a unique run without replacing any previous work."""
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f-') + uuid.uuid4().hex[:12]
        directory = self.run_dir(run_id)
        directory.mkdir(parents=True, exist_ok=False)
        (self.root / 'notes').mkdir(exist_ok=True)
        record = dict(settings, record_version=2, id=run_id, question=question, created_at=now(),
                      updated_at=now(), status='running', phase='workers', pid=os.getpid())
        write_json(directory / 'run.json', record)
        atomic_write(directory / 'question.md', question + '\n')
        return record

    def update_run(self, run_id: str, **values) -> dict:
        path = self.run_dir(run_id) / 'run.json'
        record = read_json(path)
        record.update(values, updated_at=now())
        write_json(path, record)
        return record

    def update_worker(self, run_id: str, worker: str, **values) -> dict:
        """Write a worker snapshot (one live writer per worker)."""
        path = self.run_dir(run_id) / identifier(worker) / 'status.json'
        record = read_json(path)
        record.update(values, worker=worker, updated_at=now())
        write_json(path, record)
        return record

    def event(self, run_id: str, worker: str, event: str, data: dict) -> None:
        """Append observable activity to a worker's single-writer event log."""
        directory = self.run_dir(run_id) / identifier(worker)
        directory.mkdir(exist_ok=True)
        payload = dict(data, event=event, worker=worker, time=now())
        with (directory / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + '\n')

    def publish(self, run_id: str, worker: str, title: str, content: str,
                references: list[str] | None = None, *, kind: str = 'finding') -> dict:
        """Publish an immutable Markdown contribution with validated dependencies."""
        if not title.strip() or not content.strip():
            raise ValueError('A contribution needs a title and content')
        if len(title) > 200 or len(content) > 12000:
            raise ValueError('Contribution limit: 200 title characters and 12000 content characters '
                             f'(received {len(title)} and {len(content)})')
        if kind not in ('finding', 'summary'):
            raise ValueError('Contribution kind must be finding or summary')
        references = list(dict.fromkeys(references or []))
        for reference in references:
            self.read_note(reference)
        references = list(dict.fromkeys(references + self.citations(content)))
        note_id = 'note-' + uuid.uuid4().hex
        record = dict(id=note_id, run_id=identifier(run_id), worker=identifier(worker),
                      title=title.strip(), created_at=now(), references=references, kind=kind)
        # Metadata is the publication marker: readers only discover a complete note.
        atomic_write(self.root / 'notes' / f'{note_id}.md', content.strip() + '\n')
        write_json(self.root / 'notes' / f'{note_id}.json', record)
        return record

    def citations(self, content: str) -> list[str]:
        """Find valid, explicit note IDs in prose; exposure alone is not a citation."""
        found = []
        for note_id in dict.fromkeys(re.findall(r'\bnote-[0-9a-f]{32}\b', content)):
            try:
                self.read_note(note_id)
            except (ValueError, OSError):
                continue
            found.append(note_id)
        return found

    def read_note(self, note_id: str) -> dict:
        """Read a published note, rejecting missing records and escaping symlinks."""
        stem = self.root / 'notes' / identifier(note_id)
        metadata, content = stem.with_suffix('.json'), stem.with_suffix('.md')
        for path in (metadata, content):
            if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                raise ValueError('Contribution path escapes workspace')
        record = read_json(metadata)
        if not record:
            raise ValueError(f'Unknown contribution: {note_id}')
        return dict(record, content=content.read_text(encoding='utf-8')[:12000])

    def notes(self, query: str = '', *, run_id: str | None = None,
              limit: int = 8) -> list[dict]:
        """Find notes by keyword relevance and recency, with a bounded result count."""
        terms = set(re.findall(r'\w+', query.lower()))
        found = []
        for path in (self.root / 'notes').glob('note-*.json'):
            note = self.read_note(path.stem)
            if run_id and note['run_id'] != run_id:
                continue
            words = set(re.findall(r'\w+', (note['title'] + ' ' + note['content']).lower()))
            score = len(terms & words)
            if terms and not score:
                continue
            found.append((score, note['created_at'], note['id'], note))
        found.sort(key=lambda item: item[:3], reverse=True)
        return [item[3] for item in found[:max(0, limit)]]

    def snapshot(self, run_id: str | None = None) -> dict:
        """Read a specified run or the newest run without contacting a model."""
        if run_id is None:
            runs = sorted((self.root / 'runs').glob('*/run.json'), reverse=True)
            if not runs:
                raise ValueError('No workspace runs found')
            run_id = runs[0].parent.name
        directory = self.run_dir(run_id)
        record = read_json(directory / 'run.json')
        if not record:
            raise ValueError(f'Unknown run: {run_id}')
        workers = [read_json(path) for path in sorted(directory.glob('*/status.json'))]
        if record.get('status') not in TERMINAL_STATES and record.get('pid'):
            try:
                os.kill(record['pid'], 0)
            except ProcessLookupError:
                record = dict(record, saved_status=record['status'], status='interrupted')
            except PermissionError:
                pass
        return dict(record, workers=workers, directory=str(directory))

    def read_notes(self, run_id: str) -> list[dict]:
        """Return published notes explicitly read by this run's contributors.

        Preview delivery and search hits alone do not select evidence. Ignore an
        unfinished event line or a note removed after it was read.
        """
        note_ids = {}
        for path in sorted(self.run_dir(run_id).glob('worker-*/events.jsonl')):
            for line in path.read_text(encoding='utf-8').splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get('event') == 'note_read' and event.get('note'):
                    note_ids[event['note']] = None
        notes = []
        for note_id in note_ids:
            try:
                notes.append(self.read_note(note_id))
            except (ValueError, OSError):
                continue
        return notes
