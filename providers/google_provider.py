import os
import datetime
import google.generativeai as genai
from google.generativeai import caching
from session_handler import APIProvider, SessionHandler
from actions.process_contexts_action import ProcessContextsAction


class GoogleProvider(APIProvider):
    """
    Google Generative AI provider with support for context caching
    """
    def __init__(self, session: SessionHandler):
        self.session = session
        self.params = session.get_params()

        # List of parameters that can be passed to the Google API
        self.parameters = [
            'temperature',
            'top_p',
            'top_k',
            'system',
            'stop_sequences',
            'tools',
            'tool_choice'
        ]

        self._configure_client()
        self._initialize_chat()
        self._cached_content = None
        self.usage = None

    def _configure_client(self):
        """Configure the Google API client with credentials"""
        api_key = self.params.get('api_key') or os.environ.get('GOOGLE_API_KEY', 'none')
        genai.configure(api_key=api_key)

    def _initialize_chat(self):
        """Initialize chat with optional caching support"""
        # Only create cache if caching is enabled and we have a system prompt
        if self._should_enable_caching():
            self._setup_cache()
            if self._cached_content:
                self.client = genai.GenerativeModel.from_cached_content(
                    cached_content=self._cached_content
                )
            else:
                self.client = genai.GenerativeModel(self.params['model'])
        else:
            self.client = genai.GenerativeModel(self.params['model'])

        self.gchat = self.client.start_chat(history=[])

    def _should_enable_caching(self) -> bool:
        """Check if caching should be enabled"""
        return bool(self.params.get('prompt_caching', False))

    def _setup_cache(self):
        """Set up context caching for prompt and initial context"""
        try:
            # Get TTL from config or use default
            ttl_minutes = float(self.params.get('cache_ttl', 5))
            ttl = datetime.timedelta(minutes=ttl_minutes)

            # Build initial context from prompt and any existing context
            prompt_context = ''
            if self.session.get_context('prompt'):
                prompt_context = self.session.get_context('prompt').get()['content']

            # Only proceed if we have enough content to meet minimum token requirement
            # Note: A proper token counting implementation would be needed here
            # For now we'll try to cache and let the API handle validation
            if prompt_context:
                self._cached_content = caching.CachedContent.create(
                    model=self.params['model'],
                    display_name=f"session_prompt_{datetime.datetime.now().isoformat()}",
                    system_instruction=prompt_context,
                    ttl=ttl
                )
        except Exception as e:
            # Log error but continue without caching
            print(f"Failed to initialize cache: {str(e)}")
            self._cached_content = None

    def chat(self):
        """Handle chat completion requests"""
        try:
            # Assemble the message from the context
            messages = self.assemble_message()
            if not messages:
                return None

            # Extract the latest message
            latest_message = messages[-1]['content']

            # Loop through parameters and add supported ones
            gen_params = {}
            for param in self.parameters:
                if param in self.params and self.params[param] is not None:
                    gen_params[param] = self.params[param]

            # Handle type conversions for numeric parameters
            if 'temperature' in gen_params:
                gen_params['temperature'] = float(gen_params['temperature'])
            if 'max_tokens' in gen_params:
                gen_params['max_tokens'] = int(gen_params['max_tokens'])

            # Send message with filtered parameters
            response = self.gchat.send_message(
                latest_message,
                stream=bool(gen_params.get('stream', False)),
                generation_config=genai.GenerationConfig(**gen_params)
            )

            # Handle streaming vs non-streaming response
            if self.params.get('stream', False):
                return response
            else:
                # Store usage metrics if available
                if hasattr(response, 'usage_metadata'):
                    self.usage = response.usage_metadata
                return response.text

        except Exception as e:
            error_msg = f"Error in chat completion: {str(e)}"
            print(error_msg)
            return error_msg

    def stream_chat(self):
        """Stream chat responses"""
        response = self.chat()
        try:
            if isinstance(response, str):
                yield response
                return
            for chunk in response:
                if hasattr(chunk, 'text'):
                    yield chunk.text
                else:
                    yield str(chunk)
        except Exception as e:
            yield f"Stream error: {str(e)}"

    def assemble_message(self) -> list:
        """Assemble messages for the chat"""
        messages = []

        # Add system prompt if not using cache
        if not self._cached_content and self.session.get_context('prompt'):
            messages.append({
                'role': 'system',
                'content': self.session.get_context('prompt').get()['content']
            })

        # Add chat history
        chat = self.session.get_context('chat')
        if chat is not None:
            for turn in chat.get():
                turn_context = ''
                if 'context' in turn and turn['context']:
                    turn_context = ProcessContextsAction.process_contexts_for_assistant(turn['context'])
                messages.append({
                    'role': turn['role'],
                    'content': f"{turn_context}\n{turn['message']}" if turn_context else turn['message']
                })

        return messages

    def get_messages(self):
        """Return assembled messages"""
        return self.assemble_message()

    def get_usage(self):
        """Return usage statistics"""
        if not self.usage:
            return {}

        stats = {
            'total_tokens': self.usage.total_token_count,
            'prompt_tokens': self.usage.prompt_token_count,
            'completion_tokens': self.usage.candidates_token_count,
        }

        if hasattr(self.usage, 'cached_content_token_count'):
            stats['cached_tokens'] = self.usage.cached_content_token_count

        return stats

    def reset_usage(self):
        """Reset usage statistics"""
        self.usage = None

    def __del__(self):
        """Cleanup cached content on deletion"""
        if self._cached_content:
            try:
                self._cached_content.delete()
            except Exception:
                pass  # Ignore cleanup errors
