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
            extension = os.path.splitext(basename)[1]
            mimetype, _ = mimetypes.guess_type(basename)
            stream_info = StreamInfo(
                filename=basename,
                extension=extension.lower() if extension else None,
                mimetype=mimetype,
            )
            result = converter.convert_stream(stream, stream_info=stream_info)
            markdown = result.markdown or ""

            metadata = {
                "original_file": path,
                "converter": "markitdown",
            }
            if getattr(result, "title", None):
                metadata["title"] = result.title

            self.session.add_context(
                "file",
                {
                    "name": basename,
                    "content": markdown,
                    "metadata": metadata,
                },
            )
            try:
                self.session.utils.output.info(f"Loaded via MarkItDown: {basename}")
                try:
                    # Mode-agnostic action-level diagnostic
                    self.session.utils.logger.action_detail(
                        'markitdown_added',
                        {'name': basename, 'original_file': path},
                        component='actions.markitdown',
                    )
                except Exception:
                    pass
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
