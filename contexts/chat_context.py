from base_classes import InteractionContext
from datetime import datetime
import random


class ChatContext(InteractionContext):
    def __init__(self, session, context_data=None):
        self.context_data = context_data
        self.session = session
        self.conversation = []  # list to hold the file name and content

    def add(self, message, role='user', context=None, extra=None):
        # If the conversation is empty and the role isn't 'user', insert a blank 'user' message first
        if not self.conversation and role != 'user':
            self.add('', role='user')  # Add a blank 'user' message

        if message is None:
            message = ''
        turn = {
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'role': role,
            'message': message,
        }
        if context is not None:
            if isinstance(context, list):
                turn['context'] = context
            else:
                turn['context'] = [context]
        # Allow callers to include extra fields (e.g., tool_call_id for official tool outputs)
        if isinstance(extra, dict):
            try:
                for k, v in extra.items():
                    # Avoid clobbering reserved keys
                    if k not in ('timestamp', 'role', 'message', 'context'):
                        turn[k] = v
            except Exception:
                pass
        # Ensure each turn has structured metadata with a stable id and index
        meta = {}
        try:
            existing_meta = turn.get('meta')
            if isinstance(existing_meta, dict):
                meta.update(existing_meta)
        except Exception:
            pass
        if isinstance(extra, dict):
            try:
                extra_meta = extra.get('meta')
                if isinstance(extra_meta, dict):
                    for k, v in extra_meta.items():
                        if k not in meta:
                            meta[k] = v
            except Exception:
                pass
        # Assign a stable identifier if not provided (compact base36 id + random tail)
        if 'id' not in meta:
            try:
                idx = len(self.conversation) + 1
            except Exception:
                idx = None
            meta['id'] = self._short_id(idx)
        # Monotonic index within the conversation (1-based)
        if 'index' not in meta:
            meta['index'] = len(self.conversation) + 1
        turn['meta'] = meta
        self.conversation.append(turn)

    @staticmethod
    def _short_id(index_hint: int | None, suffix_len: int = 4) -> str:
        """Generate a compact, anchored-looking id like 't9-3xf7'."""
        def to_b36(n: int) -> str:
            if n is None or n <= 0:
                return "0"
            digits = "0123456789abcdefghijklmnopqrstuvwxyz"
            out = ""
            while n:
                n, r = divmod(n, 36)
                out = digits[r] + out
            return out

        idx_part = f"t{to_b36(index_hint)}"
        tail = "".join(random.choice("0123456789abcdefghijklmnopqrstuvwxyz") for _ in range(suffix_len))
        return f"{idx_part}-{tail}"

    def get(self, args=None):
        # Return the entire conversation
        if args == "all":
            return self.conversation

        # Get fresh params each time
        params = self.session.get_params()
        context_sent = params.get('context_sent', 'all')
        if context_sent == 'none' or context_sent == 'last_1':
            return self.conversation[-1:] if self.conversation else []
        elif context_sent == 'all':
            return self.conversation
        else:
            parts = context_sent.split('_')
            if len(parts) == 2 and parts[1].isdigit():
                n = int(parts[1])
                if parts[0] == 'first':
                    return self.conversation[:n]
                elif parts[0] == 'last':
                    return self.conversation[-n:]

        # Default to returning all if the option is not recognized
        return self.conversation

    def clear(self):
        self.conversation = []

    def remove_last_message(self):
        """
        Remove the last message from the conversation.
        :return: True if a message was removed, False otherwise
        """
        if self.conversation:
            self.conversation.pop()
            return True
        return False

    def remove_messages(self, n):
        """
        Remove the last n messages from the conversation.
        :param n: number of messages to remove
        :return: number of messages actually removed
        """
        if n > 0:
            removed = min(n, len(self.conversation))
            self.conversation = self.conversation[:-removed]
            return removed
        return 0

    def remove_first_message(self):
        """
        Remove the first message from the conversation.
        :return: True if a message was removed, False otherwise
        """
        if self.conversation:
            self.conversation.pop(0)
            return True
        return False

    def remove_first_messages(self, n):
        """
        Remove the first n messages from the conversation.
        :param n: number of messages to remove
        :return: number of messages actually removed
        """
        if n > 0:
            removed = min(n, len(self.conversation))
            self.conversation = self.conversation[removed:]
            return removed
        return 0
