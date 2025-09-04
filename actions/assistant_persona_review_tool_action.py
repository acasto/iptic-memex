from __future__ import annotations

from typing import Any, Dict, List

from base_classes import StepwiseAction, Completed
from utils.tool_args import get_str, get_bool, get_list


class AssistantPersonaReviewToolAction(StepwiseAction):
    """Review marketing copy from multiple persona perspectives.

    - Runs one internal completion per persona using the general review prompt.
    - Optionally runs a panel synthesis pass to aggregate key takeaways.
    - Returns concise, structured outputs to keep things actionable.
    """

    def __init__(self, session):
        self.session = session
        self.token_counter = session.get_action('count_tokens')

    # ---- Dynamic tool registry metadata ----
    @classmethod
    def tool_name(cls) -> str:
        return 'persona_review'

    @classmethod
    def tool_aliases(cls) -> list[str]:
        return []

    @classmethod
    def tool_spec(cls, session) -> dict:
        return {
            'args': ['content', 'personas', 'goal', 'rubric', 'panel', 'tone', 'constraints', 'model', 'files'],
            'description': (
                'Review provided copy from one or more personas. Returns concise, structured feedback per persona '
                'and an optional panel summary. Inputs: content, personas (list or comma-separated), optional goal, '
                'rubric, tone, constraints, and model override. Optional files: one or more supplemental file paths (list '\
                'or comma-separated) containing guidelines, company/brand voice, target audience, persona details, etc. '
                'Uses internal runs with prompts personas/review and personas/panel.'
            ),
            'required': ['content', 'personas'],
            'schema': {
                'properties': {
                    'content': {"type": "string", "description": "Copy to review."},
                    'personas': {"type": "string", "description": "List or comma-separated personas (e.g., CFO, VP Engineering)."},
                    'goal': {"type": "string", "description": "Short objective (e.g., improve conversion on landing page)."},
                    'rubric': {"type": "string", "description": "Optional criteria: clarity, trust, benefits, objections, CTA."},
                    'panel': {"type": "boolean", "description": "If true, run a consensus pass across personas."},
                    'tone': {"type": "string", "description": "Optional style guidance (e.g., B2B, concise, no jargon)."},
                    'constraints': {"type": "string", "description": "Optional constraints or guardrails."},
                    'model': {"type": "string", "description": "Optional model override; otherwise uses [AGENT].default_model."},
                    'files': {"type": "string", "description": "Supplemental file paths (CSV or list) with guidelines and related info."},
                }
            },
            'auto_submit': True,
        }

    @staticmethod
    def can_run(session) -> bool:
        # Tool is available whenever tools are enabled; internal runs will pick up [AGENT].default_model
        return True

    # ---- Stepwise protocol -----------------------------------------------
    def start(self, args: Dict[str, Any], content: str = "") -> Completed:
        # Inputs
        raw_content = (get_str(args, 'content') or content or '').strip()
        personas = get_list(args, 'personas') or get_list(args, 'persona') or []
        goal = get_str(args, 'goal') or ''
        rubric = get_str(args, 'rubric') or ''
        tone = get_str(args, 'tone') or ''
        constraints = get_str(args, 'constraints') or ''
        model_override = get_str(args, 'model')
        files_arg = get_list(args, 'files') or get_list(args, 'file') or []
        do_panel = bool(get_bool(args, 'panel', False))

        if not raw_content:
            self.session.add_context('assistant', {'name': 'persona_review_error', 'content': 'No content provided for review'})
            return Completed({'ok': False, 'error': 'no_content'})
        if not personas:
            self.session.add_context('assistant', {'name': 'persona_review_error', 'content': 'No personas provided'})
            return Completed({'ok': False, 'error': 'no_personas'})

        # Large input gating
        try:
            token_count = self.token_counter.count_tiktoken(raw_content)
        except Exception:
            token_count = 0
        limit = int(self.session.get_tools().get('large_input_limit', 4000))
        if token_count > limit:
            if self.session.get_tools().get('confirm_large_input', True):
                try:
                    self.session.ui.emit('warning', {'message': f"Input exceeds token limit ({limit}). Auto-submit disabled for review."})
                except Exception:
                    pass
                self.session.set_flag('auto_submit', False)
            else:
                self.session.add_context('assistant', {'name': 'persona_review_error', 'content': f'Input exceeds token limit ({limit}). Consider narrowing content.'})
                return Completed({'ok': False, 'error': 'too_large'})

        # Load supplemental files text (guidelines, persona details, brand voice, etc.)
        guidelines_text = self._read_supplemental_files(files_arg)

        # Execute per-persona internal completions
        review_prompt = 'personas/review'
        panel_prompt = 'personas/panel'

        results: List[Dict[str, str]] = []
        for p in personas:
            persona = str(p).strip()
            if not persona:
                continue
            msg = self._build_persona_message(persona, raw_content, goal, rubric, tone, constraints, guidelines_text)
            overrides = {'prompt': review_prompt}
            if model_override:
                overrides['model'] = model_override
            res = self.session.run_internal_completion(message=msg, overrides=overrides, contexts=None, capture='text')
            text = getattr(res, 'last_text', None) or ''
            results.append({'persona': persona, 'review': text})

        # Optional panel synthesis
        panel_text = None
        if do_panel and results:
            panel_msg = self._build_panel_message(results, goal=goal, guidelines=guidelines_text)
            overrides = {'prompt': panel_prompt}
            if model_override:
                overrides['model'] = model_override
            res = self.session.run_internal_completion(message=panel_msg, overrides=overrides, contexts=None, capture='text')
            panel_text = getattr(res, 'last_text', None) or ''

        # Emit a consolidated assistant context
        combined = self._format_combined_output(results, panel_text)
        self.session.add_context('assistant', {'name': 'persona_review', 'content': combined})

        return Completed({'ok': True, 'personas': [r['persona'] for r in results], 'panel': bool(panel_text)})

    # ---- Helpers -----------------------------------------------------------
    @staticmethod
    def _build_persona_message(
        persona: str,
        content: str,
        goal: str | None,
        rubric: str | None,
        tone: str | None,
        constraints: str | None,
        guidelines: str | None,
    ) -> str:
        parts: List[str] = []
        parts.append(f"[Persona] {persona}")
        if goal:
            parts.append(f"[Goal] {goal}")
        if tone:
            parts.append(f"[Tone] {tone}")
        if constraints:
            parts.append(f"[Constraints] {constraints}")
        if rubric:
            parts.append(f"[Rubric] {rubric}")
        if guidelines:
            parts.append("[Guidelines]\n" + guidelines.strip())
        parts.append("[Copy]\n" + content)
        # Keep the message lean; instruction lives in personas/review prompt
        return "\n".join(parts)

    @staticmethod
    def _build_panel_message(
        results: List[Dict[str, str]],
        *,
        goal: str | None = None,
        guidelines: str | None = None,
    ) -> str:
        parts: List[str] = []
        if goal:
            parts.append(f"[Goal] {goal}")
        if guidelines:
            parts.append("[Guidelines]\n" + guidelines.strip())
        parts.append("[Persona Reviews]")
        for r in results:
            persona = r.get('persona', '')
            review = r.get('review', '')
            parts.append(f"--- {persona} ---\n{review}")
        return "\n".join(parts)

    def _read_supplemental_files(self, files: List[Any]) -> str:
        if not files:
            return ''
        fs = self.session.get_action('assistant_fs_handler')
        texts: List[str] = []
        for f in files:
            try:
                name = str(f).strip()
                if not name:
                    continue
                content = fs.read_file(name, binary=False, encoding='utf-8') if fs else None
                if isinstance(content, bytes):
                    try:
                        content = content.decode('utf-8', errors='ignore')
                    except Exception:
                        content = None
                if content:
                    header = f"### {name}"
                    texts.append(f"{header}\n{content}")
            except Exception:
                continue
        return "\n\n".join(texts).strip()

    @staticmethod
    def _format_combined_output(results: List[Dict[str, str]], panel_text: str | None) -> str:
        out: List[str] = []
        out.append("## Persona Reviews")
        for r in results:
            out.append(f"### {r['persona']}")
            out.append(r['review'].strip())
        if panel_text:
            out.append("\n## Panel Summary")
            out.append(panel_text.strip())
        return "\n\n".join(out)
