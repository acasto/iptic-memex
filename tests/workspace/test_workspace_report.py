from __future__ import annotations

import json

from workspace.report import run_report
from workspace.state import WorkspaceStore, now


def test_report_measures_activity_without_mutating_artifacts(tmp_path):
    store = WorkspaceStore(tmp_path)
    run = store.create_run('Question', {})
    store.update_worker(run['id'], 'worker-1', status='completed', started_at=now(),
                        usage={'total_in': 100, 'total_out': 20, 'total_cached': 80})
    one = store.publish(run['id'], 'worker-1', 'First evidence', 'A fact')
    store.publish(run['id'], 'worker-2', 'Peer citation', f"Consider {one['id']}")
    store.event(run['id'], 'worker-1', 'notes_seen', {'notes': [one['id'], one['id']]})
    store.event(run['id'], 'worker-1', 'note_read', {'note': one['id']})
    store.event(run['id'], 'worker-1', 'notes_searched', {'notes': [one['id']]})
    store.event(run['id'], 'worker-1', 'tool_start', {'tool': 'workspace'})
    path = store.run_dir(run['id']) / 'worker-1' / 'events.jsonl'
    with path.open('a') as stream:
        stream.write('{')  # Concurrent read may encounter an unfinished final line.
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    report = run_report(store, run['id'])
    worker = report['workers'][0]
    assert report['peer_citation_edges'] == 1
    assert report['usage']['total_in'] == 100
    assert report['usage']['total_cached'] == 80
    assert report['usage']['total_reasoning'] is None
    assert worker['notes_delivered'] == worker['notes_read'] == worker['notes_in_search_results'] == 1
    assert worker['first_publication_seconds'] is not None
    assert worker['tool_calls'] == {'workspace': 1}
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


def test_legacy_reads_are_unknown_and_summaries_are_not_independent_findings(tmp_path):
    store = WorkspaceStore(tmp_path)
    run = store.create_run('Question', {})
    note = store.publish(run['id'], 'worker-1', 'Legacy final', 'An answer')
    path = store.root / 'notes' / (note['id'] + '.json')
    metadata = json.loads(path.read_text())
    metadata.pop('kind')
    path.write_text(json.dumps(metadata))
    store.update_run(run['id'], record_version=1)
    store.update_worker(run['id'], 'worker-1', note_id=note['id'])
    report = run_report(store, run['id'])
    assert report['findings'] == 0
    assert report['workers'][0]['notes_read'] is None
    assert report['usage']['total_in'] is None
