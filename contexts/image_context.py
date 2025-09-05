import base64
from pathlib import Path
from base_classes import InteractionContext


class ImageContext(InteractionContext):
    """Handles image files for provider input"""

    SUPPORTED_MIME_TYPES = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
        '.heic': 'image/heic',
        '.heif': 'image/heif'
    }

    def __init__(self, session, image_path=None):
        self.session = session
        self.context = {
            'name': '',
            'content': '',
            'mime_type': '',
            'source_type': 'base64'
        }
        if image_path:
            # Accept either a path string or a preconstructed dict with base64 and metadata
            if isinstance(image_path, dict):
                name = image_path.get('name') or ''
                content = image_path.get('content') or ''
                mime = image_path.get('mime_type') or ''
                source_type = image_path.get('source_type') or 'base64'
                self.context.update({'name': name, 'content': content, 'mime_type': mime, 'source_type': source_type})
            else:
                self.process_image(image_path)

    def process_image(self, image_path):
        """Process image file into base64 and extract metadata"""
        path = Path(image_path)
        if not path.exists():
            raise ValueError(f"Image file not found: {image_path}")

        # Check file type
        extension = path.suffix.lower()
        if extension not in self.SUPPORTED_MIME_TYPES:
            raise ValueError(f"Unsupported image type: {extension}")

        # Get MIME type
        self.context['mime_type'] = self.SUPPORTED_MIME_TYPES[extension]

        # Convert to base64
        try:
            with open(path, 'rb') as img:
                base64_data = base64.b64encode(img.read()).decode('utf-8')
                self.context['content'] = base64_data
                self.context['name'] = path.name
        except Exception as e:
            raise ValueError(f"Failed to process image: {str(e)}")

    def get(self):
        """Return the context with image metadata"""
        return self.context
