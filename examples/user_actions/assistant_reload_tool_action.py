from typing import Optional

from base_classes import InteractionAction


class AssistantReloadToolAction(InteractionAction):
    """
    Reload action modules by invalidating the action-class cache.

    Usage (assistant command block):
    %%RELOAD%%
    assistant_openlink_tool
    assistant_file_tool
    %%END%%

    Notes
    - Accepts one name per line. You can supply variants like
      "assistant_openlink_tool_action.py" or "assistant_openlink_tool_action";
      they normalize to the canonical action name (e.g., "assistant_openlink_tool").
    - Use "all" to clear the entire action cache.
    - Actions are re-imported the next time they are requested via `get_action`.
    """

    def __init__(self, session):
        self.session = session

    # ---- Dynamic tool registry metadata (optional for user tools) ----
    @classmethod
    def tool_name(cls) -> str:
        return 'reload'

    @classmethod
    def tool_aliases(cls) -> list[str]:
        return []

    @classmethod
    def tool_spec(cls, session) -> dict:
        return {
            'args': ['target', 'targets'],
            'description': (
                "Reload one or more action modules by name. Accepts newline-separated names in content or 'target(s)' args."
            ),
            'required': [],
            'schema': {
                'properties': {
                    'target': {"type": "string", "description": "Single action to reload (e.g., 'assistant_file_tool')."},
                    'targets': {"type": "string", "description": "Comma-separated list of actions to reload."},
                    'content': {"type": "string", "description": "Optional newline-separated action names; supports 'all' to clear cache."}
                }
            },
            'auto_submit': True,
        }

    @staticmethod
    def _normalize_target(target: str) -> str:
        name = (target or "").strip()
        if not name:
            return ""
        # Strip path components
        if "/" in name:
            name = name.split("/")[-1]
        if "\\" in name:
            name = name.split("\\")[-1]
        # Drop extension
        if name.endswith(".py"):
            name = name[:-3]
        # Drop trailing `_action`
        if name.endswith("_action"):
            name = name[:-7]
        return name

    def run(self, args: Optional[dict] = None, content: str = "") -> None:
        # Accept names via content (newline-separated), or via args: target or comma-separated targets
        lines = [ln.strip() for ln in (content or "").splitlines() if ln.strip() and not ln.strip().startswith('#')]
        if not lines and isinstance(args, dict):
            tgt = (args.get('target') or '').strip()
            tgts = (args.get('targets') or '').strip()
            collected = []
            if tgt:
                collected.append(tgt)
            if tgts:
                collected.extend([x.strip() for x in tgts.split(',') if x.strip()])
            lines = collected

        registry = getattr(self.session, "_registry", None)
        if registry is None or not hasattr(registry, "_action_cache"):
            self.session.add_context('assistant', {
                'name': 'reload_error',
                'content': "Registry not available or unsupported"
            })
            return

        cache = registry._action_cache  # noqa: SLF001 (intentional internal access)

        if not lines:
            self.session.add_context('assistant', {
                'name': 'reload_error',
                'content': "No action names provided"
            })
            return

        # Support clearing all
        if any(x.lower() in {"all", "*"} for x in lines):
            count = len(cache)
            cache.clear()
            self.session.add_context('assistant', {
                'name': 'reload_success',
                'content': f"Cleared action cache; {count} entr{'y' if count == 1 else 'ies'} reloaded on next use"
            })
            return

        reloaded = []
        missing = []

        for raw in lines:
            name = self._normalize_target(raw)
            if not name:
                continue
            if name in cache:
                try:
                    del cache[name]
                    reloaded.append(name)
                except Exception as e:  # pragma: no cover - defensive
                    missing.append(f"{name} (error: {e})")
            else:
                # Not in cache now, but will reload on first use anyway
                missing.append(name)

        if reloaded and not missing:
            self.session.add_context('assistant', {
                'name': 'reload_success',
                'content': f"Reloaded: {', '.join(sorted(reloaded))}"
            })
        elif reloaded and missing:
            self.session.add_context('assistant', {
                'name': 'reload_partial',
                'content': (
                    f"Reloaded: {', '.join(sorted(reloaded))}. "
                    f"Not currently cached: {', '.join(sorted(missing))} (will load fresh on next use)"
                )
            })
        else:
            self.session.add_context('assistant', {
                'name': 'reload_info',
                'content': f"No cached entries for: {', '.join(sorted(missing))} (will load fresh on first use)"
            })
