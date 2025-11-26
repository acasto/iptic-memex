from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class HookSpec:
    """Configuration for a single hook."""

    name: str
    model: Optional[str]
    prompt: Optional[str]
    tools: Optional[str]
    steps: int
    mode: str  # 'inject' | 'silent' | 'rewrite' (future)
    label: Optional[str]
    prefix: Optional[str]


def _parse_hook_names(session, phase: str) -> List[str]:
    """Return the list of hook names configured for a given phase."""
    try:
        raw = session.get_option("HOOKS", phase, fallback=None)
    except Exception:
        raw = None
    if not raw:
        return []
    try:
        return [n.strip() for n in str(raw).split(",") if n and str(n).strip()]
    except Exception:
        return []


def _build_hook_spec(session, name: str) -> Optional[HookSpec]:
    """Construct a HookSpec from config for HOOK.<name>."""
    section = f"HOOK.{name}"

    def _get_opt(opt: str, fallback: Any = None) -> Any:
        try:
            return session.get_option(section, opt, fallback=fallback)
        except Exception:
            return fallback

    # Enable flag: allow toggling hooks without editing [HOOKS] lists
    enabled_val = _get_opt("enable", True)
    enabled = True
    try:
        if isinstance(enabled_val, bool):
            enabled = enabled_val
        elif isinstance(enabled_val, str):
            enabled = enabled_val.strip().lower() not in ("false", "0", "no")
    except Exception:
        enabled = True
    if not enabled:
        return None

    model = _get_opt("model", None)
    prompt = _get_opt("prompt", None)
    tools = _get_opt("tools", None)
    label = _get_opt("label", None)
    prefix = _get_opt("prefix", None)

    steps_val = _get_opt("steps", 1)
    steps = 1
    try:
        if isinstance(steps_val, int):
            steps = steps_val
        elif isinstance(steps_val, str) and steps_val.strip().isdigit():
            steps = int(steps_val.strip())
    except Exception:
        steps = 1
    if steps <= 0:
        steps = 1

    mode_val = _get_opt("mode", "inject")
    try:
        mode = str(mode_val).strip().lower() if mode_val is not None else "inject"
    except Exception:
        mode = "inject"
    if mode not in ("inject", "silent", "rewrite"):
        mode = "inject"

    return HookSpec(
        name=name,
        model=str(model) if model is not None else None,
        prompt=str(prompt) if prompt is not None else None,
        tools=str(tools) if tools is not None else None,
        steps=steps,
        mode=mode,
        label=str(label).strip() if isinstance(label, str) and label.strip() else None,
        prefix=str(prefix) if prefix is not None else None,
    )


def _run_single_hook(session, spec: HookSpec, phase: str, extras: Optional[Dict[str, Any]]) -> None:
    """Execute a single hook spec using an internal agent run."""
    # Build overrides for the internal session
    overrides: Dict[str, Any] = {}
    if spec.model:
        overrides["model"] = spec.model
    if spec.prompt:
        overrides["prompt"] = spec.prompt
    if spec.tools:
        # Non-interactive agent runs respect AGENT.active_tools_agent
        overrides["active_tools_agent"] = spec.tools

    # Expose simple extras as hook_* overrides so templates can use them
    extras = extras or {}
    try:
        overrides["hook_name"] = spec.name
        overrides["hook_phase"] = phase
        for key, value in extras.items():
            if key in ("phase",):
                continue
            if isinstance(value, (str, int, float, bool)):
                overrides[f"hook_{key}"] = value
    except Exception:
        pass

    try:
        result = session.run_internal_agent(
            steps=spec.steps,
            overrides=overrides or None,
            contexts=None,
            output="final",
            verbose_dump=False,
        )
    except Exception:
        # Hooks must never break the main turn; log best-effort and continue.
        try:
            session.utils.logger.log("WARNING", f"Hook '{spec.name}' failed", component="core.hooks")
        except Exception:
            pass
        return

    if spec.mode == "silent":
        # Side-effects only (e.g., memory writes)
        return

    # For now, treat both 'inject' and 'rewrite' as context injectors. A future
    # iteration can wire 'rewrite' to modify the user message before provider.
    try:
        last_text = getattr(result, "last_text", None)
    except Exception:
        last_text = None
    if not last_text:
        return

    # Build a transient assistant context object (not stored in session.context)
    ctx_obj = None
    try:
        ctx_obj = getattr(session, "create_context", None)
        if callable(ctx_obj):
            name = spec.label or f"hook:{spec.name}"
            prefix = spec.prefix or ""
            if prefix and not prefix.endswith("\n"):
                prefix = prefix + "\n"
            content = f"{prefix}{str(last_text)}"
            ctx_obj = session.create_context(
                "assistant",
                {
                    "name": name,
                    "content": content,
                },
            )
        else:
            ctx_obj = None
    except Exception:
        ctx_obj = None
    if not ctx_obj:
        return

    # Attach the new assistant context to the last chat turn so it is processed
    # alongside other per-turn contexts by providers.
    try:
        chat = session.get_context("chat")
    except Exception:
        chat = None
    if not chat:
        return

    try:
        history = chat.get("all") or []
    except Exception:
        history = []
    if not history:
        return

    last_turn = history[-1]
    try:
        existing = last_turn.get("context")
        if not isinstance(existing, list):
            existing = [] if existing is None else [existing]
            last_turn["context"] = existing
        existing.append({"type": "assistant", "context": ctx_obj})
    except Exception:
        pass


def run_hooks(session, phase: str, extras: Optional[Dict[str, Any]] = None) -> None:
    """Run all configured hooks for the given phase.

    - phase: 'pre_turn' or 'post_turn' (other values are ignored for now)
    - extras: optional dict of additional context (e.g., input_text, last_user)
    """
    try:
        names = _parse_hook_names(session, phase)
    except Exception:
        names = []
    if not names:
        return

    shared_extras: Dict[str, Any] = dict(extras or {})
    shared_extras.setdefault("phase", phase)

    for name in names:
        spec = _build_hook_spec(session, name)
        if not spec:
            continue
        # Silent hooks do not need to block the pre-turn path; they can safely
        # run after the provider call using the post_turn phase instead.
        if phase == "pre_turn" and spec.mode == "silent":
            continue
        _run_single_hook(session, spec, phase, shared_extras)
