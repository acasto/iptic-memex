from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import types
from actions.assistant_commands_action import AssistantCommandsAction


class FakeSession:
    def get_action(self, name):
        # No user overrides in these tests
        return None

    def get_option(self, section, option, fallback=None):
        # Return fallback to keep defaults for cmd/search tool handlers
        return fallback


def _index_specs(specs):
    return {s.get('name'): s for s in specs}


def test_default_cmd_spec_shape_and_required():
    sess = FakeSession()
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
    sess = FakeSession()
    act = AssistantCommandsAction(sess)
    specs = _index_specs(act.get_tool_specs())
    assert 'math' in specs
    props = specs['math']['parameters']['properties']
    # MATH has expression arg + content placeholder
    assert 'expression' in props and 'content' in props


def test_overrides_description_required_and_schema_properties():
    sess = FakeSession()
    act = AssistantCommandsAction(sess)
    # Inject overrides on FILE command
    act.commands['FILE']['description'] = 'Filesystem operations'
    act.commands['FILE']['required'] = ['mode', 'file', 'recursive']
    act.commands['FILE']['schema'] = {
        'properties': {
            'recursive': {'type': 'boolean'}
        }
    }
    # Ensure arg exists to be overridden
    if 'recursive' not in act.commands['FILE']['args']:
        act.commands['FILE']['args'].append('recursive')

    specs = _index_specs(act.get_tool_specs())
    file_spec = specs['file']
    assert file_spec['description'] == 'Filesystem operations'
    params = file_spec['parameters']
    assert params['required'] == ['mode', 'file', 'recursive']
    props = params['properties']
    assert props['recursive']['type'] == 'boolean'
    # content still present
    assert 'content' in props


def test_ragsearch_spec_present_and_required_query():
    sess = FakeSession()
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
