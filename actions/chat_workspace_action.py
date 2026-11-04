"""Opt-in CLI commands for durable workspace tasks."""
import json
from pathlib import Path

from base_classes import Completed, StepwiseAction
from core.runtime.contracts import InteractionRequest
from ui.interaction_handler import CLIInteractionHandler
from workspace.chat import ChatWorkspace


class ChatWorkspaceAction(StepwiseAction):
    def __init__(self, session):
        self.session = session

    def start(self, args=None, content=None):
        try:
            if not getattr(self.session, 'cli_workspace_enabled', False):
                raise ValueError('Workspace attachment is available in CLI chat only')
            args = list(args or [])
            operation = args.pop(0) if args else 'list'
            attachment = getattr(self.session, 'chat_workspace', None)
            if operation == 'attach':
                if attachment:
                    raise ValueError('A workspace is already attached')
                if len(args) > 1:
                    raise ValueError('Usage: /workspace attach [directory]')
                directory = Path(args[0]).expanduser() if args else (
                    self.session.project.root / '.memex-workspace')
                self.session.chat_workspace = ChatWorkspace(
                    self.session, directory, config_file=getattr(self.session, 'chat_config_file', None))
                result = f'Attached workspace: {directory}'
            else:
                if attachment is None:
                    raise ValueError('First use /workspace attach [directory]')
                if operation in ('start', 'detach'):
                    options = {}
                    while args and args[0].startswith('--'):
                        option = args.pop(0)
                        if option == '--':
                            break
                        if option not in ('--profile', '--chat-last') or not args:
                            raise ValueError('Usage: start/detach [--profile PATH] '
                                             '[--chat-last N] [--] brief')
                        key = option[2:].replace('-', '_')
                        if key in options:
                            raise ValueError(f'Duplicate option: {option}')
                        value = args.pop(0)
                        if key == 'chat_last':
                            try:
                                value = int(value)
                            except ValueError:
                                raise ValueError('--chat-last requires an integer') from None
                        options[key] = value
                    result = attachment.start(' '.join(args), detached=operation == 'detach',
                                              profile=options.pop('profile', None), **options)
                elif operation == 'list':
                    result = [{'id': r['id'], 'status': r['status'], 'brief': r['brief']}
                              for r in attachment.tasks.list()]
                elif operation == 'inspect' and len(args) == 1:
                    result = attachment.inspect(args[0])
                elif operation == 'import' and 1 <= len(args) <= 2:
                    result = attachment.import_result(*args)
                elif operation == 'cancel' and len(args) == 1:
                    attachment.cancel(args[0])
                    result = 'Cancellation requested'
                elif operation == 'reply' and len(args) == 2:
                    record = attachment.inspect(args[0])
                    question = next((q for q in record.get('questions', []) if q['id'] == args[1]), None)
                    if not question:
                        raise ValueError('Unknown or closed question')
                    value = CLIInteractionHandler(self.session.utils.input, self.session.utils.output)(
                        InteractionRequest(**question))
                    attachment.reply(args[0], args[1], value)
                    result = 'Reply delivered'
                else:
                    raise ValueError('Use attach, start, detach, list, inspect TASK, '
                                     'import TASK [ATTEMPT], reply TASK QUESTION, or cancel TASK')
            self.session.ui.emit('status', {'message': result if isinstance(result, str) else
                                           json.dumps(result, ensure_ascii=False, indent=2)})
            return Completed({'ok': True})
        except (ValueError, OSError) as exc:
            self.session.ui.emit('warning', {'message': str(exc)})
            return Completed({'ok': False, 'error': str(exc)})
