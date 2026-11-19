"""Non-destructive provider views of completed native file reads in one task agent."""


def _file_batch(turns, index):
    calls = turns[index].get('tool_calls') or []
    if not calls or turns[index].get('role') != 'assistant':
        return []
    results = turns[index + 1:index + 1 + len(calls)]
    if len(results) != len(calls):
        return []
    batch = []
    for offset, (call, row) in enumerate(zip(calls, results), 1):
        args = call.get('arguments')
        call_id = call.get('id') or call.get('call_id')
        if (call.get('name') != 'file' or not isinstance(args, dict)
                or args.get('mode') != 'read' or not call_id
                or row.get('role') != 'tool' or row.get('tool_call_id') != call_id
                or row.get('is_error') or not row.get('meta', {}).get('id')
                or not row.get('context')
                or any(entry.get('type') != 'file' for entry in row['context'])):
            return []
        batch.append((index + offset, args.get('file'), row))
    return batch


class ReadCompaction:
    """Keep native call/result structure and archive indexes while folding result bodies."""

    def __init__(self):
        self.replacements = {}

    def project(self, turns):
        if not self.replacements:
            return turns
        view = []
        for row in turns:
            replacement = self.replacements.get(row.get('meta', {}).get('id'))
            if replacement is not None and row.get('role') == 'tool':
                row = dict(row, message=replacement, context=[])
            view.append(row)
        return view

    def compact(self, turns, path, note):
        if not isinstance(path, str) or not path or not isinstance(note, str) or not 1 <= len(note.strip()) <= 2000:
            raise ValueError('Supply the read file path in file and a 1–2000 character summary')
        for index in reversed(range(len(turns))):
            batch = _file_batch(turns, index)
            if not any(file == path for _, file, _ in batch):
                continue
            replacements = {}
            chars = 0
            sources = []
            for start, file, row in batch:
                key = row['meta']['id']
                if key in self.replacements:
                    raise ValueError('Latest matching batch is already compacted')
                marker = f'Archived file read {file}. Retrieve with agents history_read start={start} limit=1.'
                if not sources:
                    marker += ' Agent note (unverified): ' + note.strip()
                replacements[key] = marker
                chars += len(row['message']) - len(marker)
                sources.append({'file': file, 'history_index': start})
            if chars <= 0:
                raise ValueError('The note and references exceed the original read size')
            self.replacements.update(replacements)
            return {'sources': sources, 'result_characters_removed': chars}
        raise ValueError('No completed text-only file-read batch matches that path')

    def restore(self):
        count = len(self.replacements)
        self.replacements.clear()
        return {'restored_results': count}


def largest_tool_results(turns):
    """Rank visible textual results by character count, without logging their content."""
    names = {}
    sizes = []
    for turn in turns:
        for call in turn.get('tool_calls') or ():
            label = call.get('name', '?')
            args = call.get('arguments') or {}
            if label == 'file' and isinstance(args, dict):
                label = f"file {str(args.get('file', ''))[:160]}"
            names[call.get('id') or call.get('call_id')] = label
        meta = turn.get('meta') or {}
        if turn.get('role') == 'tool':
            label = names.get(turn.get('tool_call_id'), '?')
            size = len(turn.get('message') or '')
        elif meta.get('kind') == 'tool_result':
            label = meta.get('tool_name', '?')
            size = len(turn.get('message') or '')
            for entry in turn.get('context') or ():
                body = entry['context'].get().get('content')
                if isinstance(body, str):
                    size += len(body)
        else:
            continue
        sizes.append({'tool': label, 'chars': size})
    return sorted(sizes, key=lambda item: item['chars'], reverse=True)[:3]


def call_metrics(session, event):
    """Optional diagnostics for a task agent; omit unavailable provider measurements."""
    try:
        if event.type == 'provider_started':
            return {'context_size': session.get_params().get('context_size'),
                    'largest_results': largest_tool_results(session.get_context('chat').get())}
        if event.type == 'provider_finished' and event.data.get('status') in ('completed', 'incomplete'):
            usage = session.get_provider().get_usage() or {}
            return {key: usage[key] for key in ('turn_in', 'turn_cached')
                    if type(usage.get(key)) is int}
    except Exception:
        # A backend can omit usage or expose opaque context. Observing must not stop execution.
        pass
    return {}
