import os
import glob
from typing import List
from base_classes import StepwiseAction, Completed
from PyPDF2 import PdfReader
from utils.tool_args import get_str


class LoadPdfAction(StepwiseAction):
    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)

    def start(self, args: list | dict | None = None, content=None) -> Completed:
        # Accept args as pattern or list of patterns
        patterns: List[str] = []
        if isinstance(args, (list, tuple)):
            patterns = [str(a) for a in args]
        elif isinstance(args, dict):
            val = args.get('files') or get_str(args, 'file') or get_str(args, 'pattern') or get_str(args, 'path')
            if isinstance(val, list):
                patterns = [str(x) for x in val]
            elif isinstance(val, str):
                patterns = [val]

        if not patterns:
            self.tc.run('file_path')
            pat = self.session.ui.ask_text("Enter PDF filename or pattern (or q to exit): ")
            if str(pat).strip().lower() == 'q':
                self.tc.run('chat')
                return Completed({'ok': True, 'cancelled': True})
            patterns = [str(pat)]

        files: List[str] = []
        for p in patterns:
            files.extend(self._resolve_glob_pattern(p))
        files = [f for f in files if os.path.isfile(f)]
        if not files:
            try:
                self.session.ui.emit('warning', {'message': f"No PDF files found matching: {', '.join(patterns)}"})
            except Exception:
                pass
            self.tc.run('chat')
            return Completed({'ok': False, 'loaded': 0})

        loaded = 0
        for path in files:
            if self._process_and_add_pdf(path):
                loaded += 1
        self.tc.run('chat')
        try:
            self.session.ui.emit('status', {'message': f"Loaded {loaded} PDF(s) into context."})
        except Exception:
            pass
        return Completed({'ok': True, 'loaded': loaded, 'files': files})

    def resume(self, state_token: str, response) -> Completed:
        # Single-step; delegate to start using response as pattern
        pat = None
        if isinstance(response, dict) and 'response' in response:
            pat = response['response']
        elif isinstance(response, str):
            pat = response
        return self.start({'pattern': pat})

    def _resolve_glob_pattern(self, pattern):
        resolved_path = self.session.utils.fs.resolve_file_path(pattern, extension='.pdf')
        if resolved_path and os.path.isfile(resolved_path):
            return [resolved_path]
        glob_pattern = resolved_path if resolved_path else pattern
        if not glob_pattern.endswith('.pdf'):
            if glob_pattern.endswith(os.sep):
                glob_pattern += '*.pdf'
            else:
                glob_pattern += ('*.pdf' if '*' not in glob_pattern else '')
        return glob.glob(glob_pattern)

    @staticmethod
    def _process_pdf(file_path):
        try:
            pdf = PdfReader(file_path)
            text = ""
            for page in pdf.pages:
                # extract_text can return None
                page_text = page.extract_text() or ""
                text += page_text + "\n"
            metadata = {}
            if getattr(pdf, 'metadata', None):
                metadata = {k: str(v) for k, v in pdf.metadata.items()}
            return {
                'name': os.path.basename(file_path),
                'content': text,
                'metadata': metadata
            }
        except Exception as e:
            return None

    def _process_and_add_pdf(self, file_path):
        pdf_content = self._process_pdf(file_path)
        if pdf_content:
            try:
                self.session.add_context('pdf', pdf_content)
                try:
                    self.session.ui.emit('status', {'message': f"Added PDF: {file_path}"})
                except Exception:
                    pass
                return True
            except Exception:
                try:
                    self.session.ui.emit('error', {'message': f"Error adding context for {file_path}"})
                except Exception:
                    pass
                return False
        else:
            try:
                self.session.ui.emit('error', {'message': f"Failed to process PDF: {file_path}"})
            except Exception:
                pass
            return False
