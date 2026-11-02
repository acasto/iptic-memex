"""CLI adapter for application-owned bounded agent work."""

import json

from base_classes import InteractionMode
from app.runs import run_agent
from app.session_output import EventOutput
from ui.agent_renderer import AgentRenderer


class AgentMode(InteractionMode):
    def __init__(self, session, steps=1, writes_policy='deny', use_status_tags=True,
                 output_mode=None, message=None):
        self.session = session
        self.steps = max(1, int(steps))
        self.writes_policy = writes_policy
        self.use_status_tags = use_status_tags
        self.message = message
        self.output_mode = output_mode or session.get_option('AGENT', 'output', fallback='final')
        self.output = session.utils.output

    def start(self, *, emit_output=True):
        if emit_output and isinstance(self.output, EventOutput):
            from utils.output_utils import OutputHandler
            self.output = OutputHandler(self.session.config)
        renderer = AgentRenderer(self.output.write, getattr(self.output, 'warning', lambda text: None),
                                 output=self.output_mode,
                                 verbose=bool(self.session.get_params().get('agent_debug')))
        result = run_agent(session=self.session, steps=self.steps, message=self.message,
                           writes_policy=self.writes_policy, status_tags=self.use_status_tags,
                           output=self.output_mode, on_event=renderer if emit_output else None,
                           verbose_dump=bool(self.session.get_params().get('agent_debug')))
        if emit_output and self.output_mode == 'final':
            value = result.raw if self.session.get_params().get('raw_completion') else result.last_text
            if value is not None:
                self.output.write(value if isinstance(value, str) else json.dumps(value, default=str))
        return result
