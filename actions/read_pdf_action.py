from __future__ import annotations

import os
from io import BytesIO
from typing import Optional
from base_classes import InteractionAction

try:
    from pypdf import PdfReader  # preferred
except Exception:  # pragma: no cover - fallback
    from PyPDF2 import PdfReader  # type: ignore


class ReadPdfAction(InteractionAction):
    """Extract text from a PDF and add it as a file context."""

    def __init__(self, session):
        self.session = session

    def process(self, path: str, *, fs_handler=None) -> bool:
        try:
            # Read via assistant fs handler when provided; else use utils FS
            if fs_handler:
                data = fs_handler.read_file(path, binary=True)
            else:
                abs_path = self.session.utils.fs.resolve_file_path(path, extension='.pdf') or path
                data = self.session.utils.fs.read_file(abs_path, binary=True)
            if data is None:
                return False

            reader = PdfReader(BytesIO(data))
            text_parts = []
            for page in getattr(reader, 'pages', []) or []:
                try:
                    t = page.extract_text() or ''
                except Exception:
                    t = ''
                if t:
                    text_parts.append(t)
            text = "\n".join(text_parts)
            meta = {}
            try:
                m = getattr(reader, 'metadata', None)
                if m:
                    meta = {k: str(v) for k, v in dict(m).items()}
            except Exception:
                pass

            name = os.path.basename(path)
            self.session.add_context('file', {'name': name, 'content': text, 'metadata': meta})
            try:
                self.session.ui.emit('status', {'message': f"Loaded PDF: {name}"})
            except Exception:
                pass
            return True
        except Exception:
            try:
                self.session.ui.emit('error', {'message': f"Failed to process PDF: {path}"})
            except Exception:
                pass
            return False

    def run(self, args=None, content=None):
        # Not used directly; helpers are invoked programmatically
        pass

