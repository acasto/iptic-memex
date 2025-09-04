from __future__ import annotations

import configparser
import types


def make_cfg(default_model: str | None = None):
    cfg = configparser.ConfigParser()
    cfg['DEFAULT'] = {}
    cfg.add_section('AGENT')
    if default_model is not None:
        cfg.set('AGENT', 'default_model', default_model)
    return cfg


class FakeOutput:
    def write(self, *a, **k):
        pass
    def stop_spinner(self):
        pass
    def spinner(self, *a, **k):
        class _N:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        return _N()


class FakeSession:
    def __init__(self):
        self.context = {}
        self.utils = types.SimpleNamespace(output=FakeOutput())
        self._flags = {}
    def add_context(self, kind, value=None):
        self.context.setdefault(kind, [])
        ctx = types.SimpleNamespace(get=lambda: [], add=lambda *a, **k: None)
        self.context[kind].append(ctx)
        return ctx
    def get_context(self, kind):
        lst = self.context.get(kind) or []
        return lst[0] if lst else None
    def set_flag(self, k, v):
        self._flags[k] = v
    def get_flag(self, k):
        return self._flags.get(k)
    def get_option(self, *a, **k):
        return None
    def get_provider(self):
        return None


class FakeBuilder:
    def __init__(self, cfg):
        self.config_manager = types.SimpleNamespace(base_config=cfg)
        self.last_build_kwargs = None
    def build(self, mode='internal', **options):
        # Simulate SessionBuilder default selection:
        # internal (non-interactive) builds prefer [AGENT].default_model
        eff = dict(options)
        if mode in ('internal', 'completion') and 'model' not in eff:
            if self.config_manager.base_config is not None:
                agent_default = self.config_manager.base_config.get('AGENT', 'default_model', fallback=None)
                if agent_default:
                    eff['model'] = agent_default
        # capture effective overrides used by mode_runner
        self.last_build_kwargs = eff
        return FakeSession()


class FakeTurnRunner:
    def __init__(self, session):
        self.session = session
    class _Res:
        def __init__(self):
            self.last_text = 'OK_FROM_RUNNER'
            self.turns_executed = 1
    def run_user_turn(self, message, options=None):
        return self._Res()
    def run_agent_loop(self, steps, prepare_prompt=None, options=None):
        return self._Res()


def test_internal_completion_uses_agent_default_model(monkeypatch):
    import core.mode_runner as mr
    # Patch TurnRunner so we don't require a real provider
    monkeypatch.setattr(mr, 'TurnRunner', FakeTurnRunner)

    cfg = make_cfg(default_model='gpt-4o-mini')
    builder = FakeBuilder(cfg)

    # Internal completion should use [AGENT].default_model when not overridden
    res_c = mr.run_completion(builder=builder, overrides=None, contexts=None, message='hi', capture='text')
    assert isinstance(res_c.last_text, str) and 'OK_FROM_RUNNER' in res_c.last_text
    assert builder.last_build_kwargs is not None
    assert builder.last_build_kwargs.get('model') == 'gpt-4o-mini'

    # And internal agent should also use [AGENT].default_model
    res_a = mr.run_agent(builder=builder, steps=3, overrides=None, contexts=None, output='final', verbose_dump=False)
    assert isinstance(res_a.last_text, str) and 'OK_FROM_RUNNER' in res_a.last_text
    assert builder.last_build_kwargs is not None
    assert builder.last_build_kwargs.get('model') == 'gpt-4o-mini'


def test_internal_runs_respect_explicit_model_override(monkeypatch):
    import core.mode_runner as mr
    monkeypatch.setattr(mr, 'TurnRunner', FakeTurnRunner)

    cfg = make_cfg(default_model='gpt-4o-mini')
    builder = FakeBuilder(cfg)
    # Agent
    res_a = mr.run_agent(builder=builder, steps=2, overrides={'model': 'haiku'}, contexts=None, output='final', verbose_dump=False)
    assert isinstance(res_a.last_text, str) and 'OK_FROM_RUNNER' in res_a.last_text
    assert builder.last_build_kwargs is not None and builder.last_build_kwargs.get('model') == 'haiku'
    # Completion
    res_c = mr.run_completion(builder=builder, overrides={'model': 'haiku'}, contexts=None, message='hi', capture='text')
    assert isinstance(res_c.last_text, str) and 'OK_FROM_RUNNER' in res_c.last_text
    assert builder.last_build_kwargs is not None and builder.last_build_kwargs.get('model') == 'haiku'
