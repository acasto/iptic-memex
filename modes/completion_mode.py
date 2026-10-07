from base_classes import InteractionMode
from core.mode_runner import run_completion


class CompletionMode(InteractionMode):
    """Run a one-shot completion through the shared turn pipeline."""

    def __init__(self, session, message: str | None = None):
        self.session = session
        self.message = message
        try:
            session.set_option('tool_mode', 'none')
        except Exception:
            pass
        session.set_flag('completion_mode', True)
        action = session.get_action('process_contexts')
        contexts = action.get_contexts(session) if action else []
        has_stdin = any(ctx['context'].get().get('name') == 'stdin' for ctx in contexts)
        if has_stdin and message is None and 'prompt' not in session.config.overrides:
            session.remove_context_type('prompt')
        if message is None and not contexts:
            self.message = 'Please process the provided content.'

    def start(self, *, emit_output: bool = True):
        """Return an explicit outcome; optionally render the completion for CLI."""
        params = self.session.get_params()
        raw = bool(params.get('raw_completion'))
        overrides = self.session.config.overrides or {}
        stream = bool(overrides.get('stream', False)) and not raw
        result = run_completion(
            session=self.session,
            message=self.message or '',
            stream=stream,
            stdin_as_message=self.message is None,
            capture='raw' if raw else 'text',
        )
        if emit_output and not stream:
            text = result.raw if raw else result.last_text
            if text is not None:
                self.session.utils.output.write(text)
        return result
