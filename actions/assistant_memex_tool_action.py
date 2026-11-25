"""Consult a model through a separate, durable Memex conversation."""

from app.conversations import send_message
from base_classes import Completed, StepwiseAction
from utils.tool_args import get_str


class AssistantMemexToolAction(StepwiseAction):
    """Start or continue a saved consultation using the caller's session builder."""

    def __init__(self, session):
        self.session = session

    @staticmethod
    def tool_name():
        return 'memex'

    @staticmethod
    def can_run(session):
        return (not getattr(session, 'private', False)
                and getattr(session, '_builder', None) is not None)

    @staticmethod
    def tool_spec(session):
        return {
            'args': ['message', 'model', 'session_id'],
            'description': (
                'Consult another model in a saved Memex conversation. Send a self-contained '
                'first message with an optional model name. To follow up, send the next '
                'message with the returned session_id. Read last_text for the reply.'
            ),
            'required': ['message'],
            'schema': {'properties': {
                'message': {'type': 'string', 'description': 'Message to send to the consultant.'},
                'model': {'type': 'string', 'description': 'Starting model. Defaults to your model.'},
                'session_id': {'type': 'string', 'description': 'Existing conversation ID for a follow-up.'},
            }},
            'auto_submit': True,
        }

    def start(self, args=None, content=None):
        args = args or {}
        message = get_str(args, 'message') or content
        model = get_str(args, 'model')
        sid = get_str(args, 'session_id')
        if not self.can_run(self.session):
            return Completed({'ok': False, 'error': 'Saved consultations are unavailable in this session.'})
        if sid and model:
            return Completed({'ok': False, 'error': 'For a follow-up, use session_id and omit model.'})
        options = {}
        if sid is None:
            options['model'] = model or self.session.get_params().get('model')
            options['project_context'] = getattr(self.session, 'project', None)
            options['base_directory'] = self.session.get_option('TOOLS', 'base_directory', 'working')
        try:
            sid, result = send_message(self.session._builder, sid, message, options,
                                       parent=self.session)
        except (OSError, ValueError, RuntimeError) as exc:
            return Completed({'ok': False, 'error': str(exc)})
        return Completed({
            'ok': result.status == 'completed', 'session_id': sid,
            'last_text': result.last_text, 'status': result.status,
            'stop_reason': result.stop_reason, 'error': result.error,
        })
