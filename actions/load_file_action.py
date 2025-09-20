"""Load file action (stepwise-capable).

Converted to StepwiseAction with CLI-backward-compatible behavior.
"""

from __future__ import annotations

import os
import glob
from typing import Any, Dict, List

from base_classes import StepwiseAction, Completed, Updates


class LoadFileAction(StepwiseAction):
    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)
        self.utils = session.utils
        self.token_counter = session.get_action('count_tokens')

    MARKITDOWN_EXTENSIONS = (
        '.pdf',
        '.docx',
        '.xlsx',
        '.xls',
        '.pptx',
        '.msg',
        '.mp3',
        '.wav',
    )

    def _detect_kind(self, path: str) -> str:
        p = path.lower()
        if p.endswith(self.MARKITDOWN_EXTENSIONS):
            return 'markitdown'
        if p.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif')):
            return 'image'
        # csv treated as text/raw
        return 'file'

    def _load_files(self, patterns: List[str]) -> List[str]:
        loaded: List[str] = []
        # Detail log: raw argv/patterns
        try:
            self.session.utils.logger.action_detail(
                'loadfile_inputs',
                {'patterns': list(patterns or [])},
                component='actions.load_file',
            )
        except Exception:
            pass
        # Compute uploads dir (if present) to detect ephemeral uploaded files
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            uploads_dir = os.path.join(project_root, 'web', 'uploads')
            uploads_dir_abs = os.path.abspath(uploads_dir)
        except Exception:
            uploads_dir_abs = None
        # Build set of already-loaded file paths (by original_file metadata or raw name)
        existing: set[str] = set()
        try:
            for item in (self.session.get_contexts('file') or []):
                ctx = item.get('context') if isinstance(item, dict) else item
                data = ctx.get() if hasattr(ctx, 'get') else None
                if isinstance(data, dict):
                    orig = (data.get('metadata') or {}).get('original_file') if isinstance(data.get('metadata'), dict) else None
                    name = data.get('name')
                    if isinstance(orig, str) and orig:
                        existing.add(os.path.abspath(os.path.expanduser(orig)))
                    if isinstance(name, str) and name:
                        # Names for raw files are the input path
                        existing.add(os.path.abspath(os.path.expanduser(name)))
        except Exception:
            pass

        # Expand patterns, normalize and dedupe
        expanded_paths: List[str] = []
        for pat in patterns:
            expanded = os.path.expanduser(pat)
            matches = glob.glob(expanded)
            for path in matches:
                try:
                    expanded_paths.append(os.path.abspath(path))
                except Exception:
                    expanded_paths.append(path)
        # De-duplicate expansions while preserving order
        seen_abs: set[str] = set()
        unique_paths: List[str] = []
        for p in expanded_paths:
            if p in seen_abs:
                continue
            seen_abs.add(p)
            unique_paths.append(p)

        # Detail log: expansion result and existing set size
        try:
            self.session.utils.logger.action_detail(
                'loadfile_expanded',
                {
                    'expanded_count': len(expanded_paths),
                    'unique_count': len(unique_paths),
                    'existing_count': len(existing),
                },
                component='actions.load_file',
            )
        except Exception:
            pass

        for path in unique_paths:
                if os.path.isfile(path):
                    kind = self._detect_kind(path)
                    processed = False
                    if kind == 'markitdown':
                        helper = self.session.get_action('markitdown')
                        if helper:
                            # Skip if already in context
                            abs_path = os.path.abspath(path)
                            if abs_path in existing:
                                try:
                                    self.session.utils.logger.action_detail(
                                        'loadfile_skip_existing', {'path': abs_path}, component='actions.load_file'
                                    )
                                except Exception:
                                    pass
                                processed = True  # treat as success but do not add again
                            else:
                                processed = bool(helper.process(path))
                                if processed:
                                    existing.add(abs_path)
                    elif kind == 'image':
                        helper = self.session.get_action('read_image')
                        if helper:
                            processed = bool(helper.process(path))

                    if not processed:
                        # Default: add as plain text file (path-based, preserves prior behavior)
                        abs_path = os.path.abspath(path)
                        if abs_path in existing:
                            try:
                                self.session.utils.logger.action_detail(
                                    'loadfile_skip_existing', {'path': abs_path}, component='actions.load_file'
                                )
                            except Exception:
                                pass
                            # Already present; skip creating another context, but mark as loaded for UX
                            loaded.append(path)
                            continue
                        ctx = self.session.add_context('file', path)
                        existing.add(abs_path)
                        # If this came from a web upload, adjust metadata and remove temp file
                        try:
                            if uploads_dir_abs and os.path.abspath(path).startswith(uploads_dir_abs):
                                original_name = os.path.basename(path)
                                try:
                                    if hasattr(ctx, 'file') and isinstance(ctx.file, dict):
                                        ctx.file['server_path'] = path
                                        ctx.file['origin'] = 'upload'
                                        ctx.file['name'] = original_name
                                except Exception:
                                    pass
                                try:
                                    _ = self.session.utils.fs.delete_file(path)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                    else:
                        # If processed and was a web upload, remove the temp file
                        try:
                            if uploads_dir_abs and os.path.abspath(path).startswith(uploads_dir_abs):
                                _ = self.session.utils.fs.delete_file(path)
                        except Exception:
                            pass

                    # Emit a context event for the added file (plain text or markitdown-processed)
                    try:
                        import os as _os
                        try:
                            display_name = _os.path.relpath(_os.path.abspath(path), _os.getcwd())
                        except Exception:
                            display_name = os.path.basename(path)
                        tokens = 0
                        if not processed:
                            # Plain file path: content lives in ctx
                            try:
                                if self.token_counter and ctx and hasattr(ctx, 'get'):
                                    data = ctx.get() or {}
                                    content = data.get('content') or ''
                                    if isinstance(content, str):
                                        tokens = int(self.token_counter.count_tiktoken(content))
                            except Exception:
                                tokens = 0
                        else:
                            # MarkItDown path: find the just-added file context by metadata/name
                            try:
                                if self.token_counter:
                                    # Prefer using MarkItDown index set by the converter
                                    md_idx = self.session.get_user_data('__markitdown_index__') or {}
                                    abs_path = _os.path.abspath(path)
                                    if isinstance(md_idx, dict) and abs_path in md_idx:
                                        entry = md_idx.get(abs_path) or {}
                                        tokens = int(entry.get('token_count') or 0)
                                    # If unavailable, search contexts
                                    files = self.session.get_contexts('file') or []
                                    for item in reversed(files):
                                        c = item.get('context') if isinstance(item, dict) else None
                                        d = c.get() if hasattr(c, 'get') else None
                                        if not isinstance(d, dict):
                                            continue
                                        meta = d.get('metadata') if isinstance(d.get('metadata'), dict) else {}
                                        if not tokens and (meta.get('original_file') == abs_path or d.get('name') == display_name):
                                            tokens = int(meta.get('token_count') or 0)
                                            if not tokens:
                                                content = d.get('content') or ''
                                                if isinstance(content, str) and content:
                                                    tokens = int(self.token_counter.count_tiktoken(content))
                                            break
                                    if not tokens:
                                        # Fallback: match by converter + basename
                                        base = _os.path.basename(path)
                                        for item in reversed(files):
                                            c = item.get('context') if isinstance(item, dict) else None
                                            d = c.get() if hasattr(c, 'get') else None
                                            if not isinstance(d, dict):
                                                continue
                                            meta = d.get('metadata') if isinstance(d.get('metadata'), dict) else {}
                                            if meta.get('converter') == 'markitdown':
                                                orig = str(meta.get('original_file') or '')
                                                if orig.endswith(base):
                                                    tokens = int(meta.get('token_count') or 0)
                                                    if not tokens:
                                                        content = d.get('content') or ''
                                                        if isinstance(content, str) and content:
                                                            tokens = int(self.token_counter.count_tiktoken(content))
                                                    break
                                    if not tokens and files:
                                        # Fallback: assume most recent file context came from this conversion
                                        try:
                                            d = files[-1].get('context').get()
                                            content = d.get('content') if isinstance(d, dict) else None
                                            if isinstance(content, str) and content:
                                                tokens = int(self.token_counter.count_tiktoken(content))
                                        except Exception:
                                            pass
                            except Exception:
                                tokens = 0
                        try:
                            self.session.utils.logger.action_detail(
                                'loadfile_emit_context',
                                {
                                    'name': display_name,
                                    'tokens': tokens,
                                    'processed': bool(processed),
                                },
                                component='actions.load_file',
                            )
                        except Exception:
                            pass
                        self.session.ui.emit('context', {
                            'message': f"Added file: {display_name}" + (f" ({tokens} tokens)" if tokens else ''),
                            'origin': 'command',
                            'title': '/load file',
                            'kind': 'file',
                            'name': display_name,
                            'tokens': tokens,
                            'action': 'add',
                        })
                    except Exception:
                        pass

                    loaded.append(path)
        return loaded

    # CLI: run remains supported via StepwiseAction driver
    def start(self, args: Dict | List[str] | None = None, content: Any | None = None) -> Completed | Updates:
        # If args provided, treat them as file patterns
        if args:
            patterns: List[str]
            if isinstance(args, list):
                patterns = args
            elif isinstance(args, dict):
                patterns = args.get('files') or []
                if isinstance(patterns, str):
                    patterns = [patterns]
                if not patterns and 'pattern' in args:
                    patterns = [str(args['pattern'])]
            else:
                patterns = []
            try:
                self.session.utils.logger.action_detail(
                    'loadfile_start', {'argv_count': len(patterns), 'argv': list(patterns)}, component='actions.load_file'
                )
            except Exception:
                pass
            loaded = self._load_files(patterns)
            self.tc.run('chat')
            return Completed({'ok': True, 'loaded': loaded})

        # Interactive path
        self.tc.run('file_path')
        from base_classes import InteractionNeeded
        try:
            filename = self.session.ui.ask_text("Enter filename (or q to exit): ")
        except InteractionNeeded:
            # Propagate to Web/TUI so the server can return needs_interaction
            raise
        except Exception:
            # If UI not set or any other error, fall back to input handler (CLI)
            filename = self.utils.input.get_input(prompt="Enter filename (or q to exit): ")

        if filename.lower().strip() == 'q':
            self.tc.run('chat')
            return Completed({'ok': True, 'loaded': [], 'cancelled': True})

        # In CLI we can loop if nothing found to mirror old behavior
        if isinstance(self.session.ui.__class__.__name__, str) and getattr(self.session.ui, 'ask_text', None):
            # Try once; if no matches in CLI, reprompt until found or quit
            if hasattr(self.session.ui, 'capabilities'):
                pass  # no-op; simple attempt below

        expanded_filename = os.path.expanduser(filename)
        files = glob.glob(expanded_filename)
        if not files:
            # In blocking UIs (CLI), re-prompt synchronously
            is_blocking = False
            try:
                is_blocking = bool(getattr(self.session.ui, 'capabilities', None) and self.session.ui.capabilities.blocking)
            except Exception:
                is_blocking = False
            if is_blocking:
                while True:
                    self.utils.output.warning(f"No files found matching '{filename}'. Please try again.")
                    filename = self.utils.input.get_input(prompt="Enter filename (or q to exit): ")
                    if filename.lower().strip() == 'q':
                        self.tc.run('chat')
                        return Completed({'ok': True, 'loaded': [], 'cancelled': True})
                    expanded_filename = os.path.expanduser(filename)
                    files = glob.glob(expanded_filename)
                    if files:
                        break
            else:
                # Web/TUI will have thrown InteractionNeeded earlier; return empty
                self.tc.run('chat')
                return Completed({'ok': True, 'loaded': []})

        loaded = self._load_files(files)
        self.tc.run('chat')
        return Completed({'ok': True, 'loaded': loaded})

    def resume(self, state_token: str, response: Any) -> Completed | Updates:
        # Expect response to be a filename string or list of patterns
        patterns: List[str] = []
        if isinstance(response, str):
            patterns = [response]
        elif isinstance(response, list):
            patterns = [str(x) for x in response]
        elif isinstance(response, dict):
            # Support nested {'response': value}
            val = response.get('response')
            if isinstance(val, (str, list)):
                if isinstance(val, str):
                    patterns = [val]
                else:
                    patterns = [str(x) for x in val]
            else:
                r = response.get('files') or response.get('pattern') or response.get('filename')
                if isinstance(r, str):
                    patterns = [r]
                elif isinstance(r, list):
                    patterns = [str(x) for x in r]
        try:
            self.session.utils.logger.action_detail(
                'loadfile_resume', {'argv_count': len(patterns), 'argv': list(patterns)}, component='actions.load_file'
            )
        except Exception:
            pass
        loaded = self._load_files(patterns)
        self.tc.run('chat')
        return Completed({'ok': True, 'loaded': loaded, 'resumed': True})
