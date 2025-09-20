from __future__ import annotations

import os
import mimetypes
from io import BytesIO
from typing import Iterable

from base_classes import InteractionAction

try:
    from markitdown import MarkItDown
    from markitdown._stream_info import StreamInfo
    from markitdown._exceptions import (
        FileConversionException,
        MissingDependencyException,
        UnsupportedFormatException,
    )
except Exception:  # pragma: no cover
    MarkItDown = None  # type: ignore
    StreamInfo = None  # type: ignore
    FileConversionException = MissingDependencyException = UnsupportedFormatException = Exception  # type: ignore


class MarkitdownAction(InteractionAction):
    """Convert supported files to Markdown and add them to the session context."""

    SUPPORTED_EXTENSIONS: tuple[str, ...] = (
        ".pdf",
        ".docx",
        ".xlsx",
        ".xls",
        ".pptx",
        ".msg",
        ".mp3",
        ".wav",
    )

    def __init__(self, session):
        self.session = session
        self._converter: MarkItDown | None = None

    def _get_converter(self) -> MarkItDown | None:
        if MarkItDown is None:
            return None
        if self._converter is None:
            self._converter = MarkItDown(enable_plugins=False)
        return self._converter

    def _is_supported(self, path: str) -> bool:
        ext = os.path.splitext(path)[1].lower()
        return ext in self.SUPPORTED_EXTENSIONS

    def supported_extensions(self) -> Iterable[str]:
        return self.SUPPORTED_EXTENSIONS

    def process(self, path: str, *, fs_handler=None) -> bool:
        if not self._is_supported(path):
            return False

        converter = self._get_converter()
        if converter is None or StreamInfo is None:
            self._emit_error(f"MarkItDown is not available for {path}")
            return False

        try:
            if fs_handler:
                data = fs_handler.read_file(path, binary=True)
            else:
                abs_path = self.session.utils.fs.resolve_file_path(path) or path
                data = self.session.utils.fs.read_file(abs_path, binary=True)
            if data is None:
                return False

            stream = BytesIO(data)
            basename = os.path.basename(path)
            # Prefer cwd-relative display name for LLM context clarity
            try:
                import os as _os
                display_name = _os.path.relpath(_os.path.abspath(path), _os.getcwd())
            except Exception:
                display_name = basename
            extension = os.path.splitext(basename)[1]
            mimetype, _ = mimetypes.guess_type(basename)
            stream_info = StreamInfo(
                filename=basename,
                extension=extension.lower() if extension else None,
                mimetype=mimetype,
            )
            result = converter.convert_stream(stream, stream_info=stream_info)
            markdown = result.markdown or ""

            # Pre-compute token count for downstream emitters (avoids fragile lookups)
            token_count = 0
            try:
                counter = self.session.get_action('count_tokens')
                if counter:
                    token_count = int(counter.count_tiktoken(markdown))
            except Exception:
                token_count = 0

            metadata = {
                "original_file": path,
                "converter": "markitdown",
                "token_count": token_count,
            }
            if getattr(result, "title", None):
                metadata["title"] = result.title

            self.session.add_context(
                "file",
                {
                    "name": display_name,
                    "content": markdown,
                    "metadata": metadata,
                },
            )
            # Index token counts by original absolute path for downstream emitters
            try:
                idx = self.session.get_user_data('__markitdown_index__') or {}
                if not isinstance(idx, dict):
                    idx = {}
                from os.path import abspath as _abspath
                idx[_abspath(path)] = {
                    'display_name': display_name,
                    'token_count': int(token_count or 0),
                }
                self.session.set_user_data('__markitdown_index__', idx)
            except Exception:
                pass
            try:
                # Mode-agnostic action-level diagnostic only; callers emit user-visible status.
                self.session.utils.logger.action_detail(
                    'markitdown_added',
                    {'name': basename, 'original_file': path, 'token_count': token_count},
                    component='actions.markitdown',
                )
            except Exception:
                pass
            return True
        except (FileConversionException, MissingDependencyException, UnsupportedFormatException) as exc:
            self._emit_error(f"MarkItDown failed for {path}: {exc}")
        except Exception:
            self._emit_error(f"Unexpected error converting {path}")
        return False

    def _emit_error(self, message: str) -> None:
        try:
            self.session.utils.output.error(message)
        except Exception:
            pass

    def run(self, args=None, content=None):
        pass
