from base_classes import InteractionMode
from core.mode_runner import run_completion
from app.completion import configure_completion
from ui.completion_renderer import CompletionRenderer
from app.session_output import EventOutput


class CompletionMode(InteractionMode):
    """CLI completion adapter over the event-based execution runtime."""

    def __init__(self, session, message: str | None = None):
        self.session = session
        self.message = message
        self.output = session.utils.output
        configure_completion(session)
        action = session.get_action('process_contexts')
        contexts = action.get_contexts(session) if action else []
        if message is None and not contexts:
            self.message = 'Please process the provided content.'

    def start(self, *, emit_output: bool = True, on_event=None, cancellation=None, interactions=None):
        """Return an explicit outcome; optionally render the completion for CLI."""
        params = self.session.get_params()
        raw = bool(params.get('raw_completion'))
        overrides = self.session.config.overrides or {}
        stream = bool(overrides.get('stream', False)) and not raw
        if emit_output and isinstance(self.output, EventOutput):
            from utils.output_utils import OutputHandler
            self.output = OutputHandler(self.session.config)
        if on_event is None and emit_output:
            on_event = CompletionRenderer(self.output.write,
                                          getattr(self.output, 'warning', lambda message: None))
        result = run_completion(
            session=self.session,
            message=self.message or '',
            stream=stream,
            stdin_as_message=self.message is None,
            capture='raw' if raw else 'text',
            on_event=on_event,
            cancellation=cancellation,
            interactions=interactions,
        )
        if emit_output and not stream:
            text = result.raw if raw else result.last_text
            if text is not None:
                self.output.write(text)
        return result
