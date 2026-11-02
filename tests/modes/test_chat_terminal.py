"""Exercise real terminal buffering/echo with a gated offline streaming provider."""

import os
from pathlib import Path
import select
import signal
import re
import subprocess
import sys
import textwrap
import time

import pytest


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='PTY/termios terminal checks')


SCRIPT = textwrap.dedent('''
    import os
    import sys
    from configparser import ConfigParser
    from types import SimpleNamespace
    from app.session_builder import SessionBuilder
    from component_registry import ComponentRegistry
    from config_manager import ConfigManager
    from modes.chat_mode import ChatMode

    control, status = int(sys.argv[1]), int(sys.argv[2])
    confirmation = sys.argv[3] == 'confirm'
    colors = sys.argv[4]

    class Provider:
        def __init__(self, session):
            self.session = session

        def stream_chat(self):
            os.write(status, b'W')
            os.read(control, 1)
            if confirmation:
                assert self.session.ui.ask_bool('Continue?', default=False)
            yield 'First '
            os.write(status, b'F')
            os.read(control, 1)
            yield 'second'

        def get_finish_reason(self):
            return 'stop'

    config = ConfigManager.__new__(ConfigManager)
    config.base_config = ConfigParser()
    config.base_config.read_dict({
        'DEFAULT': {'default_model': 'offline', 'template_handler': 'none',
                    'colors': colors, 'tools': 'false', 'stream': 'true', 'spinner_style': 'block',
                    'user_label': 'You', 'response_label': 'Assistant',
                    'user_label_color': '', 'response_label_color': ''},
        'Fake': {'active': 'true'},
    })
    config.models = ConfigParser()
    config.models.read_dict({'offline': {'provider': 'Fake', 'model_name': 'offline',
                                         'stream': 'true'}})
    ComponentRegistry.load_provider_class = lambda self, name: Provider
    session = SessionBuilder(config).build(mode='chat')
    session.utils._tab_completion = SimpleNamespace(run=lambda *a: None, set_session=lambda *a: None)
    session.handle_exit = lambda **kw: True
    try:
        ChatMode(session).start()
    except KeyboardInterrupt:
        pass
''')


@pytest.mark.parametrize('confirmation,colors,cancel', [
    (False, False, False), (True, False, False),
    (False, True, False), (True, True, False), (False, True, True),
])
def test_chat_flushes_before_stream_end_and_owns_terminal_input(confirmation, colors, cancel):
    import pty
    master, slave = pty.openpty()
    control_read, control_write = os.pipe()
    status_read, status_write = os.pipe()
    process = subprocess.Popen(
        [sys.executable, '-c', SCRIPT, str(control_read), str(status_write),
         'confirm' if confirmation else 'plain', str(colors).lower()],
        cwd=Path(__file__).resolve().parents[2], stdin=slave, stdout=slave, stderr=slave,
        env={**{key: value for key, value in os.environ.items() if key != 'NO_COLOR'},
             'TERM': 'xterm-256color'},
        pass_fds=(control_read, status_write),
    )
    os.close(slave)
    os.close(control_read)
    os.close(status_write)
    transcript = bytearray()

    def plain():
        return re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', bytes(transcript))

    def read_until(needle, count=1):
        deadline = time.monotonic() + 8
        while plain().count(needle) < count:
            if time.monotonic() >= deadline:
                pytest.fail(f'Terminal did not reach {needle!r}: {bytes(transcript)!r}')
            if select.select([master], [], [], 0.1)[0]:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    data = b''
                if not data:
                    pytest.fail(f'Terminal closed early: {bytes(transcript)!r}')
                transcript.extend(data)
        return plain()

    def status(expected):
        assert select.select([status_read], [], [], 8)[0], bytes(transcript)
        assert os.read(status_read, 1) == expected

    try:
        read_until(b'> You:')
        os.write(master, b'Question\n')
        status(b'W')
        if select.select([master], [], [], 0.2)[0]:
            transcript.extend(os.read(master, 65536))
        waiting = plain()
        assert b'Waiting for response' not in waiting
        assert b'> Assistant:' not in waiting
        if colors:
            read_until('▖'.encode())
            assert any(char.encode() in transcript for char in '▖▘▝▗')
        os.write(master, b'stray busy typing\n')
        if cancel:
            os.kill(process.pid, signal.SIGINT)
            final = read_until(b'> You:', count=2)
            assert b'Run cancelled' in final and b'stray busy typing' not in final
            assert transcript.count(b'\x1b[?25l') == transcript.count(b'\x1b[?25h')
            os.write(master, b'\x04')
            assert process.wait(timeout=5) == 0
            return
        os.write(control_write, b'1')
        if confirmation:
            read_until(b'Continue?')
            os.write(master, b'yes\n')
        status(b'F')
        # The provider is still blocked before its second chunk. This would time
        # out with the old unflushed writes, even on a line-buffered real TTY.
        first = read_until(b'> Assistant: First ')
        assert b'second' not in first
        os.write(control_write, b'2')
        final = read_until(b'> You:', count=2)
        assert b'> Assistant: First second' in final
        assert b'stray busy typing' not in final
        if confirmation:
            assert b'yes\r\n' in final  # Input echo is restored at the actual prompt.
        os.write(master, b'\x04')
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for fd in (master, control_write, status_read):
            os.close(fd)


def test_terminal_input_is_restored_after_failure_and_pipes_are_untouched():
    import pty
    import termios
    from ui.terminal_input import TerminalInputGuard
    master, slave = pty.openpty()
    stream = os.fdopen(slave, 'r')
    try:
        original = termios.tcgetattr(slave)
        with pytest.raises(RuntimeError):
            with TerminalInputGuard(stream):
                assert not (termios.tcgetattr(slave)[3] & termios.ECHO)
                raise RuntimeError('Provider failed')
        assert termios.tcgetattr(slave) == original
    finally:
        stream.close()
        os.close(master)
    from io import StringIO
    pipe = StringIO('Queued next input\n')
    with TerminalInputGuard(pipe):
        pass
    assert pipe.read() == 'Queued next input\n'
