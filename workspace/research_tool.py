"""Model-directed research decisions, separate from generic tools and execution."""

import json

from base_classes import Completed, StepwiseAction


class ResearchToolAction(StepwiseAction):
    def __init__(self, session):
        self.session = session

    @classmethod
    def tool_name(cls):
        return 'research'

    @classmethod
    def can_run(cls, session):
        return getattr(session, 'research', None) is not None

    @classmethod
    def tool_spec(cls, session):
        return {
            'args': ['operation', 'content', 'question', 'purpose', 'question_id',
                     'status', 'references', 'calls'],
            'description': (
                'Maintain the research goal and open questions; request an independent focused '
                'investigation when useful. brief saves a working interpretation/coverage target. '
                'question creates or updates a question with its purpose and evidence. '
                'activity records advisory focus. investigate starts one branch for an open '
                'question_id, optionally reserving calls (default 6) from the shared budget. '
                'status shows progress; wait collects the active branch without model polling. '
                'conclude saves a coverage assessment, remaining gaps, sufficient|partial status, '
                'and supporting note references; it ends research for fresh synthesis.'
            ),
            'required': ['operation'],
            'schema': {'properties': {
                'operation': {'type': 'string', 'enum': ['brief', 'question', 'activity',
                             'investigate', 'status', 'wait', 'conclude']},
                'content': {'type': 'string'},
                'question': {'type': 'string'},
                'purpose': {'type': 'string'},
                'question_id': {'type': 'string'},
                'status': {'type': 'string'},
                'references': {'type': 'string', 'description': 'CSV of relevant published note IDs'},
                'calls': {'type': 'integer', 'minimum': 1},
            }},
            'auto_submit': True,
        }

    def start(self, args=None, content=None):
        args = dict(args or {})
        if content is not None:
            args.setdefault('content', content)
        try:
            coordinator = getattr(self.session, 'research', None)
            if coordinator is None:
                raise ValueError('Research decisions are available only in a lead investigation')
            result = coordinator.act(args)
            payload = {'ok': True, 'result': result}
        except (ValueError, OSError, TypeError) as exc:
            payload = {'ok': False, 'error': str(exc)}
        self.session.add_context('assistant', {
            'name': 'research_result', 'content': json.dumps(payload, ensure_ascii=False),
        })
        return Completed(payload)
