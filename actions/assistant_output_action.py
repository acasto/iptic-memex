from base_classes import InteractionAction
from collections import deque


class AssistantOutputAction(InteractionAction):
    """
    Action for processing and filtering assistant output with pattern detection.
    Sits between the raw output stream and display, allowing for content transformation
    and selective processing.
    """

    def __init__(self, session):
        self.session = session
        self.output = session.utils.output
        self.buffer = deque(maxlen=100)  # Configurable buffer size
        self.accumulated = ""
        self.in_tag = False
        self.current_tag = None
        self.tag_content = ""
        self.final_content = None

        # Configure the buffer size from tools if available
        buffer_size = session.get_tools().get('output_buffer_size', 100)
        self.buffer = deque(maxlen=buffer_size)

    def on_token(self, token):
        """
        Process each token as it arrives from the stream.
        This is the callback for StreamHandler.process_stream()
        """
        # Add to buffer for pattern detection
        self.buffer.append(token)
        self.accumulated += token

        # Basic pass-through for now
        self.output.write(token, end='', flush=True)

        return token

    def on_buffer_full(self, buffer_content):
        """
        Called when the buffer reaches its configured size
        Can be used for analyzing larger chunks of text
        """
        # For now, just a placeholder for future pattern detection
        pass

    def on_complete(self, full_content):
        """
        Called when streaming is complete with the full content
        """
        # Store the complete content for potential processing by tools
        self.final_content = full_content
        return full_content

    def run(self, stream, spinner_message="Processing response..."):
        """
        Entry point for processing a stream of tokens
        Returns the full processed content
        """
        # Reset state on each run
        self.buffer.clear()
        self.accumulated = ""
        self.in_tag = False
        self.current_tag = None
        self.tag_content = ""

        result = self.session.utils.stream.process_stream(
            stream,
            on_token=self.on_token,
            on_buffer=self.on_buffer_full,
            on_complete=self.on_complete,
            spinner_message=spinner_message
        )
        self.output.write('', flush=True)

        return result

    @staticmethod
    def _detect_pattern(text, start_pattern, end_pattern):
        """
        Helper method to detect patterns in text
        Returns (is_detected, remaining_text)
        """
        # Basic implementation - enhance as needed
        start_idx = text.find(start_pattern)
        if start_idx == -1:
            return False, text

        end_idx = text.find(end_pattern, start_idx + len(start_pattern))
        if end_idx == -1:
            return True, text  # Found start but not end

        # Pattern detected
        return True, text[:start_idx] + text[end_idx + len(end_pattern):]
