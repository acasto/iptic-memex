"""Normalize official/pseudo tools and execute actions once per request."""

from contextlib import contextmanager
from dataclasses import dataclass, field
import json
from uuid import uuid4

from base_classes import Completed, InteractionNeeded, Updates


TOOL_EVENT_TEXT_LIMIT = 1024


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict = field(default_factory=dict)
    content: str = ''
    official: bool = True
    invalid: bool = False


@contextmanager
def action_services(session, interactions):
    """Bridge existing action ask_*/emit calls to the input service in one place.

    This is an action protocol bridge, not a frontend: execution never inspects
    UI capabilities. New actions can use session.interactions directly.
    """
    previous_ui = getattr(session, 'ui', None)
    previous_service = getattr(session, 'interactions', None)
    session.ui = session.interactions = interactions
    try:
        yield
    finally:
        session.ui, session.interactions = previous_ui, previous_service


def collect_calls(session, provider, text):
    """Normalize provider calls or parsed pseudo blocks through the registry."""
    mode = session.get_effective_tool_mode()
    registry = session.get_action('assistant_commands')
    if mode == 'official':
        native = getattr(provider, 'get_tool_calls', lambda: [])() or []
        calls = []
        for call in native:
            args = call.get('arguments')
            valid_args = isinstance(args, dict)
            args = dict(args) if valid_args else {}
            content = args.pop('content', '') or ''
            calls.append(ToolCall(call.get('id') or call.get('call_id') or uuid4().hex,
                                  str(call.get('name') or '').lower(), args, content,
                                  invalid=bool(call.get('truncated') or not valid_args)))
        return calls, native, registry
    if mode == 'pseudo' and registry:
        text = registry._sanitize_think_sections(text)
        blocks = registry.extract_labeled_blocks(text)
        calls = []
        for parsed in registry.parse_commands(text):
            args, content = dict(parsed.get('args') or {}), parsed.get('content') or ''
            block = args.pop('block', None)
            if block in blocks:
                content = '\n'.join(part for part in (content, blocks[block]) if part)
            calls.append(ToolCall(uuid4().hex, parsed['command'].lower(), args, content, False))
        return calls, [], registry
    return [], [], registry


def drive_action(action, args, content, interactions):
    """Use one action instance; resume explicit continuations without rerunning it."""
    operation = lambda: action.run(args, content)
    while True:
        try:
            result = operation()
        except InteractionNeeded as need:
            if getattr(need, 'runtime_request_id', None):
                raise
            response = interactions.prompt(need)
            operation = lambda need=need, response=response: action.resume(need.state_token, response)
            continue
        if isinstance(result, Updates):
            for event in result.events:
                interactions.emit(event.get('type', 'status'), event)
            operation = lambda: action.resume('__implicit__', {'continue': True})
            continue
        return result


def take_contexts(session):
    """Take tool-created contexts once; attach them to the owning tool result."""
    action = session.get_action('process_contexts')
    contexts = list(action.get_contexts(session)) if action else []
    for kind in list(getattr(session, 'context', {})):
        if kind not in ('chat', 'prompt'):
            session.remove_context_type(kind)
    return contexts


def _tool_started_data(call):
    """Bound retained input metadata without changing executable tool inputs."""
    keys = {
        'cmd': ('desc', 'command', 'arguments'),
        'file': ('desc', 'mode', 'file', 'new_name'),
        'websearch': ('desc', 'query'),
        'ragsearch': ('desc', 'query'),
        'agents': ('operation', 'target', 'message'),
        'workspace': ('operation', 'title'),
    }.get(call.name, ('desc',))
    args = {}
    for key in keys:
        value = call.args.get(key)
        if isinstance(value, str):
            args[key] = value.strip()[:TOOL_EVENT_TEXT_LIMIT]
    data = {'tool_call_id': call.id, 'name': call.name, 'args': args}
    if call.name == 'cmd' and isinstance(call.content, str):
        data['content'] = call.content.strip()[:TOOL_EVENT_TEXT_LIMIT]
    return data


def run_call(session, call, registry, interactions, emit):
    """Return an explicit payload plus tool-owned contexts, including failures."""
    emit('tool_started', _tool_started_data(call))
    try:
        mapping = getattr(session, 'get_user_data', lambda key: {})('__tool_api_to_cmd__') or {}
        name = mapping.get(call.name, call.name)
        spec = registry.commands.get(name) if registry else None
        if call.invalid:
            raise ValueError('Tool arguments are missing, malformed, or truncated')
        if not spec:
            raise ValueError(f'Unsupported tool call: {call.name}')
        handler = spec.get('function') or {}
        args = dict(call.args)
        args.update(handler.get('fixed_args') or {})
        policy = session.get_agent_write_policy() if hasattr(session, 'get_agent_write_policy') else None
        if name == 'file' and policy == 'deny' and args.get('mode') not in ('read', 'summarize'):
            raise PermissionError('File changes are disabled by write policy')
        if name == 'cmd' and policy in ('deny', 'dry-run') and handler.get('name') != 'assistant_docker_tool':
            raise PermissionError('Read-only command execution requires the Docker cmd tool')
        if handler.get('type') == 'method':
            method = getattr(registry, handler['name'])
            result = method(args, call.content)
        else:
            action = session.get_action(handler['name'])
            if action is None:
                raise ValueError(f"Tool action unavailable: {handler['name']}")
            result = drive_action(action, args, call.content, interactions)
        payload = result.payload if isinstance(result, Completed) else result
        if payload is None:
            payload = {'ok': True}
        if not isinstance(payload, dict):
            payload = {'ok': True, 'result': payload}
    except (InteractionNeeded, KeyboardInterrupt, InterruptedError) as exc:
        emit('tool_finished', {'tool_call_id': call.id, 'name': call.name,
                              'status': 'needs_input' if isinstance(exc, InteractionNeeded) else 'cancelled'})
        raise
    except Exception as exc:
        payload = {'ok': False, 'error': str(exc) or type(exc).__name__}
    contexts = take_contexts(session)
    # Some legacy helpers report errors as named contexts rather than raising.
    errors = [ctx['context'].get().get('content') for ctx in contexts
              if ctx['context'].get().get('name', '').endswith('_error')]
    if errors:
        payload = dict(payload, ok=False, error='\n'.join(str(error) for error in errors))
    emit('tool_finished', {'tool_call_id': call.id, 'name': call.name,
                          'status': 'failed' if payload.get('ok') is False else 'completed',
                          'result': payload})
    return payload, contexts


def record_result(chat, call, payload, contexts=()):
    """Complete every official call ID, even when its execution was skipped."""
    parts = [json.dumps(payload, ensure_ascii=False, default=str)]
    for context in contexts:
        if context.get('type') == 'image':
            continue
        data = context['context'].get()
        if isinstance(data.get('content'), str) and data['content']:
            parts.append(data['content'])
    if call.official:
        chat.add('\n\n'.join(parts), 'tool', list(contexts),
                 extra={'tool_call_id': call.id, 'is_error': payload.get('ok') is False})
    else:
        # Pseudo tools have no native function-call item. Results are assistant
        # context, not a synthetic user instruction or a forged native tool ID.
        chat.add(parts[0], 'assistant', list(contexts),
                 extra={'meta': {'kind': 'tool_result', 'tool_name': call.name}})
