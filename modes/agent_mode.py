from __future__ import annotations

from base_classes import InteractionMode
from core.turns import TurnRunner, TurnOptions


class AgentMode(InteractionMode):
    """
    Non-interactive N-turn loop that executes assistant turns with optional tool use
    between turns. Stops on reaching max steps or sentinel tokens.
    """

    def __init__(self, session, steps: int = 1, writes_policy: str = "deny", use_status_tags: bool = True, output_mode: str | None = None):
        self.session = session
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
        self.turn_runner = TurnRunner(self.session)

        # Prepare agent instruction snippet (finish signal and write policy)
        policy = (self.session.get_agent_write_policy() or '').lower()
        finish_instr = (
            "Finish signal: When you are done with the task, output the token %%DONE%% as the last line."
        )
        if policy == 'deny':
            write_instr = (
                "Write policy: File writes are disabled. Do not modify files. If changes are needed, provide unified diffs (diff -u) showing exact edits."
            )
        elif policy == 'dry-run':
            write_instr = (
                "Write policy: Dry run. Do not modify files. Provide the unified diffs (diff -u) you would apply."
            )
        else:
            write_instr = None

        lines = [finish_instr]
        if write_instr:
            lines.append(write_instr)
        self._agent_instructions = "\n\n" + "\n".join(lines)
        self._agent_prompt_injected = False

    # Removed legacy helpers: dumping messages, manual per-turn prep, and assistant turn.

    def start(self):
        try:
            chat = self.session.get_context('chat')
            if not chat:
                self.utils.output.error("AgentStepsMode: chat context not available")
                return

            provider = self.session.get_provider()
            if not provider:
                self.utils.output.error("AgentStepsMode: no provider available")
                return

            out_mode = (self.session.get_params().get('agent_output_mode', 'final') or 'final').lower()

            # Suppress leading blanks/newline bursts for final/none
            suppress_ctx = self.utils.output.suppress_stdout_blanks(
                suppress_blank_lines=True, collapse_bursts=True
            ) if out_mode in ('final', 'none') else self.utils.output.suppress_stdout_blanks(False, False)

            with suppress_ctx:
                result = self.turn_runner.run_agent_loop(
                    self.steps,
                    prepare_prompt=lambda s, idx, total, has_stdin: self._prepare_prompt(idx, total, has_stdin),
                    options=TurnOptions(
                        agent_output_mode=out_mode,
                        early_stop_no_tools=True,
                        verbose_dump=bool(self.session.get_params().get('agent_debug', False)),
                    ),
                )

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
        finally:
            try:
                self.session.exit_agent_mode()
            except Exception:
                pass

    def _prepare_prompt(self, turn_index: int, total_turns: int, stdin_present: bool) -> None:
        """Inject finish/write-policy note into the prompt on first turn.

        If stdin is present and no explicit prompt override exists, replace the
        prompt with instructions-only to avoid mixing unknown prompts.
        """
        try:
            overrides = getattr(self.session.config, 'overrides', {})
            have_explicit_prompt = ('prompt' in overrides)
            prompt_ctx = self.session.get_context('prompt')
            if stdin_present and not have_explicit_prompt:
                if prompt_ctx:
                    prompt_ctx.get()['content'] = self._agent_instructions.strip()
                else:
                    self.session.add_context('prompt', self._agent_instructions.strip())
                self._agent_prompt_injected = True
                return
            if turn_index == 1 and not self._agent_prompt_injected:
                if prompt_ctx:
                    content = prompt_ctx.get().get('content', '')
                    prompt_ctx.get()['content'] = (content + self._agent_instructions)
                else:
                    self.session.add_context('prompt', self._agent_instructions.strip())
                self._agent_prompt_injected = True
        except Exception:
            pass
