from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

from providers.llamacppserver_provider import LlamaCppServerProvider


class GptOssProvider(LlamaCppServerProvider):
    """
    Harmony-aware wrapper around the managed llama.cpp server provider.

    Goals:
    - Keep process management and OpenAI-compatible wiring from
      LlamaCppServerProvider.
    - Inject a Harmony-conformant system message (identity, dates, reasoning,
      channels; tools note when functions are enabled).
    - Prefer official function tools and add Harmony stop tokens by default.
    - Leave streaming, tool_call normalization, and usage as-is from the base.
    """

    # Reuse startup UX messages from base provider
    startup_wait_message = LlamaCppServerProvider.startup_wait_message
    startup_ready_message = LlamaCppServerProvider.startup_ready_message

    class _SessionParamView(LlamaCppServerProvider._SessionParamView):
        """Param view that flips defaults for Harmony/gpt-oss usage.

        - tool_mode: official (enables function tools on requests)
        - use_old_system_role: False (prompt goes into developer; we add system)
        - stop: ensure Harmony stop tokens <|return|>, <|call|>
        - Preserve base view behavior: stream_options False, vision False,
          extra_body.cache_prompt True, etc.
        """

        def get_params(self):  # type: ignore[override]
            p: Dict[str, Any] = super().get_params()

            # Ensure we use official tools by default for this provider
            if not p.get('tool_mode'):
                p['tool_mode'] = 'official'

            # Always keep the prompt as developer; we inject a dedicated system
            p['use_old_system_role'] = False

            # Add Harmony stop tokens if not already present
            desired = ["<|return|>", "<|call|>"]
            stops = p.get('stop')
            if not stops:
                p['stop'] = desired
            else:
                try:
                    if isinstance(stops, str):
                        stops_list = [s.strip() for s in stops.split(',') if s.strip()]
                    else:
                        stops_list = list(stops)
                except Exception:
                    stops_list = []
                for tok in desired:
                    if tok not in stops_list:
                        stops_list.append(tok)
                p['stop'] = stops_list

            # Sanitize output filters: Harmony uses channels, not <think>; avoid
            # the 'assume_think_open' filter that can hide all output.
            try:
                of_raw = p.get('output_filters')
                if of_raw:
                    if isinstance(of_raw, str):
                        items = [s.strip() for s in of_raw.split(',') if s.strip()]
                    elif isinstance(of_raw, list):
                        items = [str(s).strip() for s in of_raw if str(s).strip()]
                    else:
                        items = []
                    items = [s for s in items if s.lower() != 'assume_think_open']
                    # If nothing left, fall back to a safe default that hides tool blocks only
                    if not items:
                        items = ['tool_call']
                    p['output_filters'] = items
            except Exception:
                pass

            # Prefer binary from LlamaCppServer provider section when not set
            try:
                if not p.get('binary'):
                    # First, look in [LlamaCppServer]
                    b = self._s.get_option_from_provider('binary', 'LlamaCppServer')
                    if not b:
                        # Common alt keys some configs use
                        prov_all = self._s.get_all_options_from_provider('LlamaCppServer') or {}
                        b = prov_all.get('llama_server_binary') or prov_all.get('server_binary')
                    if not b:
                        # As a last resort, look in [DEFAULT]
                        b = self._s.get_option('DEFAULT', 'llama_server_binary', fallback=None)
                    if b:
                        p['binary'] = b
            except Exception:
                pass

            return p

    def __init__(self, session):
        # Important: ensure LlamaCppServerProvider uses our _SessionParamView
        # (its __init__ references self._SessionParamView)
        super().__init__(session)

    # --- Message assembly -------------------------------------------------
    def _build_system_message(self) -> str:
        params = self.session.get_params()
        cutoff = str(params.get('harmony_knowledge_cutoff') or '2024-06')
        today = date.today().isoformat()

        # Reasoning level: default to 'medium' if unspecified
        effort = params.get('reasoning_effort') or 'medium'
        try:
            effort = str(effort).lower()
        except Exception:
            pass

        # If functions are available and enabled, include the tools note
        tools_spec = []
        try:
            mode = getattr(self.session, 'get_effective_tool_mode', lambda: 'none')()
            if mode == 'official':
                tools_spec = self.get_tools_for_request() or []
        except Exception:
            tools_spec = []

        lines: List[str] = [
            "You are ChatGPT, a large language model trained by OpenAI.",
            f"Knowledge cutoff: {cutoff}",
            f"Current date: {today}",
            "",
            f"Reasoning: {effort}",
            "",
            "# Valid channels: analysis, commentary, final. Channel must be included for every message.",
        ]
        if tools_spec:
            lines.append("Calls to these tools must go to the commentary channel: 'functions'.")

        return "\n".join(lines)

    def assemble_message(self) -> list:
        # Start from the base assembly (developer + chat turns, tool msgs, etc.)
        messages = super().assemble_message()

        # Compute and inject Harmony system message at the front
        system_text = self._build_system_message()
        if not system_text.strip():
            return messages

        # If the first message is incorrectly 'system', coerce it to developer
        # (we always want a separate Harmony system message we control)
        if messages and messages[0].get('role') == 'system':
            messages[0]['role'] = 'developer'

        messages.insert(0, {'role': 'system', 'content': system_text})
        return messages

    def get_messages(self):  # introspection view
        return self.assemble_message()
