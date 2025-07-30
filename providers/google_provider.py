import os
import datetime
import google.generativeai as genai
from google.generativeai import caching
from base_classes import APIProvider
from actions.process_contexts_action import ProcessContextsAction


class GoogleProvider(APIProvider):
    """
    Google Generative AI provider with proper system prompt and context caching
    """
    def __init__(self, session: Session):
        self.session = session
        self.params = session.get_params()
        self.token_counter = session.get_action('count_tokens')
        self._last_response = None

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
        self.turn_usage = None
        self.total_usage = {
            'prompt_tokens': 0,
            'completion_tokens': 0,
            'total_tokens': 0,
            'cached_tokens': 0
        }
        self._cached_content = None
        self._first_turn = True

    def _initialize_client(self, first_turn_context=None):
        """Initialize client with optional caching"""
        if not self._should_enable_caching():
            # Standard initialization without caching
            init_params = {'model_name': self.params['model']}
            system_prompt = self._get_system_prompt()
            if system_prompt:
                init_params['system_instruction'] = system_prompt

            self.client = genai.GenerativeModel(**init_params)
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

                # Only modify messages for multi-turn chat mode
                if self._cached_content and len(messages) > 1 and not self.session.get_flag('completion_mode'):
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
            self._last_response = response

            # Mark first turn complete
            self._first_turn = False

            # Handle streaming vs non-streaming response
            if stream:
                return response
            else:
                # Store usage metrics if available
                if hasattr(response, 'usage_metadata'):
                    self.turn_usage = {
                        'prompt_tokens': response.usage_metadata.prompt_token_count,
                        'completion_tokens': response.usage_metadata.candidates_token_count,
                        'total_tokens': response.usage_metadata.total_token_count,
                        'cached_tokens': getattr(response.usage_metadata, 'cached_content_token_count', 0)
                    }
                    # Update total usage
                    self.total_usage['prompt_tokens'] += self.turn_usage['prompt_tokens']
                    self.total_usage['completion_tokens'] += self.turn_usage['completion_tokens']
                    self.total_usage['total_tokens'] += self.turn_usage['total_tokens']
                    self.total_usage['cached_tokens'] += self.turn_usage['cached_tokens']

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
                self.turn_usage = {
                    'prompt_tokens': final_chunk.usage_metadata.prompt_token_count,
                    'completion_tokens': getattr(final_chunk.usage_metadata, 'candidates_token_count', 0),
                    'total_tokens': final_chunk.usage_metadata.total_token_count,
                    'cached_tokens': getattr(final_chunk.usage_metadata, 'cached_content_token_count', 0)
                }
                # Update total usage
                self.total_usage['prompt_tokens'] += self.turn_usage['prompt_tokens']
                self.total_usage['completion_tokens'] += self.turn_usage['completion_tokens']
                self.total_usage['total_tokens'] += self.turn_usage['total_tokens']
                self.total_usage['cached_tokens'] += self.turn_usage['cached_tokens']

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
                parts.append({'text': turn['message'].strip()})

                if parts:
                    message.append({'role': turn['role'], 'parts': parts})
        return message

    def get_full_response(self):
        """Returns the full response object from the last API call"""
        return self._last_response

    def get_messages(self):
        """Return assembled messages"""
        return self.assemble_message()

    def get_usage(self):
        """Return usage statistics including cache metrics"""
        if not self.total_usage:
            return {}

        stats = {
            'total_in': self.total_usage['prompt_tokens'],
            'total_out': self.total_usage['completion_tokens'],
            'total_tokens': self.total_usage['total_tokens'],
            'turn_in': self.turn_usage['prompt_tokens'] if self.turn_usage else 0,
            'turn_out': self.turn_usage['completion_tokens'] if self.turn_usage else 0,
            'turn_total': self.turn_usage['total_tokens'] if self.turn_usage else 0
        }

        if self.total_usage['cached_tokens'] > 0:
            stats['cached_tokens'] = self.total_usage['cached_tokens']

        return stats

    def reset_usage(self):
        """Reset usage statistics"""
        self.turn_usage = None
        self.total_usage = {
            'prompt_tokens': 0,
            'completion_tokens': 0,
            'total_tokens': 0,
            'cached_tokens': 0
        }

    def get_cost(self) -> dict:
        """Calculate cost specifically for Google's caching model"""
        usage = self.get_usage()
        if not usage:
            return {'total_cost': 0.0}

        try:
            price_unit = float(self.params.get('price_unit', 1000000))
            price_in = float(self.params.get('price_in', 0))
            price_out = float(self.params.get('price_out', 0))

            # Regular costs
            input_cost = (usage['total_in'] / price_unit) * price_in
            output_cost = (usage['total_out'] / price_unit) * price_out

            result = {
                'input_cost': round(input_cost, 6),
                'output_cost': round(output_cost, 6),
                'total_cost': round(input_cost + output_cost, 6)
            }

            # Only include cache savings if there are actually cached tokens
            if 'cached_tokens' in usage and usage['cached_tokens'] > 0:
                cache_tokens = usage['cached_tokens']
                cache_savings = round((cache_tokens / price_unit) * price_in, 6)
                result['cache_savings'] = cache_savings

            return result
        except (ValueError, TypeError):
            return None

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
