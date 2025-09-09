from __future__ import annotations

from typing import List, Optional, Any

from base_classes import StepwiseAction, Completed


class SettingShortcutsAction(StepwiseAction):
    """
    Quick setters for common options with provider/model-aware gating.

    Shortcuts:
      - stream: on|off → sets session option 'stream' True/False
      - reasoning: minimal|low|medium|high → sets 'reasoning_effort'

    Notes:
      - Reasoning shortcut is only applicable when the current model is a
        reasoning-capable model for providers that support it (OpenAI, OpenAIResponses)
        AND the model's params indicate reasoning=True. We do not toggle
        'reasoning' on/off via this shortcut.
    """

    VALID_REASONING = ('minimal', 'low', 'medium', 'high')

    def __init__(self, session):
        self.session = session

    @classmethod
    def can_run(cls, session, shortcut: Optional[str] = None, value: Optional[str] = None):
        """Return True/False (or (bool, reason)) for shortcut applicability.

        - stream: always applicable
        - reasoning: applicable for OpenAI/OpenAIResponses when params.reasoning == True
        """
        try:
            sc = (shortcut or '').strip().lower()
            if sc in (None, '', 'stream'):
                return True
            if sc == 'reasoning':
                p = session.get_params()
                provider = str(p.get('provider') or '').strip()
                if provider not in ('OpenAI', 'OpenAIResponses'):
                    return (False, 'Reasoning only applies to OpenAI/OpenAIResponses models')
                if not bool(p.get('reasoning', False)):
                    return (False, 'Current model is not marked as reasoning-capable')
                return True
            if sc in ('temperature', 'top_p'):
                return True
            # Unknown shortcut → not applicable
            return False
        except Exception:
            return False

    @classmethod
    def complete_values(cls, session, shortcut: str, prefix: str = '') -> List[str]:
        sc = (shortcut or '').strip().lower()
        pr = (prefix or '').strip().lower()
        if sc == 'stream':
            opts = ['on', 'off']
            return [o for o in opts if o.startswith(pr)]
        if sc == 'reasoning':
            ok, _ = (cls.can_run(session, 'reasoning'), None)
            if isinstance(ok, tuple):
                ok = bool(ok[0])
            if not ok:
                return []
            provider = str((session.get_params() or {}).get('provider') or '')
            allowed = ('low', 'medium', 'high') if provider == 'OpenAIResponses' else cls.VALID_REASONING
            return [v for v in allowed if v.startswith(pr)]
        if sc == 'temperature':
            opts = ['0.0', '0.2', '0.5', '0.7', '1.0']
            return [o for o in opts if o.startswith(pr)]
        if sc == 'top_p':
            opts = ['0.10', '0.30', '0.50', '0.70', '1.00']
            return [o for o in opts if o.startswith(pr)]
        return []

    # Stepwise entry
    def start(self, args=None, content: str = "") -> Completed:
        # Expect args like ['stream', 'on'] or ['reasoning', 'high']
        tokens: List[str] = []
        if isinstance(args, (list, tuple)):
            tokens = [str(a) for a in args]
        elif isinstance(args, dict):
            sc = args.get('shortcut') or args.get('0')
            val = args.get('value') or args.get('1')
            if sc is not None:
                tokens = [str(sc)] + ([str(val)] if val is not None else [])

        if not tokens:
            try:
                self.session.ui.emit('error', {'message': 'Usage: /set <stream|reasoning|temperature|top_p> <value>'})
            except Exception:
                pass
            return Completed({'ok': False, 'error': 'usage'})

        sc = tokens[0].lower()
        val: Optional[str] = None
        if len(tokens) > 1:
            val = str(tokens[1]).strip().lower()

        if sc == 'stream':
            if not val:
                # Pick via choice
                opts = ['on', 'off']
                cur = 'on' if bool(self.session.get_params().get('stream')) else 'off'
                labels = [f"{o}{' (current)' if o == cur else ''}" for o in opts]
                default_label = next((l for l in labels if l.endswith('(current)')), labels[0])
                choice = self.session.ui.ask_choice('Set streaming:', labels, default=default_label)
                # Extract base token without suffix
                val = str(choice).split(' ', 1)[0].strip().lower()
            if val not in ('on', 'off', 'true', 'false', '1', '0'):
                try:
                    self.session.ui.emit('error', {'message': "Usage: /set stream <on|off>"})
                except Exception:
                    pass
                return Completed({'ok': False, 'error': 'invalid_value', 'shortcut': sc})
            enabled = val in ('on', 'true', '1')
            self.session.set_option('stream', enabled)
            try:
                self.session.ui.emit('status', {'message': f"Streaming {'enabled' if enabled else 'disabled'}"})
            except Exception:
                pass
            return Completed({'ok': True, 'shortcut': sc, 'value': enabled})

        if sc == 'reasoning':
            # Gate again at runtime
            can = self.can_run(self.session, 'reasoning')
            ok = can[0] if isinstance(can, tuple) else bool(can)
            if not ok:
                try:
                    reason = can[1] if isinstance(can, tuple) and len(can) > 1 else 'Not applicable'
                    self.session.ui.emit('error', {'message': f"Reasoning shortcut unavailable: {reason}"})
                except Exception:
                    pass
                return Completed({'ok': False, 'error': 'not_applicable'})
            # Allowed options differ by provider
            p = self.session.get_params()
            provider = str(p.get('provider') or '').strip()
            if provider == 'OpenAIResponses':
                allowed = ('low', 'medium', 'high')
            else:
                allowed = self.VALID_REASONING
            if not val:
                # Prompt with numbered options, marking current
                current = str(p.get('reasoning_effort') or 'medium').lower()
                labels = [f"{o}{' (current)' if o == current else ''}" for o in allowed]
                default_label = next((l for l in labels if l.endswith('(current)')), labels[0])
                choice = self.session.ui.ask_choice('Select reasoning effort:', labels, default=default_label)
                val = str(choice).split(' ', 1)[0].strip().lower()
            if val not in allowed:
                try:
                    self.session.ui.emit('error', {'message': f"Invalid reasoning level for {provider or 'provider'}: {val}"})
                except Exception:
                    pass
                return Completed({'ok': False, 'error': 'invalid_value', 'shortcut': sc})
            # Do not toggle 'reasoning' itself; just set the effort parameter
            self.session.set_option('reasoning_effort', val)
            try:
                self.session.ui.emit('status', {'message': f"Reasoning effort set to {val}"})
            except Exception:
                pass
            return Completed({'ok': True, 'shortcut': sc, 'value': val})

        if sc in ('temperature', 'top_p'):
            # Determine current value and suggest common options
            key = sc
            cur = self.session.get_params().get(key)
            try:
                cur_str = f"{float(cur):.2f}" if cur is not None else None
            except Exception:
                cur_str = None
            if not val:
                options = ['0.0', '0.2', '0.5', '0.7', '1.0'] if sc == 'temperature' else ['0.10', '0.30', '0.50', '0.70', '1.00']
                labels = [f"{o}{' (current)' if cur_str and (o == cur_str or o.startswith(cur_str.rstrip('0').rstrip('.'))) else ''}" for o in options]
                default_label = next((l for l in labels if l.endswith('(current)')), (labels[2] if len(labels) > 2 else labels[0]))
                choice = self.session.ui.ask_choice(f"Set {key}:", labels, default=default_label)
                val = str(choice).split(' ', 1)[0].strip()
            # Validate and clamp
            try:
                fval = float(val)
                if fval < 0.0:
                    fval = 0.0
                if fval > 1.0:
                    fval = 1.0
            except Exception:
                try:
                    self.session.ui.emit('error', {'message': f"Invalid numeric value for {key}: {val}"})
                except Exception:
                    pass
                return Completed({'ok': False, 'error': 'invalid_value', 'shortcut': sc})
            self.session.set_option(key, fval)
            try:
                self.session.ui.emit('status', {'message': f"{key} set to {fval}"})
            except Exception:
                pass
            return Completed({'ok': True, 'shortcut': sc, 'value': fval})

        # Unknown shortcut
        try:
            self.session.ui.emit('error', {'message': f"Unknown shortcut '{sc}'"})
        except Exception:
            pass
        return Completed({'ok': False, 'error': 'unknown_shortcut', 'shortcut': sc})

    def resume(self, state_token: str, response: Any) -> Completed:
        # For simple prompts, just treat response as the missing value and re-enter start
        if isinstance(response, dict) and 'response' in response:
            response = response['response']
        # The original args are in the state passed back by the server
        shortcut = None
        try:
            state = response if isinstance(response, dict) else {}
            # Web passes {'response': value, 'state': {'args': {'argv': [...]}, 'content': None}}
            if 'state' in state:
                argv = (((state.get('state') or {}).get('args') or {}).get('argv') or [])
            else:
                argv = []
            if argv:
                shortcut = str(argv[0])
        except Exception:
            shortcut = None
        if not shortcut:
            return Completed({'ok': False, 'error': 'resume_no_state'})
        # Reconstruct args with provided response as value
        return self.start([shortcut, str(response if not isinstance(response, dict) else response.get('response', ''))])
