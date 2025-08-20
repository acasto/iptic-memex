from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


class StreamToolCallDetector:
    """
    Detects and normalizes tool calls from streaming LLM chunks.

    Supports three formats:
      1) Textual tags: <tool_call> ... </tool_call>
      2) Pure JSON at start: object or array
      3) Native deltas: function_call/tool_calls in chunk['choices'][0]['delta']

    API:
      - on_delta(chunk) -> List[str]: visible text to yield for this chunk
      - finalize() -> List[Dict[str, Any]]: normalized tool_calls

    After finalize(), if tool_mode was True and no valid tool calls were parsed,
    'fallback_visible' contains the accumulated content to yield to avoid an empty turn.
    """

    def __init__(self, prefix_buffer_size: int = 64) -> None:
        self.prefix_buffer_size = max(8, int(prefix_buffer_size or 64))
        self.reset()

    def reset(self) -> None:
        # Decision state
        self.tool_mode: bool = False
        self.decision_made: bool = False

        # Textual buffers
        self.prefix_buffer: str = ""
        self.accumulated_content: str = ""
        self.prefix_yielded: bool = False

        # Native deltas accumulation
        self.native_function_call: Dict[str, Optional[str]] = {"name": None, "arguments": ""}
        self.native_tool_calls: Dict[int, Dict[str, Optional[str]]] = {}
        self.has_native_deltas: bool = False

        # Fallback visible text after finalize() when tool_mode True but parsing fails
        self.fallback_visible: str = ""

    # ---- Streaming hooks -------------------------------------------------
    def on_delta(self, chunk: Dict[str, Any]) -> List[str]:
        out: List[str] = []
        delta = (chunk.get("choices") or [{}])[0].get("delta", {})

        # 1) Native deltas (highest priority)
        if self._process_native_deltas(delta):
            if not self.tool_mode:
                self.tool_mode = True
                self.decision_made = True
                self.has_native_deltas = True
            return out  # suppress native JSON

        # 2) Textual content
        content = delta.get("content")
        if content is None:
            return out

        if self.tool_mode:
            # Accumulate but do not yield while in tool mode
            self.accumulated_content += content
            return out

        if self.decision_made:
            # Stream normally; flush any held prefix first
            if not self.prefix_yielded and self.prefix_buffer:
                out.append(self.prefix_buffer)
                self.prefix_yielded = True
                self.prefix_buffer = ""
            out.append(content)
            return out

        # Need to decide: buffer and inspect minimal prefix
        self.prefix_buffer += content
        self.accumulated_content += content

        decision = self._check_prefix_decision()
        if decision == "tool":
            self.tool_mode = True
            self.decision_made = True
            return out
        if decision == "text":
            self.decision_made = True
            if self.prefix_buffer:
                out.append(self.prefix_buffer)
                self.prefix_yielded = True
                self.prefix_buffer = ""
            return out

        # Undecided: keep buffering
        return out

    def finalize(self) -> List[Dict[str, Any]]:
        # Not a tool turn
        if not self.tool_mode:
            return []

        # Prefer native deltas
        tool_calls: List[Dict[str, Any]] = []
        if self.has_native_deltas:
            if self.native_function_call.get("name"):
                tool_calls.append(self._normalize_call(
                    name=self.native_function_call.get("name"),
                    arguments_str=self.native_function_call.get("arguments") or "",
                ))
            # tool_calls by index order
            for idx in sorted(self.native_tool_calls.keys()):
                rec = self.native_tool_calls[idx]
                if rec.get("name"):
                    tool_calls.append(self._normalize_call(
                        name=rec.get("name"),
                        arguments_str=rec.get("arguments") or "",
                        tool_id=rec.get("id"),
                    ))
            if tool_calls:
                return tool_calls

        # Textual parse: tags then pure JSON
        content = (self.accumulated_content or "").strip()
        if content:
            tool_calls = self._parse_tags(content)
            if tool_calls:
                return tool_calls
            tool_calls = self._parse_json(content)
            if tool_calls:
                return tool_calls

        # Fallback: expose accumulated content so caller can yield it
        self.fallback_visible = self.accumulated_content
        return []

    # ---- Internals -------------------------------------------------------
    def _process_native_deltas(self, delta: Dict[str, Any]) -> bool:
        processed = False
        fc = delta.get("function_call")
        if isinstance(fc, dict):
            name = fc.get("name")
            args = fc.get("arguments")
            if name:
                self.native_function_call["name"] = name
            if args:
                self.native_function_call["arguments"] = (self.native_function_call.get("arguments") or "") + str(args)
            processed = True

        tcs = delta.get("tool_calls")
        if isinstance(tcs, list) and tcs:
            for tc in tcs:
                try:
                    idx = tc.get("index", 0)
                    rec = self.native_tool_calls.get(idx) or {"id": None, "name": None, "arguments": ""}
                    if tc.get("id"):
                        rec["id"] = tc.get("id")
                    func = tc.get("function") or {}
                    if func.get("name"):
                        rec["name"] = func.get("name")
                    if func.get("arguments"):
                        rec["arguments"] = (rec.get("arguments") or "") + str(func.get("arguments"))
                    self.native_tool_calls[idx] = rec
                except Exception:
                    continue
            processed = True

        return processed

    def _check_prefix_decision(self) -> str:
        stripped = self.prefix_buffer.lstrip()
        if not stripped:
            if len(self.prefix_buffer) >= self.prefix_buffer_size:
                return "text"
            return "undecided"

        first = stripped[0]
        if first in "{[":
            return "tool"
        if first == "<":
            target = "<tool_call"
            # current prefix from first non-ws
            current = stripped[: len(target)]
            if target.startswith(current):
                # still could be <tool_call>; wait for more if not exact
                if current == target:
                    return "tool"
                return "undecided"
            # other tag like <|... -> text
            return "text"
        return "text"

    def _parse_tags(self, content: str) -> List[Dict[str, Any]]:
        # Multiple <tool_call> ... </tool_call> blocks
        pattern = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL | re.IGNORECASE)
        out: List[Dict[str, Any]] = []
        for body in pattern.findall(content):
            try:
                data = json.loads(body.strip())
                if isinstance(data, dict) and data.get("name"):
                    args = data.get("arguments")
                    if isinstance(args, dict):
                        args_str = json.dumps(args)
                    elif isinstance(args, str):
                        args_str = args
                    else:
                        args_str = "{}"
                    out.append(self._normalize_call(data.get("name"), args_str))
            except Exception:
                continue
        return out

    def _parse_json(self, content: str) -> List[Dict[str, Any]]:
        try:
            data = json.loads(content)
        except Exception:
            return []

        out: List[Dict[str, Any]] = []
        if isinstance(data, dict):
            if data.get("name"):
                args = data.get("arguments")
                if isinstance(args, dict):
                    args_str = json.dumps(args)
                elif isinstance(args, str):
                    args_str = args
                else:
                    args_str = "{}"
                out.append(self._normalize_call(data.get("name"), args_str))
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("name"):
                    args = item.get("arguments")
                    if isinstance(args, dict):
                        args_str = json.dumps(args)
                    elif isinstance(args, str):
                        args_str = args
                    else:
                        args_str = "{}"
                    out.append(self._normalize_call(item.get("name"), args_str))
        return out

    @staticmethod
    def _normalize_call(name: Optional[str], arguments_str: str, tool_id: Optional[str] = None) -> Dict[str, Any]:
        try:
            args_obj = json.loads(arguments_str) if arguments_str else {}
            if not isinstance(args_obj, dict):
                args_obj = {}
        except Exception:
            args_obj = {}
        return {"id": tool_id, "name": (name or "").strip().lower(), "arguments": args_obj}

