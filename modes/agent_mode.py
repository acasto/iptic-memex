from __future__ import annotations

from base_classes import InteractionMode
from core.mode_runner import run_agent


class AgentMode(InteractionMode):
    """
    Non-interactive N-turn loop that executes assistant turns with optional tool use
    between turns. Stops on reaching max steps or sentinel tokens.
    """

    def __init__(
        self, session, steps: int = 1, writes_policy: str = "deny",
        use_status_tags: bool = True, output_mode: str | None = None,
        message: str | None = None,
    ):
        self.session = session
        self.message = message
        self.writes_policy = writes_policy or "deny"
        self.steps = max(1, int(steps or 1))
        self.use_status_tags = bool(use_status_tags)

        # Seed agent mode and write policy for file tools
        self.session.enter_agent_mode(writes_policy or "deny")
        # Configure agent display/output-related params
        try:
            # Pull optional AGENT defaults for display behavior
            show_details = self.session.get_option('AGENT', 'show_context_details', fallback=None)
            if show_details is not None:
                self.session.set_option('show_context_details', show_details)
            detail_max = self.session.get_option('AGENT', 'context_detail_max_chars', fallback=None)
            if detail_max is not None:
                self.session.set_option('context_detail_max_chars', detail_max)
            # Output mode: CLI overrides config; fallback to [AGENT].output or 'final'
            cfg_output = self.session.get_option('AGENT', 'output', fallback='final')
            mode = (output_mode or cfg_output or 'final').lower()
            if mode not in ('final', 'full', 'none'):
                mode = 'final'
            self.session.set_option('agent_output_mode', mode)
            # In Agent mode, only show context summaries/details when output is 'full'
            self.session.set_option('show_context_summary', mode == 'full')
            self.session.set_option('show_context_details', mode == 'full')
        except Exception:
            pass

        # Ensure a chat context exists
        if not self.session.get_context('chat'):
            self.session.add_context('chat')

        # Utilities
        self.utils = self.session.utils

    def start(self, *, emit_output: bool = True):
        try:
            chat = self.session.get_context('chat')
            if not chat:
                raise RuntimeError('Agent chat context not available')

            provider = self.session.get_provider()

            out_mode = (self.session.get_params().get('agent_output_mode', 'final') or 'final').lower()

            # Suppress leading blanks/newline bursts for final/none
            suppress_ctx = self.utils.output.suppress_stdout_blanks(
                suppress_blank_lines=True, collapse_bursts=True
            ) if out_mode in ('final', 'none') else self.utils.output.suppress_stdout_blanks(False, False)

            with suppress_ctx:
                result = run_agent(
                    session=self.session,
                    steps=self.steps,
                    message=self.message,
                    writes_policy=self.writes_policy,
                    status_tags=self.use_status_tags,
                    output=out_mode,
                    verbose_dump=bool(self.session.get_params().get('agent_debug', False)),
                )

            if self.session.get_params().get('raw_completion') and provider:
                result.raw = provider.get_full_response()
            if not emit_output:
                return result

            # Output policy after the loop
            if out_mode == 'final':
                if self.session.get_params().get('raw_completion', False):
                    if provider and hasattr(provider, 'get_full_response'):
                        raw = provider.get_full_response()
                        try:
                            import json
                            raw_str = json.dumps(raw, indent=2, ensure_ascii=False) if not isinstance(raw, str) else raw
                        except Exception:
                            raw_str = str(raw)
                        if isinstance(raw_str, str):
                            for tag in ('%%DONE%%', '%%COMPLETED%%', '%%COMPLETE%%'):
                                raw_str = raw_str.replace(tag, '')
                        self.utils.output.write(raw_str, end='')
                elif result.last_text:
                    final_text = result.last_text
                    if final_text.startswith('\r\n'):
                        final_text = final_text[2:]
                    elif final_text.startswith('\n'):
                        final_text = final_text[1:]
                    self.utils.output.write(final_text)
            # 'full': already streamed; 'none': no output
            return result
        finally:
            try:
                self.session.exit_agent_mode()
            except Exception:
                pass
