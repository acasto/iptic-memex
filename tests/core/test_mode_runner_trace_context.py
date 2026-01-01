from __future__ import annotations

import core.mode_runner as mr


def test_normalize_trace_does_not_propagate_session_identity():
    raw = {
        "trace_id": "t1",
        "span_id": "s1",
        "session_uid": "sess_outer",
        "ui_mode": "chat",
        "hook_name": "h",
    }
    norm = mr._normalize_trace(raw)
    assert norm.get("trace_id") == "t1"
    assert norm.get("parent_span_id") == "s1"
    assert norm.get("hook_name") == "h"
    assert "session_uid" not in norm
    assert "ui_mode" not in norm

