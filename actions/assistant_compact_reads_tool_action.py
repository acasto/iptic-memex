"""A task agent can shorten its own view of a completed text read."""
from base_classes import Completed, StepwiseAction
from workspace.context_view import ReadCompaction


class AssistantCompactReadsToolAction(StepwiseAction):
    def __init__(self, session):
        self.session = session

    @classmethod
    def tool_name(cls):
        return 'compact_reads'

    @classmethod
    def can_run(cls, session):
        return isinstance(getattr(session, 'read_compaction', None), ReadCompaction)

    @classmethod
    def tool_spec(cls, session):
        return {
            'args': ['file', 'summary'], 'required': ['file', 'summary'],
            'description': 'Shorten your context when finished exploring a file. The latest '
                           'completed native text file-read batch containing this exact path is '
                           'represented by your summary and history_read references on future calls.',
            'schema': {'properties': {
                'file': {'type': 'string', 'description': 'Exact file path used in the read.'},
                'summary': {'type': 'string', 'description': 'Findings, evidence and remaining uncertainty.'},
            }}, 'auto_submit': True,
        }

    def start(self, args=None, content=None):
        if not self.can_run(self.session):
            return Completed({'ok': False, 'error': 'Read compaction is disabled for this session'})
        args = args or {}
        try:
            result = self.session.read_compaction.compact(
                self.session.get_context('chat').get('all'), args.get('file'), args.get('summary'))
            return Completed({'ok': True, **result})
        except ValueError as exc:
            return Completed({'ok': False, 'error': str(exc)})
