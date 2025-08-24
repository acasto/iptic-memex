import os
import subprocess
from base_classes import StepwiseAction, Completed
from core.mode_runner import run_completion
from utils.tool_args import get_str, get_bool


class LoadImageAction(StepwiseAction):
    """Action for loading images into context"""

    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)
        self.supported_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif')

    def start(self, args: list | dict | None = None, content=None) -> Completed:
        """Load and optionally summarize image file(s) (Stepwise)."""
        model = self.session.get_params().get('model')
        force_summary = False
        filename = None
        if isinstance(args, (list, tuple)):
            if args and str(args[0]).strip().lower() == 'summary':
                force_summary = True
                args = args[1:]
            if args:
                filename = " ".join(str(a) for a in args)
        elif isinstance(args, dict):
            force_summary = bool(get_bool(args, 'summary', False))
            filename = get_str(args, 'file') or get_str(args, 'path')

        use_summary = force_summary or not (model and self.session.get_option_from_model('vision', model))

        if not filename:
            self.tc.run('image')
            filename = self.session.ui.ask_text("Enter image filename (or q to exit): ")
            if str(filename).strip().lower() == 'q':
                self.tc.run('chat')
                return Completed({'ok': True, 'cancelled': True})

        if not os.path.isfile(str(filename)):
            try:
                self.session.ui.emit('error', {'message': f"Image file '{filename}' not found."})
            except Exception:
                pass
            self.tc.run('chat')
            return Completed({'ok': False, 'error': 'not_found', 'file': filename})

        if use_summary:
            self._summarize_image(str(filename))
            result = {'ok': True, 'mode': 'summary', 'file': str(filename)}
        else:
            self.session.add_context('image', str(filename))
            try:
                self.session.ui.emit('status', {'message': f"Loaded image: {filename}"})
            except Exception:
                pass
            result = {'ok': True, 'mode': 'image', 'file': str(filename)}

        self.tc.run('chat')
        return Completed(result)

    def _summarize_image(self, image_path):
        """Helper method to get summary of an image"""
        try:
            vision_prompt = self.session.get_tools().get('vision_prompt')
            vision_model = self.session.get_tools().get('vision_model')
            if not vision_model:
                try:
                    self.session.ui.emit('error', {'message': 'No vision model configured'})
                except Exception:
                    pass
                return
            if not vision_prompt:
                try:
                    self.session.ui.emit('error', {'message': 'No vision prompt configured'})
                except Exception:
                    pass
                return

            # Prefer internal ModeRunner when available; fallback to memex subprocess
            summary = None
            try:
                builder = getattr(self.session, '_builder', None)
                if builder is not None:
                    result = run_completion(
                        builder=builder,
                        overrides={'model': vision_model, 'prompt': vision_prompt},
                        contexts=[('image', image_path)],
                        message='',
                        capture='text',
                    )
                    summary = result.last_text
            except Exception:
                summary = None

            if not summary:
                try:
                    runner = self.session.get_action('memex_runner')
                    if runner:
                        res = runner.run('-m', vision_model, '-p', vision_prompt, '-f', image_path, check=False)
                        if res and hasattr(res, 'stdout'):
                            summary = res.stdout
                        elif res and hasattr(res, 'returncode') and res.returncode != 0 and hasattr(res, 'stderr'):
                            summary = f"Error: {res.stderr}"
                except Exception:
                    try:
                        result = subprocess.run(['memex', '-m', vision_model, '-p', vision_prompt, '-f', image_path], capture_output=True, text=True)
                        summary = result.stdout if result.returncode == 0 else f"Error: {result.stderr}"
                    except Exception as e2:
                        summary = f"Error: {e2}"

            # Add the summary as raw text context
            self.session.add_context('multiline_input', {
                'name': f'Description of: {os.path.basename(image_path)}',
                'content': summary
            })

        except subprocess.SubprocessError as e:
            self.session.add_context('assistant', {'name': 'file_tool_error', 'content': f'Failed to get file summary: {str(e)}'})

    @staticmethod
    def can_run(session) -> bool:
        """Check if either vision or summary capabilities are available"""
        # Check if a vision model is configured for summaries
        if session.get_tools().get('vision_model'):
            return True

        # Check if current model supports direct vision
        model = session.get_params().get('model')
        if model and session.get_option_from_model('vision', model):
            return True

        return False
