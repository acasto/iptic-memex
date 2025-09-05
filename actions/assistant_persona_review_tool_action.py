from __future__ import annotations

from typing import Any, Dict, List

from base_classes import StepwiseAction, Completed
from utils.tool_args import get_str, get_bool, get_list
import re


class AssistantPersonaReviewToolAction(StepwiseAction):
    """Review content/ideas/features from multiple persona perspectives.

    - Runs one internal completion per persona using a general review prompt.
    - Optionally runs a panel synthesis pass to aggregate takeaways.
    - Accepts personas as names or a Markdown file describing personas.
    - Returns concise, structured outputs to keep things actionable.
    """

    def __init__(self, session):
        self.session = session
        # no-op; keep constructor for symmetry with other actions

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
            'args': ['content', 'personas', 'goal', 'notes', 'artifact_type', 'panel', 'files', 'model'],
            'description': (
                'Run a persona review over content, ideas, or features. Personas may be provided as names or as a '
                'Markdown file describing personas. Returns concise feedback per persona and an optional panel synthesis. '
                'Inputs: content (item to consider or a short reference), personas (CSV/list or a path to a personas.md), '
                'optional goal, notes (constraints, assumptions, references), artifact_type (hint: copy|idea|feature|design|other), '
                'panel (boolean), files (CSV/list of supplemental docs like brand guide, audience, product notes), and model override. '
                'Uses internal runs with prompts personas/review and personas/panel.'
            ),
            'required': ['content', 'personas'],
            'schema': {
                'properties': {
                    'content': {"type": "string", "description": "Item to evaluate or a reference to context (text)."},
                    'personas': {"type": "string", "description": "CSV/list of persona names or a path to a Markdown personas file."},
                    'goal': {"type": "string", "description": "Objective for the review (e.g., reduce friction, validate viability)."},
                    'notes': {"type": "string", "description": "Optional notes: constraints, assumptions, and cross-references to files."},
                    'artifact_type': {"type": "string", "description": "Optional hint: copy|idea|feature|design|other."},
                    'panel': {"type": "boolean", "description": "If true, run a synthesis pass across personas."},
                    'files': {"type": "string", "description": "Supplemental file paths (CSV/list) with guidelines and related info."},
                    'model': {"type": "string", "description": "Optional model override; otherwise [AGENT].default_model is used."},
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
        # Inputs (content can be the item text or a short reference; files may contain the full context)
        raw_content = (get_str(args, 'content') or content or '').strip()
        personas_arg = get_list(args, 'personas') or []
        goal = get_str(args, 'goal') or ''
        notes = get_str(args, 'notes') or ''
        artifact_type = get_str(args, 'artifact_type') or ''
        model_override = get_str(args, 'model')
        files_arg = get_list(args, 'files') or []
        do_panel = bool(get_bool(args, 'panel', False))

        if not raw_content:
            self.session.add_context('assistant', {'name': 'persona_review_error', 'content': 'No content provided for review'})
            return Completed({'ok': False, 'error': 'no_content'})
        # Collect personas: accept names or a Markdown personas file
        personas = self._collect_personas(personas_arg)
        if not personas:
            self.session.add_context('assistant', {'name': 'persona_review_error', 'content': 'No personas found'})
            return Completed({'ok': False, 'error': 'no_personas'})

        # Load supplemental files text (guidelines, persona details, brand voice, etc.)
        guidelines_text = self._read_supplemental_files(files_arg)

        # Execute per-persona internal completions
        review_prompt = 'personas/review'
        panel_prompt = 'personas/panel'

        results: List[Dict[str, str]] = []
        for p in personas:
            name = str(p.get('name', '')).strip()
            details = str(p.get('details', '') or '').strip()
            if not name:
                continue
            msg = self._build_persona_message(
                persona=name,
                persona_details=details,
                content=raw_content,
                goal=goal,
                notes=notes,
                artifact_type=artifact_type,
                guidelines=guidelines_text,
            )
            overrides = {'prompt': review_prompt}
            if model_override:
                overrides['model'] = model_override
            res = self.session.run_internal_completion(message=msg, overrides=overrides, contexts=None, capture='text')
            text = getattr(res, 'last_text', None) or ''
            results.append({'persona': name, 'review': text})

        # Optional panel synthesis
        panel_text = None
        if do_panel and results:
            panel_msg = self._build_panel_message(results, goal=goal, notes=notes, artifact_type=artifact_type, guidelines=guidelines_text)
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
        *,
        persona_details: str | None,
        content: str,
        goal: str | None,
        notes: str | None,
        artifact_type: str | None,
        guidelines: str | None,
    ) -> str:
        parts: List[str] = []
        parts.append(f"[Persona] {persona}")
        if persona_details:
            parts.append("[Persona Details]\n" + persona_details.strip())
        if goal:
            parts.append(f"[Goal] {goal}")
        if artifact_type:
            parts.append(f"[Artifact Type] {artifact_type}")
        if notes:
            parts.append(f"[Notes] {notes}")
        if guidelines:
            parts.append("[Guidelines]\n" + guidelines.strip())
        parts.append("[Item]\n" + content)
        # Keep the message lean; instruction lives in personas/review prompt
        return "\n".join(parts)

    @staticmethod
    def _build_panel_message(
        results: List[Dict[str, str]],
        *,
        goal: str | None = None,
        notes: str | None = None,
        artifact_type: str | None = None,
        guidelines: str | None = None,
    ) -> str:
        parts: List[str] = []
        if goal:
            parts.append(f"[Goal] {goal}")
        if artifact_type:
            parts.append(f"[Artifact Type] {artifact_type}")
        if notes:
            parts.append(f"[Notes] {notes}")
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

    def _collect_personas(self, personas_input: List[Any]) -> List[Dict[str, str]]:
        """Accept a list/CSV of persona names or a path to a Markdown personas file.

        Supported Markdown formats:
        - Sectioned: a heading named "Personas" containing subheadings per persona (### Name) with optional body.
        - Headings: top-level headings (##/###) are treated as persona names with their section body as details.
        - Bulleted: lines like "- Name: one-liner" or "- Name".
        """
        if not personas_input:
            return []
        fs = self.session.get_action('assistant_fs_handler')

        collected: List[Dict[str, str]] = []
        for item in personas_input:
            name = str(item or '').strip()
            if not name:
                continue
            # Try to read as a file
            content = None
            try:
                content = fs.read_file(name, binary=False, encoding='utf-8') if fs else None
            except Exception:
                content = None
            if isinstance(content, bytes):
                try:
                    content = content.decode('utf-8', errors='ignore')
                except Exception:
                    content = None
            if isinstance(content, str) and content.strip():
                collected.extend(self._parse_personas_markdown(content))
            else:
                collected.append({'name': name, 'details': ''})

        # Deduplicate by name while keeping the first details
        seen = {}
        result: List[Dict[str, str]] = []
        for p in collected:
            n = p.get('name', '').strip()
            if not n or n.lower() in seen:
                continue
            seen[n.lower()] = True
            result.append({'name': n, 'details': p.get('details', '') or ''})
        return result

    def _parse_personas_markdown(self, text: str) -> List[Dict[str, str]]:
        lines = text.replace('\r\n', '\n').replace('\r', '\n')
        headings = [(m.start(), len(m.group(1)), m.group(2).strip())
                    for m in re.finditer(r'^(#{1,6})\s+(.+?)\s*$', lines, flags=re.M)]
        personas: List[Dict[str, str]] = []

        def section_text(start_idx: int, level: int) -> str:
            # find next heading at same or higher level
            for pos, lvl, _ in headings:
                if pos > start_idx and lvl <= level:
                    return lines[start_idx:pos].strip()
            return lines[start_idx:].strip()

        # Strategy 1: look for a "Personas" section and parse subheadings
        personas_section = None
        for pos, lvl, title in headings:
            if title.lower() == 'personas':
                personas_section = (pos, lvl)
                break
        if personas_section:
            sec_pos, sec_lvl = personas_section
            sec_body = section_text(sec_pos, sec_lvl)
            # find subheadings inside section body
            offset = sec_pos + 0
            for m in re.finditer(r'^(#{1,6})\s+(.+?)\s*$', lines[sec_pos:], flags=re.M):
                pos_rel = sec_pos + m.start()
                lvl = len(m.group(1))
                title = m.group(2).strip()
                if lvl <= sec_lvl:
                    continue
                # capture this subsection's body
                # find next heading at level <= lvl
                body = ''
                for pos2, lvl2, _ in headings:
                    if pos2 > pos_rel and lvl2 <= lvl:
                        body = lines[pos_rel + m.end() - m.start():pos2].strip()
                        break
                if not body:
                    body = lines[pos_rel + m.end() - m.start():].strip()
                if title:
                    personas.append({'name': title, 'details': body})
            if personas:
                return personas

        # Strategy 2: use top-level headings as personas
        if headings:
            base_level = min(lvl for _, lvl, _ in headings)
            for i, (pos, lvl, title) in enumerate(headings):
                if lvl != base_level:
                    continue
                # compute body until next base or above
                end_pos = len(lines)
                for pos2, lvl2, _ in headings[i+1:]:
                    if lvl2 <= lvl:
                        end_pos = pos2
                        break
                body = lines[pos: end_pos]
                # remove the heading line itself
                body = re.sub(r'^#{1,6}\s+.+?\n', '', body, count=1, flags=re.M).strip()
                if title:
                    personas.append({'name': title, 'details': body})
            if personas:
                return personas

        # Strategy 3: bullet list fallback
        for m in re.finditer(r'^\s*[-*+]\s+(.+)$', lines, flags=re.M):
            item = m.group(1).strip()
            if not item:
                continue
            if ':' in item:
                nm, desc = item.split(':', 1)
                personas.append({'name': nm.strip(), 'details': desc.strip()})
            else:
                personas.append({'name': item, 'details': ''})
        return personas

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
