import traceback
from time import time
from base_classes import APIProvider
from llama_cpp import Llama
from llama_cpp.llama_speculative import LlamaPromptLookupDecoding
import io
from contextlib import redirect_stderr
from providers.llamacpp_draft_model import LlamaSmallModelDraft
from providers.llamacpp_draft_model import LlamaSmallModelDraftWithMetrics


class LlamaCppProvider(APIProvider):
    """
    llama.cpp Python bindings provider
    """

    def __init__(self, session):
        self.session = session
        self.last_api_param = None
        self._last_response = None
        # Capture normalized tool calls detected from textual output
        self._last_stream_tool_calls = None
        self._last_textual_tool_calls = None

        # Get fresh params for initialization
        params = self.session.get_params()

        # Extract or set defaults for llama-cpp
        model_path = params.get('model_path', './models/7B/llama-model.gguf')
        # Example: you can allow specifying n_ctx or other llama-specific params:
        n_ctx = params.get('context_size', 2048)
        embedding = params.get('embedding', False)
        n_gpu_layers = int(params.get('n_gpu_layers', -1))
        verbose = params.get('verbose', False)

        # If you want to enable speculative decoding when requested:
        self.draft_model = None
        logits_all = False
        if params.get('speculative', False) == "draft":
            print("Using draft model for speculative decoding")
            self.draft_model = LlamaSmallModelDraftWithMetrics(
                # model_path="/Users/adam/LLM/models/draft/Llama-3.2-3B-Instruct-Q5_K_M.gguf",
                model_path="/Users/adam/LLM/models/draft/Llama-3.2-1B-Instruct-Q5_K_M.gguf",
                num_draft_tokens=params.get('draft', 10),
                temperature=0.2
            )
            logits_all = True
        if params.get('speculative', False) == "prompt":
            print("Using prompt lookup decoding")
            self.draft_model = LlamaPromptLookupDecoding(
                max_ngram_size=5,
                num_pred_tokens=params.get('draft', 10),
            )
            logits_all = True

        # Initialize the Llama model
        f = io.StringIO()
        with redirect_stderr(f):
            self.llm = Llama(
                model_path=model_path,
                n_ctx=n_ctx,
                embedding=embedding,
                n_gpu_layers=n_gpu_layers,
                draft_model=self.draft_model,
                logits_all=logits_all,
                use_mlock=False,
                flash_attn=True,
                # type_k=6,
                # type_v=6,  # not supported yet in upstream
                verbose=verbose
            )

        # Parameters we might want to map from self.params to llama_cpp.create_chat_completion()
        # Unlike OpenAI, llama-cpp-python tries to support a similar interface, so most should just pass through.
        self.parameters = [
            'model',          # Typically not needed since we set model_path at init
            'messages',
            'max_tokens',
            'frequency_penalty',
            'logit_bias',
            'logprobs',
            'n',
            'presence_penalty',
            'response_format',
            'seed',
            'stop',
            'stream',
            'temperature',
            'top_p',
            'tools',          # If using function calling / tools
            'tool_choice',    # If using function calling / tools
            'user'
        ]

        # Track usage similar to OpenAI
        self.turn_usage = None
        self.running_usage = {
            'total_in': 0,
            'total_out': 0,
            'total_time': 0.0
        }

    def chat(self):
        """
        Creates a chat completion using llama-cpp-python
        """
        start_time = time()
        try:
            # Get fresh params each time
            current_params = self.session.get_params()
            
            # Assemble messages from session
            messages = self.assemble_message()
            api_parms = {}

            # Map parameters from current_params to the call
            for parameter in self.parameters:
                if parameter in current_params and current_params[parameter] is not None:
                    # Handle stream parameter specially - only include if True
                    if parameter == 'stream':
                        if current_params[parameter] is True:
                            api_parms[parameter] = True
                    else:
                        api_parms[parameter] = current_params[parameter]

            # If streaming set stream_options - we could set this in the config, but since it's dependent
            # on stream and enables internal feature, we'll set it here
            # if 'stream' in api_parms and api_parms['stream'] is True:
            #    if 'stream_options' not in current_params or current_params['stream_options'] is not False:
            #        api_parms['stream_options'] = {
            #            'include_usage': True,
            #        }

            # Attach official tools if enabled
            try:
                if bool(self.session.get_option('TOOLS', 'use_official_tools', fallback=False)):
                    tools_spec = self.get_tools_for_request() or []
                    if tools_spec:
                        # Primary OpenAI-style tools
                        api_parms['tools'] = tools_spec
                        if current_params.get('tool_choice') is not None:
                            api_parms['tool_choice'] = current_params.get('tool_choice')

                        # Compatibility fallback for llama.cpp builds that expect legacy functions/function_call
                        try:
                            functions = []
                            for t in tools_spec:
                                fn = t.get('function') or {}
                                if fn:
                                    functions.append({
                                        'name': fn.get('name'),
                                        'description': fn.get('description'),
                                        'parameters': fn.get('parameters'),
                                    })
                            if functions:
                                api_parms['functions'] = functions
                                # If tool_choice was not specified, hint legacy API to call functions automatically
                                if 'tool_choice' not in api_parms and 'function_call' not in api_parms:
                                    api_parms['function_call'] = 'auto'
                        except Exception:
                            pass
            except Exception:
                pass

            # llama-cpp-python expects messages and supports similar arguments
            api_parms['messages'] = messages

            # Save for debugging
            self.last_api_param = api_parms

            # Call llama-cpp create_chat_completion
            # Note: create_chat_completion returns a dict like OpenAI API
            response = self.llm.create_chat_completion(**api_parms)
            self._last_response = response

            # if in stream mode chain the generator, else return the text response
            if 'stream' in api_parms and api_parms['stream'] is True:
                return response
            else:
                if response.get('usage') is not None:
                    self.turn_usage = response['usage']
                if self.turn_usage:
                    self.running_usage['total_in'] += self.turn_usage['prompt_tokens']
                    self.running_usage['total_out'] += self.turn_usage['completion_tokens']
                # Detect textual tool calls when official tools are enabled but
                # backend did not emit native tool_calls/function_call.
                try:
                    if bool(self.session.get_option('TOOLS', 'use_official_tools', fallback=False)):
                        # If the message already contains native tool calls, return normally
                        msg = (response.get('choices') or [{}])[0].get('message') or {}
                        if not (msg.get('tool_calls') or msg.get('function_call')):
                            content = msg.get('content') or ''
                            calls = self._detect_and_parse_textual_tool_calls(content)
                            if calls:
                                # Store for TurnRunner via get_tool_calls() and hide content
                                self._last_textual_tool_calls = calls
                                return ''
                except Exception:
                    # Fall back to returning content if detection fails
                    pass
                return response['choices'][0]['message']['content']

        except Exception as e:
            print("An exception occurred in llama-cpp provider:")
            traceback.print_exc()
            if self.last_api_param is not None:
                print("Last API call parameters:")
                for key, value in self.last_api_param.items():
                    print(f"\t{key}: {value}")
        finally:
            self.running_usage['total_time'] += time() - start_time

    def stream_chat(self):
        """
        Stream chat responses while manually computing usage statistics.
        Since llama-cpp-python doesn't currently return streaming usage stats,
        we will:
        - Extract prompt text from api_params['messages']
        - Tokenize the prompt to count prompt tokens
        - As we stream completion tokens, accumulate them in a buffer
        - After streaming completes, tokenize the generated text to count completion tokens
        - Populate self.turn_usage with the computed stats
        """

        response = self.chat()
        if response is None:
            return

        # The prompt messages should be in self.last_api_param['messages']
        messages = self.last_api_param.get('messages', [])
        prompt_str = ""
        for msg in messages:
            prompt_str += msg['content']

        # Tokenize the prompt (must encode to bytes)
        prompt_tokens = self.llm.tokenize(prompt_str.encode('utf-8'), add_bos=True)
        prompt_token_count = len(prompt_tokens)

        # If official tools are enabled, use a shared detector to handle streaming
        try:
            if bool(self.session.get_option('TOOLS', 'use_official_tools', fallback=False)):
                from utils.stream_tool_call_detector import StreamToolCallDetector
                detector = StreamToolCallDetector(prefix_buffer_size=64)
                completion_str = ""
                start_time = time()
                for chunk in response:
                    try:
                        visible_parts = detector.on_delta(chunk) or []
                        for part in visible_parts:
                            if part:
                                # Yield visible text to the caller (AssistantOutputAction handles printing)
                                yield part
                                completion_str += part
                    except Exception:
                        # Fallback: yield raw content if detector fails
                        try:
                            delta = (chunk.get('choices') or [{}])[0].get('delta', {})
                            content = delta.get('content')
                            if content:
                                yield content
                                completion_str += content
                        except Exception:
                            pass

                # Finalize and capture tool calls
                calls = []
                try:
                    calls = detector.finalize() or []
                except Exception:
                    calls = []
                if calls:
                    self._last_stream_tool_calls = calls
                else:
                    # If it was a tool turn but parsing failed, emit fallback visible text
                    try:
                        fb = getattr(detector, 'fallback_visible', '')
                        if getattr(detector, 'tool_mode', False) and fb:
                            yield fb
                            completion_str += fb
                    except Exception:
                        pass

                # Compute usage
                completion_tokens = self.llm.tokenize(completion_str.encode('utf-8'), add_bos=False)
                completion_token_count = len(completion_tokens)
                total_token_count = prompt_token_count + completion_token_count
                self.turn_usage = {
                    'prompt_tokens': prompt_token_count,
                    'completion_tokens': completion_token_count,
                    'total_tokens': total_token_count
                }
                self.running_usage['total_in'] += prompt_token_count
                self.running_usage['total_out'] += completion_token_count
                self.running_usage['total_time'] += time() - start_time
                return
        except Exception:
            # If any error occurs, fall through to the legacy streaming path below
            pass

        completion_str = ""
        # Streaming textual tool-call detection buffer (enabled when official tools are on)
        detect_official = False
        try:
            detect_official = bool(self.session.get_option('TOOLS', 'use_official_tools', fallback=False))
        except Exception:
            detect_official = False
        buffer = []
        decided = False
        tool_mode = False  # True when we suppress output due to tool call (textual or native)
        # Native function_call (OpenAI-legacy) accumulation during streaming
        fc_name = None
        fc_args_buf = []
        # OpenAI-style tool_calls accumulation (by index)
        tc_map = {}
        # Maximum chars to buffer before deciding (performance guard)
        MAX_PREFIX = 256
        start_time = time()

        # Stream the response tokens
        for chunk in response:
            if 'choices' in chunk and len(chunk['choices']) > 0:
                choice = chunk['choices'][0]
                delta = choice.get('delta', {})
                # Detect native function_call deltas (llama.cpp often emits these)
                try:
                    fc = delta.get('function_call')
                    if isinstance(fc, dict) and (fc.get('name') is not None or fc.get('arguments') is not None):
                        if fc.get('name'):
                            fc_name = fc.get('name')
                        if fc.get('arguments'):
                            # Append incremental JSON string fragments
                            try:
                                fc_args_buf.append(str(fc.get('arguments')))
                            except Exception:
                                pass
                        tool_mode = True
                        decided = True
                        # Do not yield function_call JSON
                        continue
                except Exception:
                    pass

                # Detect OpenAI-style tool_calls deltas
                try:
                    tool_calls = delta.get('tool_calls')
                    if tool_calls:
                        for tc in tool_calls:
                            idx = tc.get('index')
                            fn = tc.get('function') or {}
                            name = fn.get('name')
                            args_part = fn.get('arguments')
                            rec = tc_map.get(idx) or {'id': tc.get('id'), 'name': None, 'arguments': ''}
                            if name:
                                rec['name'] = name
                            if args_part:
                                try:
                                    rec['arguments'] = (rec.get('arguments') or '') + str(args_part)
                                except Exception:
                                    pass
                            tc_map[idx] = rec
                        tool_mode = True
                        decided = True
                        continue
                except Exception:
                    pass

                # Most llama.cpp streams use OpenAI-like delta for content
                content = delta.get('content')
                if content:
                    completion_str += content
                    if detect_official and not decided:
                        buffer.append(content)
                        buffered_text = ''.join(buffer)
                        stripped = buffered_text.lstrip()
                        if stripped:
                            first = stripped[0]
                            if first in ('{', '['):
                                # JSON/array → tool-call
                                tool_mode = True
                                decided = True
                                continue
                            if first == '<':
                                tag = '<tool_call'
                                # If we have a full match, it's a tool call
                                if stripped.startswith(tag):
                                    tool_mode = True
                                    decided = True
                                    continue
                                # If current buffer is a prefix of the tag, keep buffering
                                if tag.startswith(stripped):
                                    # wait for more chunks
                                    continue
                                # Otherwise, it's some other tag; flush buffer
                                decided = True
                                tool_mode = False
                                yield buffered_text
                                buffer = []
                                continue
                            # Any other leading char → normal text
                            decided = True
                            tool_mode = False
                            yield buffered_text
                            buffer = []
                            continue
                        # Not yet decided; keep buffering
                        continue
                    # Normal streaming (either not detecting or already decided non-tool)
                    if not tool_mode:
                        yield content

        # Tokenize the completed response (encode to bytes)
        completion_tokens = self.llm.tokenize(completion_str.encode('utf-8'), add_bos=False)
        completion_token_count = len(completion_tokens)

        total_token_count = prompt_token_count + completion_token_count
        
        # Get fresh params to check speculative setting
        current_params = self.session.get_params()
        if current_params.get('speculative', False) == "draft":
            self.draft_model.print_overall_metrics()
            
        # Create a usage dict compatible with what we'd normally expect
        self.turn_usage = {
            'prompt_tokens': prompt_token_count,
            'completion_tokens': completion_token_count,
            'total_tokens': total_token_count
        }

        # Update running usage totals
        self.running_usage['total_in'] += prompt_token_count
        self.running_usage['total_out'] += completion_token_count
        self.running_usage['total_time'] += time() - start_time
        # If we detected a tool-call during streaming, finalize normalized tool_calls
        try:
            if tool_mode:
                calls = []
                # Prefer native function_call aggregation when present
                if fc_name:
                    import json
                    args_str = ''.join(fc_args_buf) if fc_args_buf else ''
                    args_obj = {}
                    if args_str:
                        try:
                            args_obj = json.loads(args_str)
                        except Exception:
                            args_obj = {}
                    calls = [{'id': 'tc_1', 'name': str(fc_name).strip().lower(), 'arguments': args_obj}]
                elif tc_map:
                    import json
                    for _, rec in sorted(tc_map.items(), key=lambda kv: (kv[0] if kv[0] is not None else 0)):
                        args_obj = {}
                        args_str = rec.get('arguments') or ''
                        if args_str:
                            try:
                                args_obj = json.loads(args_str)
                            except Exception:
                                args_obj = {}
                        calls.append({'id': rec.get('id'), 'name': (rec.get('name') or '').strip().lower(), 'arguments': args_obj})
                else:
                    # Fallback to textual detection of the buffered content
                    if detect_official and buffer:
                        full_content = ''.join(buffer)
                        calls = self._detect_and_parse_textual_tool_calls(full_content) or []
                if calls:
                    self._last_stream_tool_calls = calls
                else:
                    # Fallback: no valid calls parsed; emit the accumulated text so the turn isn't blank
                    if completion_str:
                        try:
                            yield completion_str
                        except Exception:
                            pass
                    self._last_stream_tool_calls = []
        except Exception:
            self._last_stream_tool_calls = None

    def assemble_message(self) -> list:
        """
        Assemble messages for llama-cpp API from the session context
        """
        message = []
        if self.session.get_context('prompt'):
            params = self.session.get_params()
            
            # llama-cpp-python currently only supports 'system', 'user', 'assistant' roles
            # We default use_old_system_role=True for compatibility, but users can override
            # when 'developer' role support is added to llama-cpp-python
            use_old_system_role = params.get('use_old_system_role', True)
            
            role = 'system' if use_old_system_role else 'developer'
            
            prompt_content = self.session.get_context('prompt').get()['content']
            if prompt_content.strip() == '':
                prompt_content = ' '  # Handle empty content like other providers
            message.append({'role': role, 'content': prompt_content})

        chat = self.session.get_context('chat')
        if chat is not None:
            # Map tool_call_id -> function name for legacy 'function' role messages
            id_to_name = {}
            for turn in chat.get():
                role = turn.get('role')
                # Assistant tool calls (from runner): capture mapping only; do not
                # append an assistant message with tool_calls. llama.cpp bindings
                # operate best with legacy 'function' role messages that follow.
                if role == 'assistant' and 'tool_calls' in turn:
                    # Build mapping for subsequent tool result turns
                    try:
                        for tc in (turn.get('tool_calls') or []):
                            tid = tc.get('id')
                            tname = tc.get('name')
                            if tid and tname:
                                id_to_name[tid] = tname
                    except Exception:
                        pass
                    continue

                # Tool results → prefer legacy 'function' role for llama.cpp compatibility
                if role == 'tool':
                    tool_call_id = turn.get('tool_call_id') or turn.get('id')
                    # Legacy functions shape for llama.cpp compatibility (primary)
                    try:
                        fn_name = id_to_name.get(tool_call_id) or ''
                        if fn_name:
                            fn_content = turn.get('message')
                            if fn_content is None or str(fn_content).strip() == '':
                                fn_content = ' '
                            message.append({
                                'role': 'function',
                                'name': fn_name,
                                'content': fn_content
                            })
                    except Exception:
                        pass
                    # Do not include 'tool' role message to avoid confusing some llama.cpp builds
                    continue

                turn_context = ''
                if 'context' in turn and turn['context']:
                    turn_context = self.session.get_action('process_contexts').process_contexts_for_assistant(turn['context'])
                # Ensure non-empty content for llama.cpp compatibility; some builds
                # may ignore or short-circuit on empty strings. Use a single space
                # as OpenAI-compatible providers do for blank turns.
                turn_text = (turn_context + "\n" + (turn.get('message') or '')).strip()
                if turn_text == '':
                    turn_text = ' '
                message.append({'role': role, 'content': turn_text})

        # Provider-local compatibility: if the last message is a blank user turn
        # immediately following tool/function results, drop it so the model
        # continues from tool output context (mirrors official tool-calling).
        # Do not mutate trailing user messages here; let TurnRunner manage the
        # synthetic user turn contract consistently across providers.

        return message

    def get_messages(self):
        return self.assemble_message()

    def get_full_response(self):
        """Returns the full response object from the last API call"""
        return self._last_response

    def get_tool_calls(self):
        """Extract tool calls from the last response (OpenAI-compatible shape).

        Supports both tool_calls array and legacy function_call single call.
        """
        # Prefer any tool calls captured during streaming textual detection
        if self._last_stream_tool_calls:
            out_stream = list(self._last_stream_tool_calls)
            # Clear after read to avoid reusing across turns
            self._last_stream_tool_calls = None
            return out_stream
        # Or those captured from non-stream textual detection
        if self._last_textual_tool_calls:
            out_text = list(self._last_textual_tool_calls)
            self._last_textual_tool_calls = None
            return out_text

        resp = self._last_response
        out = []
        try:
            if not resp:
                return out
            choices = resp.get('choices') or []
            if not choices:
                return out
            msg = choices[0].get('message') or {}
            # Preferred: tool_calls
            tcs = msg.get('tool_calls')
            if tcs:
                import json
                for tc in tcs:
                    fn = (tc.get('function') or {})
                    args = fn.get('arguments')
                    if isinstance(args, str):
                        try:
                            args_obj = json.loads(args)
                        except Exception:
                            args_obj = {}
                    elif isinstance(args, dict):
                        args_obj = args
                    else:
                        args_obj = {}
                    out.append({'id': tc.get('id'), 'name': fn.get('name'), 'arguments': args_obj})
                return out
            # Legacy: function_call
            fc = msg.get('function_call')
            if fc:
                import json
                args = fc.get('arguments')
                if isinstance(args, str):
                    try:
                        args_obj = json.loads(args)
                    except Exception:
                        args_obj = {}
                else:
                    args_obj = args or {}
                out.append({'id': None, 'name': fc.get('name'), 'arguments': args_obj})
        except Exception:
            return []
        return out

    # Provider-native tool spec construction
    def get_tools_for_request(self) -> list:
        try:
            from utils.tool_schema import build_official_tool_specs
            return build_official_tool_specs(self.session) or []
        except Exception:
            return []

    def get_usage(self):
        stats = {
            'total_in': self.running_usage['total_in'],
            'total_out': self.running_usage['total_out'],
            'total_tokens': self.running_usage['total_in'] + self.running_usage['total_out'],
            'total_time': self.running_usage['total_time']
        }

        if self.turn_usage:  # Current turn stats
            stats.update({
                'turn_in': self.turn_usage['prompt_tokens'],
                'turn_out': self.turn_usage['completion_tokens'],
                'turn_total': self.turn_usage['total_tokens']
            })

        return stats

    def reset_usage(self):
        self.turn_usage = None
        self.running_usage = {
            'total_in': 0,
            'total_out': 0,
            'total_time': 0.0
        }

    def get_cost(self):
        return {
            'total': 0.0,
            'turn': 0.0
        }

    # ----------------- Internal helpers for textual tool-calls -----------------
    def _detect_and_parse_textual_tool_calls(self, text: str):
        """Detect if text looks like a tool-call payload and, if so, parse it.

        Returns a normalized list of {id, name, arguments} or [].
        """
        if not text:
            return []
        s = text.lstrip()
        if not s:
            return []
        try:
            if s.startswith('<tool_call'):
                return self._parse_tool_call_tags(s)
            if s.startswith('{') or s.startswith('['):
                return self._parse_tool_call_json(s)
            # Optionally handle fenced code blocks
            if s.startswith('```'):
                # Strip first fence line
                lines = s.splitlines()
                # Drop leading ```... and trailing ``` if present
                body = '\n'.join(lines[1:])
                if body.endswith('```'):
                    body = body[: -3]
                body = body.strip()
                if body.startswith('{') or body.startswith('['):
                    return self._parse_tool_call_json(body)
        except Exception:
            pass
        return []

    @staticmethod
    def _normalize_call_fields(obj: dict, idx: int) -> dict:
        """Normalize various tool-call shapes into {id, name, arguments}.

        Supports both flat and nested OpenAI-like {function: {name, arguments}} forms.
        """
        # Extract function wrapper if present
        fn = obj.get('function') if isinstance(obj, dict) else None
        # Name candidates from multiple fields
        name = None
        if isinstance(obj, dict):
            name = (
                obj.get('name')
                or obj.get('tool')
                or obj.get('function')  # some models may put name under 'function' directly
                or obj.get('command')
            )
        if not name and isinstance(fn, dict):
            name = fn.get('name')

        # Arguments candidates
        args = None
        # Top-level common fields
        if isinstance(obj, dict):
            args = obj.get('arguments') or obj.get('args') or obj.get('parameters') or obj.get('input')
        # Nested function.arguments
        if args is None and isinstance(fn, dict):
            args = fn.get('arguments')
        # Parse stringified JSON arguments when needed
        if isinstance(args, str):
            try:
                import json as _json
                args_parsed = _json.loads(args)
                args = args_parsed if isinstance(args_parsed, dict) else {'content': args}
            except Exception:
                args = {'content': args}
        # Default when still missing
        if args is None:
            args = {}
        if not isinstance(args, dict):
            args = {'content': str(args)}

        # Final normalization
        call_id = obj.get('id') if isinstance(obj, dict) else None
        if not call_id:
            call_id = f'tc_{idx+1}'
        name_out = str(name or '').strip().lower()
        return {'id': call_id, 'name': name_out, 'arguments': args}

    def _parse_tool_call_json(self, s: str):
        import json
        try:
            data = json.loads(s)
        except Exception:
            return []
        out = []
        if isinstance(data, list):
            for i, item in enumerate(data):
                if isinstance(item, dict):
                    out.append(self._normalize_call_fields(item, i))
        elif isinstance(data, dict):
            out.append(self._normalize_call_fields(data, 0))
        return out

    def _parse_tool_call_tags(self, s: str):
        import re, json
        out = []
        # Capture attributes and inner text for each <tool_call ...> ... </tool_call>
        pattern = re.compile(r'<tool_call([^>]*)>([\s\S]*?)</tool_call>', re.IGNORECASE)
        for i, m in enumerate(pattern.finditer(s)):
            attr = m.group(1) or ''
            body = (m.group(2) or '').strip()
            # Extract name/id attributes if present
            name = None
            call_id = None
            name_m = re.search(r'name\s*=\s*"([^"]+)"', attr)
            if name_m:
                name = name_m.group(1)
            id_m = re.search(r'id\s*=\s*"([^"]+)"', attr)
            if id_m:
                call_id = id_m.group(1)
            # Try JSON body for arguments
            args = {}
            parsed = None
            if body:
                # Strip code fences inside body
                b = body
                if b.startswith('```'):
                    lines = b.splitlines()
                    b = '\n'.join(lines[1:])
                    if b.endswith('```'):
                        b = b[: -3]
                    b = b.strip()
                if b.startswith('{') or b.startswith('['):
                    try:
                        parsed = json.loads(b)
                        if isinstance(parsed, dict):
                            args = parsed.get('arguments') or parsed.get('args') or parsed.get('parameters') or parsed.get('input') or parsed
                        else:
                            # Non-dict at top-level: wrap as content
                            args = {'content': b}
                    except Exception:
                        args = {'content': body}
                else:
                    args = {'content': body}
            # Fallback name from args if missing
            if not name:
                name = (
                    (isinstance(args, dict) and (args.get('name') or args.get('tool') or args.get('function'))) or ''
                )
            # Choose normalization source: if parsed appears to be an OpenAI-like tool_call,
            # pass it through to preserve nested function structure; otherwise use flat shape.
            obj = None
            if isinstance(parsed, dict) and (('function' in parsed) or any(k in parsed for k in ('name', 'tool', 'command'))):
                obj = dict(parsed)
                if call_id and 'id' not in obj:
                    obj['id'] = call_id
            else:
                obj = {'id': call_id, 'name': name, 'arguments': args}
            norm = self._normalize_call_fields(obj, i)
            out.append(norm)
        return out
