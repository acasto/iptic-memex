import os
import glob
from typing import List
from base_classes import StepwiseAction, Completed
from utils.tool_args import get_str


class LoadRawAction(StepwiseAction):

    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)

    def _add_files(self, files: List[str]):
        loaded = []
        for f in files:
            if os.path.isfile(f):
                self.session.add_context('raw', f)
                loaded.append(f)
        return loaded

    def start(self, args: list | dict | None = None, content=None) -> Completed:
        # If args provided, treat them as patterns or explicit path
        patterns: List[str] = []
        if isinstance(args, (list, tuple)):
            patterns = [str(a) for a in args]
        elif isinstance(args, dict):
            val = args.get('files') or get_str(args, 'file') or get_str(args, 'pattern') or get_str(args, 'path')
            if isinstance(val, list):
                patterns = [str(x) for x in val]
            elif isinstance(val, str):
                patterns = [val]

        if patterns:
            expanded: List[str] = []
            for p in patterns:
                expanded.extend(glob.glob(p))
            loaded = self._add_files(expanded)
            self.tc.run('chat')
            return Completed({'ok': True, 'loaded': loaded})

        # Interactive: ask for files/patterns
        self.tc.run('file_path')
        try:
            selection = self.session.ui.ask_files("Enter filenames or patterns:", multiple=True)
        except Exception:
            # Fallback to simple text prompt
            raw = self.session.ui.ask_text("Enter filenames or patterns (space-separated):")
            selection = raw.split() if raw else []

        if not selection:
            self.tc.run('chat')
            return Completed({'ok': True, 'loaded': [], 'cancelled': True})

        loaded = self._add_files(selection)
        self.tc.run('chat')
        try:
            self.session.ui.emit('status', {'message': f"Loaded {len(loaded)} file(s) into raw context."})
        except Exception:
            pass
        return Completed({'ok': True, 'loaded': loaded})

    def resume(self, state_token: str, response) -> Completed:
        # Expect response to be list of files or patterns
        if isinstance(response, dict) and 'response' in response:
            response = response['response']
        patterns: List[str] = []
        if isinstance(response, list):
            patterns = [str(x) for x in response]
        elif isinstance(response, str):
            patterns = response.split()
        expanded: List[str] = []
        for p in patterns:
            expanded.extend(glob.glob(p))
        loaded = self._add_files(expanded)
        self.tc.run('chat')
        try:
            self.session.ui.emit('status', {'message': f"Loaded {len(loaded)} file(s) into raw context."})
        except Exception:
            pass
        return Completed({'ok': True, 'loaded': loaded, 'resumed': True})
