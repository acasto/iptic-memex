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
    when_every_n_turns: Optional[int]
    when_min_turn: Optional[int]
    when_message_contains: Optional[List[str]]
    when_role: Optional[str]
    runner: str  # 'internal' | 'external'
    external_cmd: Optional[str]


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
        # IMPORTANT: hook specs are configuration, not runtime parameters.
        # SessionConfig.get_option() checks session overrides *by option name*
        # regardless of section, so reading opt="model" would incorrectly return
        # the session's current model override instead of HOOK.<name>.model.
        try:
            cfg = getattr(session, "config", None)
            base = getattr(cfg, "base_config", None)
            if base is not None and getattr(base, "has_option", None) and base.has_option(section, opt):
                try:
                    from config_manager import ConfigManager

                    return ConfigManager.fix_values(base.get(section, opt))
                except Exception:
                    return base.get(section, opt)
        except Exception:
            pass
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
    runner_val = _get_opt("runner", "internal")
    try:
        runner = str(runner_val).strip().lower() if runner_val is not None else "internal"
    except Exception:
        runner = "internal"
    if runner not in ("internal", "external"):
        runner = "internal"
    external_cmd = _get_opt("external_cmd", None)

    # Conditional gating options
    every_val = _get_opt("when_every_n_turns", None)
    try:
        if isinstance(every_val, int):
            when_every_n_turns = every_val if every_val > 0 else None
        elif isinstance(every_val, str) and every_val.strip().isdigit():
            val = int(every_val.strip())
            when_every_n_turns = val if val > 0 else None
        else:
            when_every_n_turns = None
    except Exception:
        when_every_n_turns = None

    min_val = _get_opt("when_min_turn", None)
    try:
        if isinstance(min_val, int):
            when_min_turn = min_val if min_val > 0 else None
        elif isinstance(min_val, str) and min_val.strip().isdigit():
            val = int(min_val.strip())
            when_min_turn = val if val > 0 else None
        else:
            when_min_turn = None
    except Exception:
        when_min_turn = None

    msg_contains_raw = _get_opt("when_message_contains", None)
    when_message_contains: Optional[List[str]] = None
    try:
        items: List[str] = []
        if isinstance(msg_contains_raw, (list, tuple)):
            for item in msg_contains_raw:
                if isinstance(item, str):
                    trimmed = item.strip()
                    if trimmed:
                        items.append(trimmed)
        elif isinstance(msg_contains_raw, str):
            pieces = [p.strip() for p in msg_contains_raw.split(",")]
            items = [p for p in pieces if p]
        if items:
            when_message_contains = items
    except Exception:
        when_message_contains = None

    role_raw = _get_opt("when_role", None)
    try:
        when_role = str(role_raw).strip().lower() if role_raw else None
        if when_role == "":
            when_role = None
    except Exception:
        when_role = None

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
        when_every_n_turns=when_every_n_turns,
        when_min_turn=when_min_turn,
        when_message_contains=when_message_contains,
        when_role=when_role,
        runner=runner,
        external_cmd=str(external_cmd) if external_cmd is not None else None,
    )


def _should_run_hook(session, spec: HookSpec, extras: Optional[Dict[str, Any]]) -> bool:
    """Apply simple gating rules to decide if a hook should run this turn."""
    # Short circuit when no gating is configured
    if not any(
        [
            spec.when_every_n_turns,
            spec.when_min_turn,
            spec.when_message_contains,
            spec.when_role,
        ]
    ):
        return True

    try:
        chat = session.get_context("chat")
        history = chat.get("all") if chat else []
    except Exception:
        history = []
    if not history:
        return False

    # Locate the current user turn (latest user message)
    user_turn = None
    try:
        for turn in reversed(history):
            if str(turn.get("role", "")).lower() == "user":
                user_turn = turn
                break
    except Exception:
        user_turn = None
    if user_turn is None:
        return False

    # Compute 1-based count of user turns up to and including this one
    user_index = 0
    try:
        for turn in history:
            if str(turn.get("role", "")).lower() == "user":
                user_index += 1
            if turn is user_turn:
                break
    except Exception:
        pass

    # Role gating (future-proof for non-user cases)
    if spec.when_role:
        try:
            if str(user_turn.get("role", "")).lower() != spec.when_role:
                return False
        except Exception:
            return False

    # Minimum turn threshold
    if spec.when_min_turn and user_index < spec.when_min_turn:
        return False

    # Every-N rule
    if spec.when_every_n_turns and user_index % spec.when_every_n_turns != 0:
        return False

    # Message substring match (case-insensitive)
    if spec.when_message_contains:
        text = ""
        try:
            if extras and isinstance(extras.get("input_text"), str):
                text = extras["input_text"]
        except Exception:
            text = ""
        if not text:
            try:
                text = user_turn.get("message") or ""
            except Exception:
                text = ""
        text_lower = text.lower()
        if not any(item.lower() in text_lower for item in spec.when_message_contains):
            return False

    return True


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
        try:
            hook_span = session.utils.logger.span(
                "hook",
                hook_name=spec.name,
                hook_phase=phase,
                hook_mode=spec.mode,
                hook_runner=spec.runner,
            )
        except Exception:
            from contextlib import nullcontext
            hook_span = nullcontext()

        with hook_span:
            if spec.runner == "external":
                result = session.run_external_agent(
                    steps=spec.steps,
                    overrides=overrides or None,
                    contexts=None,
                    cmd=spec.external_cmd,
                )
            else:
                result = session.run_internal_agent(
                    steps=spec.steps,
                    overrides=overrides or None,
                    contexts=None,
                    output="final",
                    verbose_dump=False,
                )
    except Exception as exc:
        # Hooks must never break the main turn; log best-effort and continue.
        try:
            session.utils.logger.log(
                "hook_failed",
                component="core.hooks",
                aspect="errors",
                severity="warning",
                data={"hook": spec.name, "phase": phase, "runner": spec.runner, "error": str(exc)},
            )
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
        if session.get_flag("hooks_disabled", False):
            return
    except Exception:
        pass
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
        if not _should_run_hook(session, spec, shared_extras):
            continue
        # Silent hooks do not need to block the pre-turn path; they can safely
        # run after the provider call using the post_turn phase instead.
        if phase == "pre_turn" and spec.mode == "silent":
            continue
        _run_single_hook(session, spec, phase, shared_extras)
