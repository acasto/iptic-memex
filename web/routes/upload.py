"""Stage browser attachments until the owning submission takes them."""

from pathlib import Path
from tempfile import TemporaryDirectory

from starlette.responses import JSONResponse

from web.routes.chat import error


async def api_upload(request, state):
    try:
        async with request.form() as form:
            files = form.getlist('files')
            if not files:
                return error('No files in form', 400)
            root = Path(__file__).resolve().parents[1] / 'uploads'
            root.mkdir(exist_ok=True)
            batch = TemporaryDirectory(dir=root)
            saved = []
            try:
                for index, upload in enumerate(files):
                    name = ''.join(c for c in upload.filename if c.isalnum() or c in '-_. ')
                    if not name.strip('. '):
                        return error('A filename is required', 400)
                    folder = Path(batch.name) / str(index)
                    folder.mkdir()
                    path = folder / name
                    with path.open('xb') as output:
                        while chunk := await upload.read(1024 * 1024):
                            output.write(chunk)
                    saved.append({'name': name, 'path': str(path), 'size': path.stat().st_size})
                token = state.issue_token('upload', 1, 'upload', {
                    'batch': batch, 'paths': [item['path'] for item in saved],
                })
                batch = None  # The token owns cleanup from here.
            finally:
                if batch:
                    batch.cleanup()
    except (ValueError, AttributeError, OSError) as exc:
        return error(str(exc), 400)
    return JSONResponse({'ok': True, 'files': saved, 'upload_token': token})


class Upload:
    def __init__(self, state, token):
        self.directory, self.paths = state.take_upload(token)

    def load(self, session):
        # Load all contexts before the same run takes its transcript snapshot.
        existing = {kind: list(contexts) for kind, contexts in session.context.items()}
        try:
            result = session.get_action('load_file').run({'files': self.paths})
            if result.payload.get('loaded') != self.paths:
                raise ValueError('Could not load every attachment')
            return result
        except BaseException:
            session.context.clear()
            session.context.update(existing)
            raise
        finally:
            self.close()

    def close(self):
        self.directory.cleanup()
