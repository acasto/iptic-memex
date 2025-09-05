from __future__ import annotations

import os
import base64
import mimetypes
from io import BytesIO
from base_classes import InteractionAction


IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif')


class ReadImageAction(InteractionAction):
    """Add an image context or, if vision unsupported, add a summary as multiline input."""

    def __init__(self, session):
        self.session = session

    def process(self, path: str, *, fs_handler=None) -> bool:
        try:
            model = self.session.get_params().get('model')
            supports_vision = bool(model and self.session.get_option_from_model('vision', model))

            if supports_vision:
                # If vision supported, prefer adding the image directly.
                # When running inside assistant tool, avoid raw open by passing a dict.
                if fs_handler:
                    data = fs_handler.read_file(path, binary=True)
                    if data is None:
                        return False
                    ext = os.path.splitext(path)[1].lower()
                    mime = self._guess_mime(ext)
                    b64 = base64.b64encode(data).decode('utf-8')
                    self.session.add_context('image', {
                        'name': os.path.basename(path),
                        'content': b64,
                        'mime_type': mime,
                        'source_type': 'base64'
                    })
                else:
                    # Non-tool path: let ImageContext read from path
                    self.session.add_context('image', path)
                try:
                    self.session.ui.emit('status', {'message': f"Loaded image: {os.path.basename(path)}"})
                except Exception:
                    pass
                return True

            # Fallback: generate a summary
            tools = self.session.get_tools()
            vision_model = tools.get('vision_model')
            vision_prompt = tools.get('vision_prompt')
            if not vision_model:
                # No vision summary model configured; treat as plain file path
                return self._fallback_as_file_text(path, fs_handler)

            # Prefer passing bytes to the internal completion via image context
            try:
                if fs_handler:
                    data = fs_handler.read_file(path, binary=True)
                    if data is None:
                        return False
                    ext = os.path.splitext(path)[1].lower()
                    mime = self._guess_mime(ext)
                    b64 = base64.b64encode(data).decode('utf-8')
                    img_ctx = {'name': os.path.basename(path), 'content': b64, 'mime_type': mime, 'source_type': 'base64'}
                    res = self.session.run_internal_completion(
                        message='',
                        overrides={'model': vision_model, 'prompt': vision_prompt} if vision_prompt else {'model': vision_model},
                        contexts=[('image', img_ctx)],
                        capture='text',
                    )
                else:
                    res = self.session.run_internal_completion(
                        message='',
                        overrides={'model': vision_model, 'prompt': vision_prompt} if vision_prompt else {'model': vision_model},
                        contexts=[('image', path)],
                        capture='text',
                    )
                summary = (res.last_text or '').strip()
            except Exception:
                summary = ''

            self.session.add_context('multiline_input', {
                'name': f'Description of: {os.path.basename(path)}',
                'content': summary
            })
            try:
                self.session.ui.emit('status', {'message': f"Loaded image summary: {os.path.basename(path)}"})
            except Exception:
                pass
            return True

        except Exception:
            try:
                self.session.ui.emit('error', {'message': f"Failed to process image: {path}"})
            except Exception:
                pass
            return False

    def _guess_mime(self, ext: str) -> str:
        if ext in IMAGE_EXTS:
            # A few common overrides not always in mimetypes
            if ext in ('.jpg', '.jpeg'):
                return 'image/jpeg'
            if ext == '.png':
                return 'image/png'
            if ext == '.gif':
                return 'image/gif'
            if ext == '.webp':
                return 'image/webp'
            if ext == '.heic':
                return 'image/heic'
            if ext == '.heif':
                return 'image/heif'
        guess, _ = mimetypes.guess_type('x' + ext)
        return guess or 'application/octet-stream'

    def _fallback_as_file_text(self, path: str, fs_handler=None) -> bool:
        # Basic text fallback if no vision model is configured
        try:
            if fs_handler:
                data = fs_handler.read_file(path, binary=False)
            else:
                data = self.session.utils.fs.read_file(path, binary=False)
            if data is None:
                return False
            self.session.add_context('file', {'name': os.path.basename(path), 'content': str(data)})
            return True
        except Exception:
            return False

    def run(self, args=None, content=None):
        pass

