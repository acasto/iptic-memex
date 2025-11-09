"""Moonshot provider with reasoning_content support."""

from __future__ import annotations

import json
import time
from typing import Any, Iterator, List, Dict

from providers.openai_provider import OpenAIProvider


class MoonshotProvider(OpenAIProvider):
    """Moonshot AI provider that preserves kimi-k2-thinking reasoning traces."""

    def __init__(self, session):
        super().__init__(session)
        self._current_reasoning_content: str = ""

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    def get_current_reasoning(self) -> str:
        """Return the reasoning collected from the most recent response."""

        return self._current_reasoning_content or ""

    # ------------------------------------------------------------------
    # Chat overrides
    # ------------------------------------------------------------------
    def chat(self):  # type: ignore[override]
        """Run a non-streaming chat call and capture reasoning_content."""

        self._reset_reasoning()
        response = super().chat()
        self._capture_reasoning_from_last_response()
        return response

    def stream_chat(self) -> Iterator[str]:  # type: ignore[override]
        """Stream Moonshot responses, capturing reasoning deltas internally."""

        self._reset_reasoning()
        self._last_stream_tool_calls = None

        # Call the base OpenAI provider directly to avoid re-entering our chat()
        response = OpenAIProvider.chat(self)
        start_time = time.time()

        if isinstance(response, str):
            # Errors come back as strings – surface them unchanged
            yield response
            return

        if response is None:
            return

        tool_calls_map: Dict[int | None, Dict[str, Any]] = {}

        try:
            for chunk in response:
                choice = chunk.choices[0] if chunk.choices else None
                delta = getattr(choice, "delta", None)
                if delta is not None:
                    reasoning_delta = getattr(delta, "reasoning_content", None)
                    if reasoning_delta:
                        self._current_reasoning_content += reasoning_delta

                    content = getattr(delta, "content", None)
                    if content:
                        yield content

                    tool_calls = getattr(delta, "tool_calls", None)
                    if tool_calls:
                        self._collect_tool_call_deltas(tool_calls, tool_calls_map)

                # Mirror the base usage accounting logic
                if getattr(chunk, "usage", None):
                    self._update_stream_usage(chunk)

        except Exception as exc:  # pragma: no cover - defensive
            error_msg = "Stream interrupted:\n"
            if hasattr(exc, "status_code"):
                error_msg += f"Status code: {exc.status_code}\n"
            if hasattr(exc, "response"):
                error_msg += f"Response: {getattr(exc, 'response', None)}\n"
            error_msg += f"Error details: {exc}"
            yield error_msg

        finally:
            self.running_usage['total_time'] += time.time() - start_time
            self._finalize_stream_tool_calls(tool_calls_map)

    # ------------------------------------------------------------------
    # Message assembly
    # ------------------------------------------------------------------
    def assemble_message(self) -> list:
        """Attach reasoning_content from chat history before dispatch."""

        messages = super().assemble_message()
        chat_ctx = self.session.get_context('chat')
        if not chat_ctx:
            return messages

        conversation = chat_ctx.get()
        msg_index = 0
        for turn in conversation:
            if turn.get('role') != 'assistant':
                continue

            while msg_index < len(messages) and messages[msg_index].get('role') != 'assistant':
                msg_index += 1

            if msg_index >= len(messages):
                break

            reasoning = turn.get('reasoning_content')
            if reasoning:
                messages[msg_index]['reasoning_content'] = reasoning
            msg_index += 1

        return messages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _reset_reasoning(self) -> None:
        self._current_reasoning_content = ""

    def _capture_reasoning_from_last_response(self) -> None:
        response = getattr(self, '_last_response', None)
        if not response or not getattr(response, 'choices', None):
            return

        first_choice = response.choices[0]
        message = getattr(first_choice, 'message', None)
        if message is None:
            return

        reasoning = getattr(message, 'reasoning_content', None)
        if reasoning:
            self._current_reasoning_content = reasoning

    def _collect_tool_call_deltas(self, tool_calls: List[Any], tool_calls_map: Dict[int | None, Dict[str, Any]]):
        for tc in tool_calls:
            idx = getattr(tc, 'index', None)
            fn = getattr(tc, 'function', None)
            name = getattr(fn, 'name', None) if fn else None
            args_chunk = getattr(fn, 'arguments', None) if fn else None

            record = tool_calls_map.get(idx) or {'id': getattr(tc, 'id', None), 'name': None, 'arguments': ''}
            if name:
                record['name'] = name
            if args_chunk:
                try:
                    record['arguments'] = (record.get('arguments') or '') + str(args_chunk)
                except Exception:
                    pass
            tool_calls_map[idx] = record

    def _finalize_stream_tool_calls(self, tool_calls_map: Dict[int | None, Dict[str, Any]]):
        if not tool_calls_map:
            self._last_stream_tool_calls = None
            return

        out = []
        for idx, rec in sorted(tool_calls_map.items(), key=lambda item: (item[0] if item[0] is not None else 0)):
            args_obj = {}
            args_str = rec.get('arguments') or ''
            if args_str:
                try:
                    args_obj = json.loads(args_str)
                except Exception:
                    args_obj = {}
            out.append({'id': rec.get('id'), 'name': rec.get('name'), 'arguments': args_obj})
        self._last_stream_tool_calls = out

    def _update_stream_usage(self, chunk: Any) -> None:
        usage = getattr(chunk, 'usage', None)
        if not usage:
            return

        self.turn_usage = usage
        self.running_usage['total_in'] += getattr(usage, 'prompt_tokens', 0)
        self.running_usage['total_out'] += getattr(usage, 'completion_tokens', 0)

        prompt_details = getattr(usage, 'prompt_tokens_details', None)
        cached_tokens = None
        if isinstance(prompt_details, dict):
            cached_tokens = prompt_details.get('cached_tokens')
        elif hasattr(prompt_details, 'cached_tokens'):
            cached_tokens = prompt_details.cached_tokens
        if cached_tokens:
            self.running_usage['cached_tokens'] = self.running_usage.get('cached_tokens', 0) + cached_tokens

        completion_details = getattr(usage, 'completion_tokens_details', None)
        if isinstance(completion_details, dict):
            reasoning_tokens = completion_details.get('reasoning_tokens', 0)
            accepted = completion_details.get('accepted_prediction_tokens', 0)
            rejected = completion_details.get('rejected_prediction_tokens', 0)
        else:
            reasoning_tokens = getattr(completion_details, 'reasoning_tokens', 0)
            accepted = getattr(completion_details, 'accepted_prediction_tokens', 0)
            rejected = getattr(completion_details, 'rejected_prediction_tokens', 0)

        if reasoning_tokens:
            self.running_usage['reasoning_tokens'] = self.running_usage.get('reasoning_tokens', 0) + reasoning_tokens
        if accepted:
            self.running_usage['accepted_prediction_tokens'] = self.running_usage.get('accepted_prediction_tokens', 0) + accepted
        if rejected:
            self.running_usage['rejected_prediction_tokens'] = self.running_usage.get('rejected_prediction_tokens', 0) + rejected
