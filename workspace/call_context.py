"""Model-visible call accounting and optional strategy prompts; no sidecar execution."""
import json

from core.prompt_resolver import PromptResolver


def add_call_context(session, metadata, strategy):
    """Publish facts and render configured prompts through existing template handlers."""
    session.set_user_data('__turn_meta__', {
        **(session.get_user_data('__turn_meta__', {}) or {}),
        **{f'workspace_{key}': value for key, value in metadata.items()},
    })
    session.add_context('agent', {
        'name': 'workspace_call_status',
        'content': ('Workspace call status (a shared-budget snapshot; no future calls reserved):\n' +
                    json.dumps(metadata, sort_keys=True)),
    })
    if strategy is None:
        return
    sources = [strategy.call_prompt]
    if metadata['calls_remaining_after_current'] < strategy.final_call_threshold:
        sources.append(strategy.final_call_prompt)
    for source in sources:
        if not source:
            continue
        text = PromptResolver(session.config).resolve(source)
        handlers = session.get_option('DEFAULT', 'template_handler', fallback='none') or 'none'
        for name in str(handlers).split(','):
            name = name.strip()
            if name.lower() in ('none', 'false', ''):
                continue
            action = session.get_action('prompt_template' if name == 'default' else name)
            if action:
                text = action.run(text)
        if text:
            session.add_context('agent', {'name': 'workspace_call_prompt', 'content': text})
