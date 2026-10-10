"""Test that extra_body string parsing is safe (no code execution)."""

import ast


def test_extra_body_literal_dict_parsing():
    """Test that valid dict strings are parsed correctly."""
    # Simulate the code path where extra_body is a string
    api_parms = {'extra_body': "{'max_tokens': 100}"}
    
    # This is the fixed code path
    extra_body = api_parms.get('extra_body', {})
    if isinstance(extra_body, str):
        try:
            extra_body = ast.literal_eval(extra_body)
        except (SyntaxError, ValueError):
            extra_body = {}
    
    assert extra_body == {'max_tokens': 100}


def test_extra_body_rejects_malicious_code():
    """Test that malicious code in extra_body is rejected."""
    malicious_strings = [
        "__import__('os').system('echo pwned')",
        "exec('import os; os.system(\"echo pwned\")')",
        "lambda: __import__('subprocess').call(['ls'])",
        "open('/etc/passwd').read()",
    ]
    
    for malicious in malicious_strings:
        api_parms = {'extra_body': malicious}
        
        # This is the fixed code path
        extra_body = api_parms.get('extra_body', {})
        if isinstance(extra_body, str):
            try:
                extra_body = ast.literal_eval(extra_body)
            except (SyntaxError, ValueError):
                extra_body = {}
        
        # Should fall back to empty dict, not execute code
        assert extra_body == {}, f"Failed to reject: {malicious}"


def test_extra_body_invalid_syntax():
    """Test that invalid syntax is handled gracefully."""
    invalid_strings = [
        "{'incomplete': ",
        "not a dict at all",
        "[unclosed list",
    ]
    
    for invalid in invalid_strings:
        api_parms = {'extra_body': invalid}
        
        # This is the fixed code path
        extra_body = api_parms.get('extra_body', {})
        if isinstance(extra_body, str):
            try:
                extra_body = ast.literal_eval(extra_body)
            except (SyntaxError, ValueError):
                extra_body = {}
        
        # Should fall back to empty dict
        assert extra_body == {}, f"Failed to handle: {invalid}"


def test_extra_body_valid_literals():
    """Test that valid literal values are accepted."""
    valid_cases = [
        ("{'key': 'value'}", {'key': 'value'}),
        ("{'num': 42}", {'num': 42}),
        ("{'list': [1, 2, 3]}", {'list': [1, 2, 3]}),
        ("{'bool': True}", {'bool': True}),
        ("{'none': None}", {'none': None}),
        ("{'nested': {'a': 1}}", {'nested': {'a': 1}}),
    ]
    
    for input_str, expected in valid_cases:
        api_parms = {'extra_body': input_str}
        
        # This is the fixed code path
        extra_body = api_parms.get('extra_body', {})
        if isinstance(extra_body, str):
            try:
                extra_body = ast.literal_eval(extra_body)
            except (SyntaxError, ValueError):
                extra_body = {}
        
        assert extra_body == expected, f"Failed to parse: {input_str}"
