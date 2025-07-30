```
"""
Mock provider for testing iptic-memex without requiring API keys.
This provider gives simple responses to test the chat functionality.
"""

from base_classes import APIProvider


class MockProvider(APIProvider):
    """
    A mock provider that gives simple responses for testing.
    """
    
    def __init__(self, session):
        self.session = session
        self.messages = []
        self.response_count = 0
        
    def chat(self):
        """Generate a simple mock response."""
        chat_context = self.session.get_context('chat')
        if not chat_context:
            return "Error: No chat context available"
        
        try:
            messages = chat_context.get()
            if not messages:
                return "Error: No messages found"
            
            # Get the last user message
            last_message = messages[-1]
            user_message = last_message.get('message', '')
            
            # Generate simple responses based on content
            self.response_count += 1
            
            if 'hello' in user_message.lower():
                response = f"Hello! I'm a mock assistant. This is response #{self.response_count}."
            elif 'help' in user_message.lower():
                response = f"I'm a mock provider for testing. I can respond to your messages but I don't have real AI capabilities. Response #{self.response_count}."
            elif 'test' in user_message.lower():
                response = f"Test successful! The TUI and chat system are working. Response #{self.response_count}."
            else:
                response = f"I received your message: '{user_message[:50]}...' This is mock response #{self.response_count}."
            
            return response
            
        except Exception as e:
            return f"Mock provider error: {e}"
    
    def stream_chat(self):
        """Generate a streaming mock response."""
        response = self.chat()
        if response:
            # Simulate streaming by yielding chunks
            words = response.split()
            for word in words:
                yield word + " "
        return None
    
    def get_messages(self):
        """Get the current messages."""
        chat_context = self.session.get_context('chat')
        if chat_context and hasattr(chat_context, 'get'):
            return chat_context.get()
        return []
    
    def get_full_response(self):
        """Get the full response (for raw mode)."""
        return self.chat()
    
    def get_usage(self):
        """Return mock usage stats."""
        return {
            'prompt_tokens': 50,
            'completion_tokens': 25,
            'total_tokens': 75
        }
    
    def reset_usage(self):
        """Reset usage stats."""
        self.response_count = 0
    
    def get_cost(self):
        """Return mock cost information."""
        return {
            'prompt_cost': 0.001,
            'completion_cost': 0.002,
            'total_cost': 0.003
        }
```