import os
import datetime
import google.generativeai as genai
from google.generativeai import caching
from session_handler import APIProvider, SessionHandler
from actions.process_contexts_action import ProcessContextsAction


class GoogleProvider(APIProvider):
    """
    Google Generative AI provider with proper system prompt and context caching
    """
    def __init__(self, session: SessionHandler):
        self.session = session
        self.params = session.get_params()
        self.token_counter = session.get_action('count_tokens')
        
        # List of parameters that can be passed to the Google API
        self.parameters = [
            'model',
            'max_tokens',
            'temperature',
            'top_p',
            'top_k',
            'stop_sequences',
            'candidate_count',
            'stream',
            'tools',
            'tool_choice'
        ]

        # Initialize API client configuration
        api_key = self.params.get('api_key') or os.environ.get('GOOGLE_API_KEY', 'none')
        genai.configure(api_key=api_key)

        # Defer client initialization until first turn to handle caching
        self.client = None
        self.gchat = None
        self.usage = None
        self._cached_content = None
        self._first_turn = True

    def _initialize_client(self, first_turn_context=None):
        """Initialize client with optional caching on first turn"""
        if not self._should_enable_caching():
            # Standard initialization without caching
            self.client = genai.GenerativeModel(
                model_name=self.params['model'],
                system_instruction=self._get_system_prompt()
            )
            self.gchat = self.client.start_chat(history=[])
            return

        # Get combined content for potential caching
        content = self._get_system_prompt() or ""
        if first_turn_context:
            content += "\n" + first_turn_context

        # Check if content meets minimum token threshold
        token_count = self.token_counter.count_tiktoken(content)
        cache_threshold = 32768 + 1000  # Add buffer to be safe
        
        if token_count >= cache_threshold:
            # Setup caching
            try:
                ttl_minutes = float(self.params.get('cache_ttl', 60))
                self._cached_content = caching.CachedContent.create(
                    model=self.params['model'],
                    display_name=f"session_context_{datetime.datetime.now().isoformat()}",
                    system_instruction=content,
                    ttl=datetime.timedelta(minutes=ttl_minutes)
                )
                self.client = genai.GenerativeModel.from_cached_content(cached_content=self._cached_content)
            except Exception as e:
                print(f"Failed to initialize cache: {str(e)}")
                # Fall back to standard initialization
                self.client = genai.GenerativeModel(
                    model_name=self.params['model'],
                    system_instruction=self._get_system_prompt()
                )
        else:
            # Not enough content to cache, initialize normally
            self.client = genai.GenerativeModel(
                model_name=self.params['model'],
                system_instruction=self._get_system_prompt()
            )
        
        self.gchat = self.client.start_chat(history=[])

    def _should_enable_caching(self) -> bool:
        """Check if caching should be enabled based on config"""
        return (self.params.get('prompt_caching', False) and 
                self.params.get('cache', False))

    def _get_system_prompt(self) -> str:
        """Get system prompt from context"""
        if self.session.get_context('prompt'):
            return self.session.get_context('prompt').get()['content']
        return ""

    @staticmethod
    def _get_first_turn_context(messages) -> str:
        """Extract context from first turn if present"""
        if messages and len(messages) > 1:  # Skip system message
            first_msg = messages[1]
            if 'parts' in first_msg:
                # Look for context part specifically
                for part in first_msg['parts']:
                    if isinstance(part, dict) and 'text' in part:
                        # Assuming first text part is context
                        return part['text']
        return ""

    def _get_safety_settings(self):
        """Get safety settings from config"""
        settings = []
        default = self.params.get('safety_default', 'BLOCK_MEDIUM_AND_ABOVE')

        categories = {
            'harassment': 'HARM_CATEGORY_HARASSMENT',
            'hate_speech': 'HARM_CATEGORY_HATE_SPEECH',
            'sexually_explicit': 'HARM_CATEGORY_SEXUALLY_EXPLICIT',
            'dangerous_content': 'HARM_CATEGORY_DANGEROUS_CONTENT'
        }

        for category_key, enum_name in categories.items():
            threshold = self.params.get(f'safety_{category_key}', default)
            settings.append({
                'category': getattr(genai.types.HarmCategory, enum_name),
                'threshold': getattr(genai.types.HarmBlockThreshold, threshold)
            })

        return settings

    def chat(self):
        """Handle chat completion requests with caching support"""
        try:
            messages = self.assemble_message()
            if not messages:
                return None

            # Initialize client on first turn
            if self.client is None:
                first_turn_context = self._get_first_turn_context(messages)
                self._initialize_client(first_turn_context)
                
                # If we're caching, remove context from messages to avoid duplication
                if self._cached_content and len(messages) > 1:
                    messages = [messages[0]] + messages[2:]

            # Process generation parameters
            gen_params = {}
            for param in self.parameters:
                if param in self.params and self.params[param] is not None:
                    gen_params[param] = self.params[param]

            # Handle special parameters
            stream = gen_params.pop('stream', False)
            if 'model' in gen_params:
                del gen_params['model']
            if 'max_tokens' in gen_params:
                gen_params['max_output_tokens'] = int(gen_params.pop('max_tokens'))

            # Get safety settings
            safety_settings = self._get_safety_settings()

            # Send message
            response = self.gchat.send_message(
                messages[-1]['parts'],
                stream=stream,
                generation_config=genai.GenerationConfig(**gen_params),
                safety_settings=safety_settings
            )

            # Mark first turn complete
            self._first_turn = False

            # Handle streaming vs non-streaming response
            if stream:
                return response
            else:
                # Store usage metrics if available
                if hasattr(response, 'usage_metadata'):
                    self.usage = {
                        'prompt_tokens': response.usage_metadata.prompt_token_count,
                        'completion_tokens': response.usage_metadata.candidates_token_count,
                        'total_tokens': response.usage_metadata.total_token_count,
                        'cached_tokens': getattr(response.usage_metadata, 'cached_content_token_count', 0)
                    }
                if hasattr(response, 'parts'):
                    text_parts = [part.text for part in response.parts]
                    return ' '.join(text_parts)
                return response.text

        except Exception as e:
            error_msg = f"Error in chat completion: {str(e)}"
            print(error_msg)
            return error_msg

    def stream_chat(self):
        """Stream chat responses and capture final usage stats"""
        response = self.chat()
        try:
            if isinstance(response, str):
                yield response
                return

            final_chunk = None
            for chunk in response:
                if hasattr(chunk, 'text'):
                    yield chunk.text
                final_chunk = chunk

            # Capture usage from final chunk
            if final_chunk and hasattr(final_chunk, 'usage_metadata'):
                self.usage = {
                    'prompt_tokens': final_chunk.usage_metadata.prompt_token_count,
                    'completion_tokens': getattr(final_chunk.usage_metadata, 'candidates_token_count', 0),
                    'total_tokens': final_chunk.usage_metadata.total_token_count,
                    'cached_tokens': getattr(final_chunk.usage_metadata, 'cached_content_token_count', 0)
                }

        except Exception as e:
            yield f"Stream error: {str(e)}"

    def assemble_message(self) -> list:
        """Assemble the message from context"""
        message = []
        if self.session.get_context('prompt') and not self._cached_content:
            message.append({
                'role': 'model',
                'parts': [{
                    'text': self._get_system_prompt()
                }]
            })

        chat = self.session.get_context('chat')
        if chat is not None:
            for turn in chat.get():
                parts = []
                turn_contexts = []

                # Process contexts
                if 'context' in turn and turn['context']:
                    for ctx in turn['context']:
                        if ctx['type'] == 'image':
                            img_data = ctx['context'].get()
                            parts.append({
                                'inline_data': {
                                    'mime_type': img_data['mime_type'],
                                    'data': img_data['content']
                                }
                            })
                        else:
                            turn_contexts.append(ctx)

                    # Add text contexts
                    if turn_contexts:
                        text_context = ProcessContextsAction.process_contexts_for_assistant(turn_contexts)
                        if text_context:
                            parts.insert(0, {'text': text_context})

                # Add message text
                if turn['message']:
                    parts.append({'text': turn['message']})

                if parts:
                    message.append({'role': turn['role'], 'parts': parts})

        return message

    def get_messages(self):
        """Return assembled messages"""
        return self.assemble_message()

    def get_usage(self):
        """Return usage statistics including cache metrics"""
        if not self.usage:
            return {}

        stats = {
            'total_in': self.usage['prompt_tokens'],
            'total_out': self.usage['completion_tokens'],
            'total_tokens': self.usage['total_tokens'],
            'turn_in': self.usage['prompt_tokens'],
            'turn_out': self.usage['completion_tokens'],
            'turn_total': self.usage['total_tokens']
        }

        if 'cached_tokens' in self.usage:
            stats['cached_tokens'] = self.usage['cached_tokens']

        return stats

    def reset_usage(self):
        """Reset usage statistics"""
        self.usage = None

    def cleanup(self):
        """Clean up resources and cached content"""
        if self._cached_content:
            try:
                self._cached_content.delete()
                self._cached_content = None
            except Exception as e:
                raise Exception(f"Failed to delete Google cache: {str(e)}")

    def __del__(self):
        """Attempt cleanup on deletion"""
        try:
            self.cleanup()
        except Exception:
            pass
