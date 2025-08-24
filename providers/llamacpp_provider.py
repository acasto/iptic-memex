import traceback
from time import time
from base_classes import APIProvider
from llama_cpp import Llama
from llama_cpp.llama_speculative import LlamaPromptLookupDecoding
import io
from contextlib import redirect_stderr
from providers.llamacpp_draft_model import LlamaSmallModelDraft
from providers.llamacpp_draft_model import LlamaSmallModelDraftWithMetrics
import os
from typing import List, Optional


class LlamaCppProvider(APIProvider):
    """
    llama.cpp Python bindings provider
    """

    def __init__(self, session):
        self.session = session
        self.last_api_param = None
        self._last_response = None
        # Embedding instance cache (separate from chat llm when needed)
        self._embed_llm = None
        self._embed_model_path = None

        # Defer chat model initialization until first chat() call
        self.llm = None
        self._chat_model_path = None

        # Capture llama.cpp params; resolve upon first use
        params = self.session.get_params()
        self._n_ctx = params.get('context_size', 2048)
        self._n_gpu_layers = int(params.get('n_gpu_layers', -1))
        self._verbose = params.get('verbose', False)

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

        self._logits_all = logits_all

        # Parameters we might want to map from self.params to llama_cpp.create_chat_completion()
        # Unlike OpenAI, llama-cpp-python tries to support a similar interface, so most should just pass through.
        self.parameters = [
            'model',  # Typically not needed since we set model_path at init
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
            # NOTE: llama.cpp provider in this project does not support
            # official tool calling. We intentionally do NOT pass
            # 'tools' / 'tool_choice' through to llama_cpp to avoid
            # template errors in certain chat formats (e.g., Qwen),
            # where the Jinja chat template expects an iterable.
            # See: repo guidelines "Local Backends (llama.cpp)".
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

            # Drop OpenAI-style tools for llama.cpp (unsupported here).
            # Passing a bare boolean (e.g., tools=True) causes some
            # chat templates to iterate a bool and crash.
            # current_params may carry global tool flags; ignore for llama.cpp
            # Ensure these never leak into llama_cpp kwargs
            api_parms.pop('tools', None)
            api_parms.pop('tool_choice', None)

            # If streaming set stream_options - we could set this in the config, but since it's dependent
            # on stream and enables internal feature, we'll set it here
            # if 'stream' in api_parms and api_parms['stream'] is True:
            #    if 'stream_options' not in current_params or current_params['stream_options'] is not False:
            #        api_parms['stream_options'] = {
            #            'include_usage': True,
            #        }

            # Ensure chat model is initialized lazily now that we're about to chat
            self._get_chat_llm()

            # llama-cpp-python expects messages and supports similar arguments
            api_parms['messages'] = messages

            # Save for debugging
            self.last_api_param = api_parms

            # Call llama-cpp create_chat_completion
            # Note: create_chat_completion returns a dict like OpenAI API
            response = self.llm.create_chat_completion(**api_parms)
            self._last_response = None

            # if in stream mode chain the generator, else return the text response
            if 'stream' in api_parms and api_parms['stream'] is True:
                return response
            else:
                if response['usage'] is not None:
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

        response = self.chat()
        if response is None:
            return

        # The prompt messages should be in self.last_api_param['messages']
        messages = self.last_api_param.get('messages', [])
        prompt_str = ""
        for msg in messages:
            prompt_str += msg['content']

        # Tokenize the prompt (must encode to bytes)
        # Ensure tokenizer available (chat llm initialized by chat())
        prompt_tokens = self.llm.tokenize(prompt_str.encode('utf-8'), add_bos=True)
        prompt_token_count = len(prompt_tokens)

        completion_str = ""
        start_time = time()

        # Stream the response tokens
        for chunk in response:
            if 'choices' in chunk and len(chunk['choices']) > 0:
                choice = chunk['choices'][0]
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

    # --- Embeddings support ---
    def _get_chat_llm(self) -> Llama:
        """Lazy-initialize the chat-capable Llama instance."""
        if self.llm is not None:
            return self.llm
        params = self.session.get_params()
        model_path = params.get('model_path')
        if not model_path or not os.path.exists(os.path.expanduser(model_path)):
            raise RuntimeError(f"Model path does not exist: {model_path}")
        f = io.StringIO()
        with redirect_stderr(f):
            self.llm = Llama(
                model_path=model_path,
                n_ctx=self._n_ctx,
                embedding=False,
                n_gpu_layers=self._n_gpu_layers,
                draft_model=self.draft_model,
                logits_all=self._logits_all,
                use_mlock=False,
                flash_attn=True,
                verbose=self._verbose,
            )
        self._chat_model_path = model_path
        return self.llm
    def _get_embed_llm(self, model_path: Optional[str] = None) -> Llama:
        """Get or create a llama.cpp instance configured for embeddings.

        If `model_path` is provided, it will be used; otherwise falls back to the
        configured `model_path` in params. Caches a dedicated embedding instance
        keyed by model path.
        """
        # Resolve desired path: explicit override or default from params
        path = model_path
        if not path:
            params = self.session.get_params()
            path = params.get('model_path')

        # Reuse cached embed model if path matches
        if self._embed_llm is not None and self._embed_model_path == path:
            return self._embed_llm

        # Build a fresh embedding-capable instance
        params = self.session.get_params()
        n_ctx = params.get('context_size', 2048)
        n_gpu_layers = int(params.get('n_gpu_layers', -1))
        verbose = params.get('verbose', False)

        f = io.StringIO()
        with redirect_stderr(f):
            self._embed_llm = Llama(
                model_path=path,
                n_ctx=n_ctx,
                embedding=True,
                n_gpu_layers=n_gpu_layers,
                use_mlock=False,
                flash_attn=True,
                verbose=verbose,
            )
        self._embed_model_path = path
        return self._embed_llm

    def embed(self, texts: List[str], model: Optional[str] = None) -> List[List[float]]:
        """Create embeddings using llama.cpp locally.

        Robust to models that only expose token-level embeddings by pooling.
        Processes inputs one-by-one to avoid batch decode edge cases.
        """
        # Coerce single string inputs defensively
        if isinstance(texts, str):
            texts = [texts]

        # Decide model path
        model_path: Optional[str] = None
        if model and (os.path.exists(os.path.expanduser(model)) or str(model).lower().endswith('.gguf')):
            model_path = os.path.expanduser(model)
        else:
            params = self.session.get_params()
            model_path = params.get('model_path')

        llm = self._get_embed_llm(model_path)

        def _pool(vec_or_token_vecs):
            # If already a single vector [dim], return as floats
            if isinstance(vec_or_token_vecs, list) and vec_or_token_vecs and isinstance(vec_or_token_vecs[0], (int, float)):
                return [float(x) for x in vec_or_token_vecs]
            # If token-level [[dim], ...], mean-pool
            if isinstance(vec_or_token_vecs, list) and vec_or_token_vecs and isinstance(vec_or_token_vecs[0], list):
                # mean over tokens
                token_vecs = vec_or_token_vecs
                if not token_vecs:
                    return []
                dim = len(token_vecs[0])
                agg = [0.0] * dim
                n = 0
                for tv in token_vecs:
                    # guard ragged output
                    if len(tv) != dim:
                        dim = min(dim, len(tv))
                        tv = tv[:dim]
                        agg = agg[:dim]
                    for i, val in enumerate(tv):
                        agg[i] += float(val)
                    n += 1
                if n == 0:
                    return []
                return [v / n for v in agg]
            return []

        out: List[List[float]] = []
        for t in texts:
            vec = None
            # Prefer llama.cpp embed() per-item
            try:
                if hasattr(llm, 'embed'):
                    buf = io.StringIO()
                    with redirect_stderr(buf):
                        res = llm.embed(t, truncate=True)
                    vec = _pool(res)
            except Exception:
                vec = None
            # Fallback to create_embedding per-item
            if not vec:
                try:
                    buf = io.StringIO()
                    with redirect_stderr(buf):
                        resp = llm.create_embedding(t)
                    if isinstance(resp, dict) and 'data' in resp:
                        data = resp.get('data') or []
                        if data:
                            vec = [float(x) for x in (data[0].get('embedding') or [])]
                    else:
                        vec = _pool(resp)
                except Exception:
                    vec = None
            if not vec:
                raise RuntimeError('Failed to compute embedding with llama.cpp')
            out.append(vec)
        return out

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
                turn_context = ''
                if 'context' in turn and turn['context']:
                    turn_context = self.session.get_action('process_contexts').process_contexts_for_assistant(
                        turn['context'])
                message.append({'role': turn['role'], 'content': turn_context + "\n" + turn['message']})

        return message

    def get_messages(self):
        return self.assemble_message()

    def get_full_response(self):
        """Returns the full response object from the last API call"""
        return self._last_response

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
