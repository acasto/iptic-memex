"""Model-facing task delegation capabilities, separate from strategy prompts."""

from base_classes import Completed, StepwiseAction
from utils.tool_args import get_str, get_float
from workspace.task_service import AgentAccess


class WorkspaceAgentsToolAction(StepwiseAction):
    def __init__(self, session):
        self.session = session

    @classmethod
    def tool_name(cls):
        return 'agents'

    @classmethod
    def can_run(cls, session):
        return isinstance(getattr(session, 'agents', None), AgentAccess)

    @classmethod
    def tool_spec(cls, session):
        return {
            'args': ['operation', 'target', 'message', 'seconds'], 'required': ['operation'],
            'description': 'Task agents in this project: spawn isolated work with message; status '
            'lists IDs, results and remaining resources; send queues selected text for a future '
            'call; wait waits up to 30 seconds without model polling; followup continues a finished '
            'descendant session; cancel stops a descendant. Shared task limits apply to all calls. '
            'No directories, permissions, or budgets can be supplied. Results are unverified claims.',
            'schema': {'properties': {
                'operation': {'type': 'string', 'enum': [
                    'spawn', 'status', 'send', 'wait', 'followup', 'cancel']},
                'target': {'type': 'string'}, 'message': {'type': 'string'},
                'seconds': {'type': 'number', 'minimum': 0, 'maximum': 30},
            }}, 'auto_submit': True,
        }

    def start(self, args=None, content=None):
        args = args or {}
        try:
            if not self.can_run(self.session):
                raise ValueError('Agents tool requires task access')
            unknown = set(args) - {'operation', 'target', 'message', 'seconds'}
            if unknown:
                raise ValueError('Unsupported agents arguments: ' + ', '.join(sorted(unknown)))
            access = self.session.agents
            service, caller = access.service, access.agent_id
            operation, target = get_str(args, 'operation'), get_str(args, 'target')
            message = get_str(args, 'message') or content or ''
            if operation == 'spawn':
                result = {'agent_id': service.start(message, parent_id=caller)}
            elif operation == 'status':
                result = service.status(target)
            elif operation == 'send':
                result = service.send(caller, target, message)
            elif operation == 'wait':
                result = service.wait(target, caller=caller, seconds=get_float(args, 'seconds', 30))
            elif operation == 'followup':
                result = service.followup(caller, target, message)
            elif operation == 'cancel':
                result = service.cancel(caller, target)
            else:
                raise ValueError('Unknown agents operation')
            return Completed({'ok': True, **result})
        except (ValueError, RuntimeError) as exc:
            return Completed({'ok': False, 'error': str(exc)})
