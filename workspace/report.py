"""Read-only metrics and provenance inspection for saved workspace runs."""

from __future__ import annotations

from datetime import datetime
import json

from workspace.state import WorkspaceStore


def seconds_between(start: str | None, end: str | None) -> float | None:
    """Measure saved wall time when both timestamps are available."""
    if not start or not end:
        return None
    return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 3)


def run_report(store: WorkspaceStore, run_id: str | None = None) -> dict:
    """Report observations and explicit citations without claiming they prove useful collaboration."""
    run = store.snapshot(run_id)
    notes = store.notes(run_id=run['id'], limit=10000)
    final_ids = {w.get('note_id') for w in run['workers']}
    peers = set()
    for note in notes:
        for reference in set(note['references'] + store.citations(note['content'])):
            target = store.read_note(reference)
            if target['worker'] != note['worker'] and target['run_id'] == note['run_id']:
                peers.add((note['id'], reference))
    workers = []
    for worker in run['workers']:
        events = []
        path = store.run_dir(run['id']) / worker['worker'] / 'events.jsonl'
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # A concurrent reader may encounter an unfinished last line.
        tools = {}
        delivered, retrieved, searched = set(), set(), set()
        publications = [n['created_at'] for n in notes if n['worker'] == worker['worker']]
        for event in events:
            if event['event'] == 'tool_start':
                tools[event['tool']] = tools.get(event['tool'], 0) + 1
            elif event['event'] == 'notes_seen':
                delivered.update(event['notes'])
            elif event['event'] == 'note_read':
                retrieved.add(event['note'])
            elif event['event'] == 'notes_searched':
                searched.update(event['notes'])
            elif event['event'] == 'published':
                publications.append(event['time'])
        workers.append(dict(
            worker=worker['worker'], model=worker.get('model'), status=worker.get('status'),
            model_name=worker.get('model_name'), max_tokens=worker.get('max_tokens'),
            generation_settings=worker.get('generation_settings'),
            turns=worker.get('turns'), tool_calls=tools, notes_delivered=len(delivered),
            notes_read=len(retrieved) if run.get('record_version', 1) >= 2 else None,
            notes_in_search_results=len(searched) if run.get('record_version', 1) >= 2 else None,
            first_publication_seconds=seconds_between(worker.get('started_at'),
                                                      min(publications) if publications else None),
            wall_seconds=seconds_between(worker.get('started_at'), worker.get('finished_at')),
            usage=worker.get('usage'), prompt_sha256=worker.get('prompt_sha256'),
            workspace_errors=[{k: e[k] for k in (
                'time', 'operation', 'error', 'title_chars', 'content_chars') if k in e}
                              for e in events if e.get('event') == 'workspace_error'],
        ))
    usage = {}
    for key in ('total_in', 'total_out', 'total_cached', 'total_reasoning'):
        values = [(w.get('usage') or {}).get(key) for w in workers]
        usage[key] = sum(values) if values and all(v is not None for v in values) else None
    return dict(
        run_id=run['id'], status=run['status'], question=run['question'],
        source_run=run.get('source_run'),
        wall_seconds=seconds_between(run.get('created_at'), run.get('finished_at')),
        usage=usage, workers=workers, notes=len(notes),
        findings=sum(n.get('kind', 'summary' if n['id'] in final_ids else 'finding') == 'finding'
                     for n in notes),
        peer_citation_edges=len(peers),
        synthesis_input_sha256=run.get('synthesis_input_sha256'),
        synthesis_notes_included=run.get('synthesis_notes_included'),
        synthesis_notes_deduplicated=run.get('synthesis_notes_deduplicated'),
        synthesis_notes_current=run.get('synthesis_notes_current'),
        synthesis_notes_historical=run.get('synthesis_notes_historical'),
    )
