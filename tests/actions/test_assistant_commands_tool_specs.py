from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import types
from actions.assistant_commands_action import AssistantCommandsAction
from config_manager import ConfigManager
from component_registry import ComponentRegistry
from session import Session


def _make_session() -> Session:
    cfg = ConfigManager()
    sc = cfg.create_session_config()
    reg = ComponentRegistry(sc)
    sess = Session(sc, reg)
    # Normalize gating so tests are deterministic regardless of user config
    sc.set_option('active_tools', '')
    sc.set_option('inactive_tools', '')
    # Basic UI-less session; no need to set model/provider for these tests
    return sess


def _index_specs(specs):
    return {s.get('name'): s for s in specs}


def test_default_cmd_spec_shape_and_required():
    sess = _make_session()
    act = AssistantCommandsAction(sess)
    specs = act.get_tool_specs()
    idx = _index_specs(specs)

    assert 'cmd' in idx
    cmd = idx['cmd']
    assert isinstance(cmd.get('description'), str) and len(cmd['description']) > 0
    params = cmd.get('parameters')
    assert params and params.get('type') == 'object'
    props = params.get('properties') or {}
    # default args + content present
    for k in ('command', 'arguments', 'content'):
        assert k in props
        assert props[k].get('type') == 'string'
    # required inferred for CMD
    required = params.get('required') or []
    assert 'command' in required


def test_content_property_present_for_math():
    sess = _make_session()
    # Ensure MATH is not disabled by default config
    sess.config.set_option('inactive_tools', '')
    act = AssistantCommandsAction(sess)
    specs = _index_specs(act.get_tool_specs())
    assert 'math' in specs
    props = specs['math']['parameters']['properties']
    # MATH has expression arg + content placeholder
    assert 'expression' in props and 'content' in props


def test_file_spec_has_recursive_boolean_and_content():
    sess = _make_session()
    act = AssistantCommandsAction(sess)
    specs = _index_specs(act.get_tool_specs())
    file_spec = specs['file']
    params = file_spec['parameters']
    props = params['properties']
    assert props['recursive']['type'] in ('boolean', 'bool')
    assert 'content' in props


def test_ragsearch_spec_present_and_required_query():
    sess = _make_session()
    act = AssistantCommandsAction(sess)
    specs = _index_specs(act.get_tool_specs())
    assert 'ragsearch' in specs
    rag = specs['ragsearch']
    # Description exists
    assert isinstance(rag.get('description'), str) and rag['description']
    params = rag.get('parameters') or {}
    assert params.get('type') == 'object'
    props = params.get('properties') or {}
    # Expected properties present
    for k in ('query', 'index', 'indexes', 'k', 'preview_lines', 'per_index_cap', 'threshold', 'content'):
        assert k in props
    # query is required
    required = params.get('required') or []
    assert 'query' in required
