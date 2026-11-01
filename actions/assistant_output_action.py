"""Legacy rendering adapter; response filtering belongs to core.runtime."""

from base_classes import InteractionAction
from core.runtime.response import ResponseProcessor


class AssistantOutputAction(InteractionAction):
    """Render legacy chat/agent streams using the shared response processor.

    Completion consumes the processor directly and emits events instead. This
    adapter can disappear when the remaining frontend paths migrate.
    """

    def __init__(self, session):
        self.session = session
        self.output = session.utils.output
        self.processor = None

    def _diagnostic(self, kind, data):
        if kind == 'warning':
            self.output.warning(data['message'])

    def _on_token(self, text):
        visible = self.processor.feed(text)
        if visible:
            self.output.write(visible, end='', flush=True)
        return text

    def _on_complete(self, raw):
        tail = self.processor.finish()
        if tail:
            self.output.write(tail, end='', flush=True)
        self.output.write('', flush=True)
        return raw

    def run(self, stream, spinner_message='Processing response...'):
        self.processor = ResponseProcessor(self.session, self._diagnostic)
        token = self.session.get_cancellation_token() if hasattr(self.session, 'get_cancellation_token') else None
        sink_cancel = getattr(self.output, 'cancel_check', None)

        def cancelled():
            return bool((token and token.is_cancelled()) or (sink_cancel and sink_cancel()))

        def cancel():
            self.session.set_flag('turn_cancelled', True)
            if token:
                token.cancel('user')

        return self.session.utils.stream.process_stream(
            stream, on_token=self._on_token, on_complete=self._on_complete,
            spinner_message=spinner_message, cancel_check=cancelled, on_cancel=cancel,
        )

    def get_raw_output(self):
        return self.processor.raw if self.processor else ''

    def get_display_output(self):
        return self.processor.display if self.processor else ''

    def get_sanitized_output(self):
        return self.processor.sanitized if self.processor else ''

    def get_hidden_output(self):
        parts = []
        if self.processor:
            for processor in self.processor.display_filters + self.processor.return_filters:
                if hasattr(processor, 'get_hidden'):
                    parts.append(processor.get_hidden() or '')
                if hasattr(processor, 'clear_hidden'):
                    processor.clear_hidden()
        return ''.join(parts)

    @classmethod
    def filter_full_text(cls, text, session):
        processor = ResponseProcessor(session)
        processor.feed(text)
        processor.finish()
        return processor.display

    @classmethod
    def filter_full_text_for_return(cls, text, session):
        processor = ResponseProcessor(session)
        processor.feed(text)
        processor.finish()
        return processor.sanitized
