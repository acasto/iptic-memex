import json
import os
from google import genai
from google.genai import types as gx_types
from base_classes import APIProvider
from actions.process_contexts_action import ProcessContextsAction


class GoogleProvider(APIProvider):
    """
    Google Generative AI provider with proper system prompt and context caching
    """
    ##%%BLOCK:refactor_google_no_caching%%
    def __init__(self, session):
        self.session = session
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

        self.client = None
        self.turn_usage = None
        self.total_usage = {
            'prompt_tokens': 0,
            'completion_tokens': 0,
            'total_tokens': 0,
            'cached_tokens': 0
        }

    def _ensure_client(self) -> None:
        """Lazily initialize the google-genai client when first needed."""
        if self.client is not None:
            return
        params = self.session.get_params()
        api_key = params.get('api_key') or os.environ.get('GOOGLE_API_KEY')
        try:
            if api_key:
                self.client = genai.Client(api_key=api_key)
            else:
                # Let the client resolve credentials from environment/defaults
                self.client = genai.Client()
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Google GenAI client: {e}")

    def _get_system_prompt(self) -> str:
        """Get system prompt from context"""
        prompt_context = self.session.get_context('prompt')
        if prompt_context:
            return prompt_context.get()['content']
        return ""

    def _get_safety_settings(self):
        """Get safety settings from current config"""
        params = self.session.get_params()
        
        settings = []
        default = params.get('safety_default', 'BLOCK_MEDIUM_AND_ABOVE')

        categories = {
            'harassment': 'HARM_CATEGORY_HARASSMENT',
            'hate_speech': 'HARM_CATEGORY_HATE_SPEECH',
            'sexually_explicit': 'HARM_CATEGORY_SEXUALLY_EXPLICIT',
            'dangerous_content': 'HARM_CATEGORY_DANGEROUS_CONTENT'
        }

        for category_key, enum_name in categories.items():
            threshold = params.get(f'safety_{category_key}', default)
            settings.append({
                'category': getattr(gx_types.HarmCategory, enum_name),
                'threshold': getattr(gx_types.HarmBlockThreshold, threshold)
            })

        return settings

    def _build_tools_config(self):
        """Return google-genai Tool definitions when official tool mode is enabled."""
        try:
            mode = getattr(self.session, 'get_effective_tool_mode', lambda: 'none')()
        except Exception:
            mode = 'none'
        if mode != 'official':
            return None
        try:
            cmd = self.session.get_action('assistant_commands')
        except Exception:
            cmd = None
        if not cmd or not hasattr(cmd, 'get_tool_specs'):
            return None
        try:
            canonical = cmd.get_tool_specs() or []
        except Exception:
            canonical = []
        decls = []
        for spec in canonical:
            try:
                params_obj = {}
                p = spec.get('parameters') or {}
                params_obj['type'] = p.get('type') or 'object'
                params_obj['properties'] = p.get('properties') or {}
                required = p.get('required')
                if isinstance(required, list):
                    params_obj['required'] = required
                decls.append({
                    'name': spec.get('name'),
                    'description': spec.get('description'),
                    'parameters': params_obj,
                })
            except Exception:
                continue
        if not decls:
            return None
        return [gx_types.Tool(function_declarations=decls)]

    def _prepare_request(self):
        """Build request payload (contents + config + model) for google-genai."""
        self._ensure_client()
        current_params = self.session.get_params()

        messages = self.assemble_message()
        if not messages:
            return None, None, None

        contents = self._build_contents(messages)
        if not contents:
            return None, None, None

        # Process generation parameters using fresh params
        gen_params = {}
        for param in self.parameters:
            if param in current_params and current_params[param] is not None:
                gen_params[param] = current_params[param]

        tools_obj = self._build_tools_config()

        # Strip fields that don't belong in GenerateContentConfig
        gen_params.pop('tools', None)
        gen_params.pop('tool_choice', None)
        gen_params.pop('stream', None)
        gen_params.pop('model', None)

        if 'max_tokens' in gen_params:
            try:
                gen_params['max_output_tokens'] = int(gen_params.pop('max_tokens'))
            except (TypeError, ValueError):
                gen_params.pop('max_tokens', None)

        cfg_kwargs = dict(gen_params)

        safety_settings = self._get_safety_settings()
        if safety_settings:
            cfg_kwargs['safety_settings'] = safety_settings

        system_prompt = self._get_system_prompt()
        if system_prompt:
            cfg_kwargs['system_instruction'] = system_prompt

        if tools_obj:
            cfg_kwargs['tools'] = tools_obj

        config = gx_types.GenerateContentConfig(**cfg_kwargs)

        api_model = current_params.get('model_name', current_params.get('model'))
        return contents, config, api_model

    def _build_contents(self, messages):
        """Convert internal chat history into google-genai Content objects."""
        contents = []
        pending_calls = []
        system_prompt = self._get_system_prompt()
        system_consumed = False

        for msg in messages:
            role = msg.get('role')

            if role == 'model' and not system_consumed and system_prompt:
                if self._is_system_message(msg, system_prompt):
                    system_consumed = True
                    continue

            if role == 'assistant':
                parts = []
                for call in msg.get('tool_calls') or []:
                    fn_name = call.get('name')
                    args = call.get('arguments') or {}
                    fn_call = gx_types.FunctionCall(name=fn_name, args=args)
                    parts.append(gx_types.Part(function_call=fn_call))
                    pending_calls.append({
                        'id': call.get('id') or call.get('call_id'),
                        'name': fn_name,
                    })

                parts.extend(self._convert_basic_parts(msg.get('parts') or []))
                if not parts:
                    continue
                contents.append(gx_types.Content(role='model', parts=parts))
                continue

            if role == 'tool':
                call_id = msg.get('tool_call_id')
                tool_entry = None
                if call_id:
                    for idx, pending in enumerate(pending_calls):
                        if pending.get('id') == call_id:
                            tool_entry = pending_calls.pop(idx)
                            break
                if tool_entry is None and pending_calls:
                    tool_entry = pending_calls.pop(0)

                payload = self._build_tool_response_payload(msg)
                part = gx_types.Part.from_function_response(
                    name=(tool_entry or {}).get('name') or 'tool_result',
                    response=payload,
                )
                contents.append(gx_types.Content(role='user', parts=[part]))
                continue

            basic_parts = self._convert_basic_parts(msg.get('parts') or [])
            if not basic_parts:
                continue
            contents.append(gx_types.Content(role='user', parts=basic_parts))

        return contents

    def _convert_basic_parts(self, parts):
        converted = []
        for part in parts:
            if isinstance(part, gx_types.Part):
                converted.append(part)
                continue
            if isinstance(part, dict):
                if 'text' in part:
                    text_val = part.get('text')
                    if text_val is None:
                        continue
                    converted.append(gx_types.Part(text=str(text_val)))
                elif 'inline_data' in part:
                    data = part['inline_data'] or {}
                    converted.append(gx_types.Part(inline_data=gx_types.Blob(
                        mime_type=data.get('mime_type'),
                        data=data.get('data'),
                    )))
                else:
                    converted.append(gx_types.Part(text=str(part)))
            else:
                converted.append(gx_types.Part(text=str(part)))
        return converted

    def _is_system_message(self, msg, system_prompt):
        if not system_prompt:
            return False
        parts = msg.get('parts') or []
        if len(parts) != 1:
            return False
        part = parts[0]
        if isinstance(part, dict) and 'text' in part:
            return str(part['text']) == system_prompt
        if isinstance(part, gx_types.Part) and getattr(part, 'text', None):
            return part.text == system_prompt
        return False

    def _build_tool_response_payload(self, msg):
        text = self._extract_text_from_parts(msg.get('parts') or [])
        if not text:
            text = msg.get('raw_message') or ''
        text = text or ''
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {'output': text}

    def _extract_text_from_parts(self, parts):
        chunks = []
        for part in parts:
            if isinstance(part, dict) and 'text' in part and part['text']:
                chunks.append(str(part['text']))
            elif isinstance(part, gx_types.Part) and getattr(part, 'text', None):
                chunks.append(part.text)
        return '\n'.join(chunks)

    def chat(self):
        """Handle non-streaming chat completion requests."""
        try:
            contents, config, api_model = self._prepare_request()
            if not contents:
                return None

            response = self.client.models.generate_content(
                model=api_model,
                contents=contents,
                config=config,
            )
            self._last_response = response

            # Store usage metrics if available
            usage = getattr(response, 'usage_metadata', None)
            if usage is not None:
                prompt_tokens = getattr(usage, 'prompt_token_count', 0) or 0
                completion_tokens = getattr(usage, 'candidates_token_count', 0) or 0
                total_tokens = getattr(usage, 'total_token_count', 0) or 0
                cached_tokens = getattr(usage, 'cached_content_token_count', 0) or 0
                self.turn_usage = {
                    'prompt_tokens': prompt_tokens,
                    'completion_tokens': completion_tokens,
                    'total_tokens': total_tokens,
                    'cached_tokens': cached_tokens,
                }
                self.total_usage['prompt_tokens'] += prompt_tokens
                self.total_usage['completion_tokens'] += completion_tokens
                self.total_usage['total_tokens'] += total_tokens
                self.total_usage['cached_tokens'] += cached_tokens

            # If the model responded with function calls, avoid the `.text`
            # accessor entirely so we don't trigger warnings about non-text
            # parts; TurnRunner will handle tool execution via get_tool_calls.
            try:
                has_funcs = bool(getattr(response, 'function_calls', None))
            except Exception:
                has_funcs = False
            if has_funcs:
                return ''

            # Otherwise, use the google-genai convenience accessor for text.
            try:
                txt = getattr(response, 'text', None)
            except Exception:
                txt = None
            return txt or ''

        except Exception as e:
            error_msg = f"Error in chat completion: {str(e)}"
            print(error_msg)
            return error_msg

    def stream_chat(self):
        """Stream chat responses and capture final usage stats"""
        try:
            contents, config, api_model = self._prepare_request()
            if not contents:
                return

            response_stream = self.client.models.generate_content_stream(
                model=api_model,
                contents=contents,
                config=config,
            )

            final_chunk = None
            tool_chunk = None
            for chunk in response_stream:
                # For chunks that contain function calls, capture them for tool
                # execution and skip text extraction to avoid warnings.
                try:
                    has_funcs = bool(getattr(chunk, 'function_calls', None))
                except Exception:
                    has_funcs = False

                if has_funcs:
                    tool_chunk = chunk
                else:
                    try:
                        txt = getattr(chunk, 'text', None)
                    except Exception:
                        txt = None
                    if txt:
                        yield txt

                final_chunk = chunk

            # Capture usage from final chunk
            target_chunk = tool_chunk or final_chunk
            if target_chunk and hasattr(target_chunk, 'usage_metadata'):
                usage = target_chunk.usage_metadata
                prompt_tokens = getattr(usage, 'prompt_token_count', 0) or 0
                completion_tokens = getattr(usage, 'candidates_token_count', 0) or 0
                total_tokens = getattr(usage, 'total_token_count', 0) or 0
                cached_tokens = getattr(usage, 'cached_content_token_count', 0) or 0
                self.turn_usage = {
                    'prompt_tokens': prompt_tokens,
                    'completion_tokens': completion_tokens,
                    'total_tokens': total_tokens,
                    'cached_tokens': cached_tokens,
                }
                # Update total usage
                self.total_usage['prompt_tokens'] += prompt_tokens
                self.total_usage['completion_tokens'] += completion_tokens
                self.total_usage['total_tokens'] += total_tokens
                self.total_usage['cached_tokens'] += cached_tokens
                # Store last response for tool-call extraction (prefer the
                # chunk that actually contains function calls when present).
                self._last_response = tool_chunk or final_chunk

        except Exception as e:
            # Suppress noisy conversion errors (e.g., function_call parts not convertible to text)
            msg = str(e)
            if 'Could not convert' in msg and 'function_call' in msg:
                return
            yield f"Stream error: {msg}"

    def assemble_message(self) -> list:
        """Assemble the message from context"""
        message = []
        if self.session.get_context('prompt'):
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
                    entry = {
                        'role': turn['role'],
                        'parts': parts,
                        'raw_message': turn.get('message'),
                    }
                    if 'tool_call_id' in turn:
                        entry['tool_call_id'] = turn.get('tool_call_id')
                    if 'tool_calls' in turn:
                        entry['tool_calls'] = turn.get('tool_calls')
                    message.append(entry)
        return message

    def get_full_response(self):
        """Returns the full response object from the last API call"""
        return self._last_response

    def get_messages(self):
        """Return assembled messages"""
        return self.assemble_message()

    def get_tool_calls(self):
        """Return normalized tool calls from the last response, if present.

        Shape: [{"id": str|None, "name": str, "arguments": dict}]
        """
        resp = self._last_response
        out = []
        if not resp:
            return out
        generated = []
        try:
            # Prefer candidates -> content.parts[*].function_call
            candidates = getattr(resp, 'candidates', None)
            if candidates:
                for cand in candidates:
                    content = getattr(cand, 'content', None)
                    parts = getattr(content, 'parts', None) if content else None
                    if parts:
                        for p in parts:
                            fc = getattr(p, 'function_call', None) if hasattr(p, 'function_call') else (
                                p.get('function_call') if isinstance(p, dict) else None)
                            if fc:
                                name = getattr(fc, 'name', None) if hasattr(fc, 'name') else fc.get('name')
                                args = getattr(fc, 'args', None) if hasattr(fc, 'args') else (fc.get('args') or {})
                                if name:
                                    n = str(name).strip().lower()
                                    # Map API-safe tool names back to canonical names when available
                                    try:
                                        mapping = self.session.get_user_data('__tool_api_to_cmd__') or {}
                                        if isinstance(mapping, dict) and n in mapping:
                                            n = mapping.get(n, n)
                                    except Exception:
                                        pass
                                    call_id = getattr(fc, 'id', None) if hasattr(fc, 'id') else (
                                        fc.get('id') if isinstance(fc, dict) else None
                                    )
                                    if not call_id:
                                        call_id = f"google-func-{len(out) + 1}"
                                    record = {'id': call_id, 'name': n, 'arguments': args or {}}
                                    out.append(record)
                                    generated.append(record)
                if out:
                    return out
            # Some SDKs expose response.function_calls
            fcs = getattr(resp, 'function_calls', None)
            if isinstance(fcs, list):
                for fc in fcs:
                    name = getattr(fc, 'name', None) if hasattr(fc, 'name') else (fc.get('name') if isinstance(fc, dict) else None)
                    args = getattr(fc, 'args', None) if hasattr(fc, 'args') else (fc.get('args') if isinstance(fc, dict) else {})
                    if name:
                        n = str(name).strip().lower()
                        try:
                            mapping = self.session.get_user_data('__tool_api_to_cmd__') or {}
                            if isinstance(mapping, dict) and n in mapping:
                                n = mapping.get(n, n)
                        except Exception:
                            pass
                        call_id = getattr(fc, 'id', None) if hasattr(fc, 'id') else (
                            fc.get('id') if isinstance(fc, dict) else None
                        )
                        if not call_id:
                            call_id = f"google-func-{len(out) + 1}"
                        record = {'id': call_id, 'name': n, 'arguments': args or {}}
                        out.append(record)
                        generated.append(record)
            return out
        except Exception:
            return []

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
            params = self.session.get_params()
            
            price_unit = float(params.get('price_unit', 1000000))
            price_in = float(params.get('price_in', 0))
            price_out = float(params.get('price_out', 0))

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
        """Clean up resources (placeholder for compatibility)."""
        # No explicit resources to clean up with the current google-genai client.
        return None

    def __del__(self):
        """Attempt cleanup on deletion"""
        try:
            self.cleanup()
        except Exception:
            pass
