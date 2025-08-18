from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from ui.base import UI, CapabilityFlags


class CLIUI(UI):
    """CLI implementation that blocks and uses utils for IO."""

    def __init__(self, session) -> None:
        self.session = session
        self.capabilities = CapabilityFlags(file_picker=False, rich_text=False, progress=True, diffs=False, blocking=True)

    # Inputs -------------------------------------------------------------
    def ask_text(self, prompt: str, *, default: Optional[str] = None, multiline: bool = False) -> str:
        return self.session.utils.input.get_input(prompt=prompt, default=default, multiline=multiline)

    def ask_bool(self, prompt: str, *, default: Optional[bool] = None) -> bool:
        # utils.input.get_bool already validates common inputs
        if default is not None:
            prompt = f"{prompt} [{'y' if default else 'n'}]"
        return self.session.utils.input.get_bool(prompt)

    def ask_choice(
        self,
        prompt: str,
        options: List[str],
        *,
        default: Optional[Union[str, List[str]]] = None,
        multi: bool = False,
    ) -> Union[List[str], str]:
        # Minimal CLI selection: show indexed options and accept numbers or values
        out = self.session.utils.output
        out.write(prompt)
        for i, opt in enumerate(options, start=1):
            out.write(f"  {i}. {opt}")
        if multi:
            raw = self.session.utils.input.get_input("Select one or more (comma-separated index or value): ")
            parts = [p.strip() for p in raw.split(',') if p.strip()]
            chosen: List[str] = []
            for p in parts:
                if p.isdigit() and 1 <= int(p) <= len(options):
                    chosen.append(options[int(p) - 1])
                elif p in options:
                    chosen.append(p)
            if not chosen and default is not None:
                return default if isinstance(default, list) else [str(default)]
            return chosen
        else:
            raw = self.session.utils.input.get_input("Select one (index or value): ")
            val = None
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                val = options[int(raw) - 1]
            elif raw in options:
                val = raw
            if val is None:
                if isinstance(default, list):
                    return default[0] if default else options[0]
                return default if isinstance(default, str) and default in options else (options[0] if options else '')
            return val

    def ask_files(
        self,
        prompt: str,
        *,
        accept: Optional[List[str]] = None,
        multiple: bool = True,
        must_exist: bool = True,
    ) -> List[str]:
        import os

        suffixes = tuple(accept) if accept else None
        raw = self.session.utils.input.get_input(f"{prompt} ")
        parts = raw.split() if multiple else [raw.strip()]
        files: List[str] = []
        for p in parts:
            if not p:
                continue
            if must_exist and not os.path.isfile(p):
                self.session.utils.output.warning(f"File not found: {p}")
                continue
            if suffixes and not p.endswith(suffixes):
                self.session.utils.output.warning(f"Skipping unsupported file type: {p}")
                continue
            files.append(p)
        return files

    # Events -------------------------------------------------------------
    def emit(self, event_type: str, data: Dict[str, Any]) -> None:
        out = self.session.utils.output
        et = (event_type or 'status').lower()
        if et == 'warning':
            out.warning(str(data.get('message', '')))
        elif et == 'error':
            out.error(str(data.get('message', '')))
        elif et == 'progress':
            prog = data.get('progress')
            msg = data.get('message', '')
            if prog is None:
                out.info(f"[progress] {msg}")
            else:
                out.info(f"[{int(float(prog)*100)}%] {msg}")
        elif et == 'diff':
            out.info("[diff] " + (data.get('path') or ''))
        elif et == 'metrics':
            out.info("[metrics] " + str({k: v for k, v in data.items() if k != 'type'}))
        else:
            out.write(str(data.get('message', '')))
