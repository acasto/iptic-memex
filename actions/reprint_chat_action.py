from base_classes import InteractionAction
from actions.assistant_output_action import AssistantOutputAction  # only for non-stream filter path
from utils.output_utils import ColorSystem, Style
import os
import re


class ReprintChatAction(InteractionAction):

    def __init__(self, session):
        self.session = session

    def run(self, args=None):
        """
        Reprints the chat conversation.
        - Default: apply configured output filters to assistant messages
          (mirrors streaming display behavior) and respect `context_sent`.
        - With 'raw': bypass filters but still respect `context_sent` (rolling window).
        - With 'all': fetch full history and still apply filters.
        - With 'raw all': bypass filters and print the full history exactly as stored.
        """
        chat = self.session.get_context('chat')
        params = self.session.get_params()

        # clear the screen
        self._clear_screen()

        # Determine whether to bypass filters and/or fetch full history
        bypass_filters = False
        fetch_all = False
        if args:
            tokens = []
            if isinstance(args, list):
                tokens = [str(a).lower() for a in args]
            elif isinstance(args, str):
                tokens = [args.lower()]
            # 'raw' means bypass filters; 'all' means fetch entire history
            bypass_filters = ('raw' in tokens)
            fetch_all = ('all' in tokens)

        formatted = ""
        turns = chat.get('all' if fetch_all else None)
        for turn in turns:
            if turn['role'] == 'user':
                label = self._color_wrap(params.get('user_label', 'User:'), params.get('user_label_color', 'white'))
                message = turn['message'] or ''
            else:
                label = self._color_wrap(params.get('response_label', 'Assistant:'), params.get('response_label_color', 'white'))
                # Apply output filters unless bypassing
                if bypass_filters:
                    message = turn['message'] or ''
                else:
                    # Use the display pipeline to mirror what the user saw during streaming
                    message = AssistantOutputAction.filter_full_text(turn['message'] or '', self.session)

            formatted += f"{label} {message}\n\n"

        # In non-blocking UIs (Web/TUI), emit the full formatted text as a single status message
        if not getattr(self.session.ui.capabilities, 'blocking', False):
            try:
                self.session.ui.emit('status', {'message': formatted})
            except Exception:
                pass
            return

        try:
            out = self.session.utils.output
            if self.session.get_params().get('highlighting'):
                out.write(self._format_code_block(formatted))
            else:
                out.write(formatted)
        except Exception:
            # Last resort fallback
            if self.session.get_params().get('highlighting'):
                print(self._format_code_block(formatted))
            else:
                print(formatted)

    @staticmethod
    def _clear_screen():
        try:
            if os.name == 'nt':
                os.system('cls')
            else:
                os.system('clear')
        except Exception:
            pass

    @staticmethod
    def _color_wrap(text: str, color: str | None = None) -> str:
        try:
            if not color:
                return text
            return ColorSystem.style_text(text, Style(fg=color))
        except Exception:
            return text

    @staticmethod
    def _format_code_block(text: str) -> str:
        def highlight_block(block: str) -> str:
            match = re.match(r"^```(\w+)?\n([\s\S]*?)\n?```$", block, re.DOTALL)
            if not match:
                return block
            language = match.group(1)
            code_content = match.group(2)
            try:
                from pygments import highlight
                from pygments.lexers import get_lexer_by_name, guess_lexer
                from pygments.formatters import TerminalFormatter
                from pygments.util import ClassNotFound
                try:
                    if language:
                        lexer = get_lexer_by_name(language, startinline=(language.lower() == 'php'))
                    else:
                        lexer = guess_lexer(code_content)
                except Exception:
                    lexer = get_lexer_by_name('text', stripall=True)
                formatter = TerminalFormatter()
                highlighted_code = highlight(code_content, lexer, formatter)
                return f"```{language or ''}\n{highlighted_code}```"
            except Exception:
                return block

        parts = re.split(r'(```[\s\S]*?```)', text)
        formatted_parts = [highlight_block(part) if part.startswith('```') else part for part in parts]
        return ''.join(formatted_parts)
