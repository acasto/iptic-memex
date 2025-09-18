import os
import sys
sys.path.insert(0, os.getcwd())

from tui.output_bridge import OutputBridge
from tui.widgets.chat_transcript import ChatTranscript
from tui.output_sink import OutputEvent


def test_tool_event_inserts_before_last_assistant():
    chat = ChatTranscript()
    bridge = OutputBridge()
    bridge.set_chat_view(chat)

    # Seed with a user and assistant bubble
    chat.add_message('user', 'hello')
    last_id = chat.add_message('assistant', 'world', streaming=False)

    # Simulate that active_message_id is no longer available (turn finished)
    active_id = None

    # Emit a tool-originated status line; should insert before last assistant
    ev = OutputEvent(type='write', text='tool-line', level='info', is_stream=False, origin='tool')
    bridge.handle_output_event(ev, active_id)

    # Ensure the inserted message sits before the assistant bubble
    # Find indices
    ids = [e.msg_id for e in chat.entries]
    roles = [e.role for e in chat.entries]
    # The tool entry immediately precedes the assistant entry
    idx_assistant = roles.index('assistant')
    assert idx_assistant > 0
    assert roles[idx_assistant - 1] == 'tool'


def test_tool_events_group_into_single_bubble():
    chat = ChatTranscript()
    bridge = OutputBridge()
    bridge.set_chat_view(chat)

    # Seed assistant message and simulate active id
    active_id = chat.add_message('assistant', '', streaming=True)

    # First tool event creates the bubble
    ev1 = OutputEvent(type='write', text='First', level='info', origin='tool')
    bridge.handle_output_event(ev1, active_id)

    # Second tool event should append, not create new bubble
    ev2 = OutputEvent(type='write', text='Second', level='info', origin='tool')
    bridge.handle_output_event(ev2, active_id)

    tool_entries = [e for e in chat.entries if e.role == 'tool']
    assert len(tool_entries) == 1
    assert 'First' in tool_entries[0].text
    assert 'Second' in tool_entries[0].text
