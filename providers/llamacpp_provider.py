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

        # If official tools are enabled, some llama.cpp builds may not stream tool_calls; fallback to non-stream
        try:
            if bool(self.session.get_option('TOOLS', 'use_official_tools', fallback=False)):
                text = self.chat()
                if isinstance(text, str):
                    yield text
                    return
        except Exception:
            pass

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

        completion_str = ""
        start_time = time()

        # Stream the response tokens
        for chunk in response:
            if 'choices' in chunk and len(chunk['choices']) > 0:
                choice = chunk['choices'][0]
                # Most llama.cpp streams use OpenAI-like delta
                content = choice.get('delta', {}).get('content')
                if content:
                    completion_str += content
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
        # Print the tokens per second
        # print()
        # print(f"TPS: {total_token_count / self.running_usage['total_time']}")

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
            for turn in chat.get():
                role = turn.get('role')
                # Assistant tool calls (from runner) → include tool_calls in assistant message
                if role == 'assistant' and 'tool_calls' in turn:
                    message.append({
                        'role': 'assistant',
                        'content': None,
                        'tool_calls': [
                            {
                                'id': tc.get('id'),
                                'type': 'function',
                                'function': {
                                    'name': tc.get('name'),
                                    'arguments': __import__('json').dumps(tc.get('arguments') or {})
                                }
                            } for tc in (turn.get('tool_calls') or [])
                        ]
                    })
                    continue

                # Tool results → role 'tool' with tool_call_id
                if role == 'tool':
                    tool_call_id = turn.get('tool_call_id') or turn.get('id')
                    message.append({
                        'role': 'tool',
                        'content': turn.get('message') or '',
                        'tool_call_id': tool_call_id
                    })
                    continue

                turn_context = ''
                if 'context' in turn and turn['context']:
                    turn_context = self.session.get_action('process_contexts').process_contexts_for_assistant(turn['context'])
                message.append({'role': role, 'content': (turn_context + "\n" + (turn.get('message') or '')).strip()})

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
