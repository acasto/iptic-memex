"""Incremental response filtering without rendering or stream consumption."""


class ResponseProcessor:
    """Separate presentation and parser content using existing filters."""

    def __init__(self, session, emit=None):
        self.emit = emit or (lambda kind, data: None)
        self.raw_parts = []
        self.display_parts = []
        self.return_parts = []
        self.display_filters = []
        self.return_filters = []
        params = session.get_params() or {}
        names = params.get('output_filters') or []
        if isinstance(names, str):
            names = names.split(',')
        options = {key: params.get(key, '') for key in ('think_placeholder', 'tool_placeholder')}
        for name in names:
            name = str(name).strip()
            if not name:
                continue
            display = session.get_action(f'output_filter_{name}')
            if display is None or not callable(getattr(display, 'process_token', None)):
                self.emit('warning', {'message': f'Output filter not found: {name}'})
                continue
            self._configure(display, options)
            self.display_filters.append(display)
            if getattr(display, 'AFFECTS_RETURN', False):
                parser = session.get_action(f'output_filter_{name}')
                self._configure(parser, options)
                self.return_filters.append(parser)

    @staticmethod
    def _configure(processor, options):
        configure = getattr(processor, 'configure', None)
        if configure:
            configure(options)

    @staticmethod
    def _apply(text, filters):
        for processor in filters:
            decision = processor.process_token(text)
            if decision:
                action, replacement = decision
                if action in ('DROP', 'HIDE'):
                    return ''
                elif action in ('REPLACE', 'PASS') and replacement is not None:
                    text = replacement
        return text

    def feed(self, text: str) -> str:
        """Accept provider text and return its visible delta."""
        self.raw_parts.append(text)
        visible = self._apply(text, self.display_filters)
        self.display_parts.append(visible)
        self.return_parts.append(self._apply(text, self.return_filters))
        return visible

    def _finish(self, filters):
        tails = []
        for index, processor in enumerate(filters):
            complete = getattr(processor, 'on_complete', None)
            tail = complete() if complete else ''
            if tail:
                tails.append(self._apply(tail, filters[index + 1:]))
        return ''.join(tails)

    def finish(self) -> str:
        """Flush filters through the remainder of their pipeline once."""
        visible = self._finish(self.display_filters)
        self.display_parts.append(visible)
        self.return_parts.append(self._finish(self.return_filters))
        return visible

    @property
    def raw(self):
        return ''.join(self.raw_parts)

    @property
    def display(self):
        return ''.join(self.display_parts)

    @property
    def sanitized(self):
        return ''.join(self.return_parts)
