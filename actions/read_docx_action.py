from __future__ import annotations

import os
from io import BytesIO
from base_classes import InteractionAction

try:
    from docx import Document
except Exception:  # pragma: no cover
    Document = None  # type: ignore


class ReadDocxAction(InteractionAction):
    """Extract text from a DOCX and add it as a file context."""

    def __init__(self, session):
        self.session = session

    def process(self, path: str, *, fs_handler=None) -> bool:
        try:
            if Document is None:
                raise RuntimeError('python-docx not available')
            # Read via assistant fs handler when provided; else use utils FS
            if fs_handler:
                data = fs_handler.read_file(path, binary=True)
            else:
                abs_path = self.session.utils.fs.resolve_file_path(path, extension='.docx') or path
                data = self.session.utils.fs.read_file(abs_path, binary=True)
            if data is None:
                return False

            doc = Document(BytesIO(data))
            full_text = "\n".join([p.text for p in doc.paragraphs])
            name = os.path.basename(path)
            self.session.add_context('file', {'name': name, 'content': full_text, 'metadata': {'original_file': path}})
            try:
                self.session.ui.emit('status', {'message': f"Loaded DOCX: {name}"})
            except Exception:
                pass
            return True
        except Exception:
            try:
                self.session.ui.emit('error', {'message': f"Failed to process DOCX: {path}"})
            except Exception:
                pass
            return False

    def run(self, args=None, content=None):
        pass

