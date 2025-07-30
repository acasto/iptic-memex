import os
import subprocess
from base_classes import InteractionAction


class LoadImageAction(InteractionAction):
    """Action for loading images into context"""

    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)
        self.supported_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif')

    def run(self, args: list = None):
        """Load and optionally summarize image file(s)"""
        # Check if this is a summary request or if we should fall back to summary
        model = self.session.get_params().get('model')
        force_summary = args and args[0] == "summary"
        use_summary = force_summary or not (model and self.session.get_option_from_model('vision', model))

        if use_summary:
            args = args[1:] if force_summary and len(args) > 1 else args

        # Rest of the file loading logic...
        if not args:
            self.tc.run('image')
            while True:
                filename = self.session.utils.input.get_input(
                    prompt="Enter image filename (or q to exit): ",
                    multiline=False,
                    allow_empty=True
                )

                if filename.lower() == 'q':
                    self.tc.run('chat')
                    break

                if os.path.isfile(filename):
                    if use_summary:
                        self._summarize_image(filename)
                    else:
                        self.session.add_context('image', filename)
                    self.tc.run('chat')
                    break
                else:
                    print(f"Image file '{filename}' not found.")
            return

    def _summarize_image(self, image_path):
        """Helper method to get summary of an image"""
        try:
            vision_prompt = self.session.get_tools().get('vision_prompt')
            vision_model = self.session.get_tools().get('vision_model')
            if not vision_model:
                print("No vision model configured")
                return
            if not vision_prompt:
                print("No vision prompt configured")
                return

            result = subprocess.run(['memex', '-m', vision_model, '-p', vision_prompt, '-f', image_path],
                                    capture_output=True, text=True)
            summary = result.stdout if result.returncode == 0 else f"Error: {result.stderr}"

            # Add the summary as raw text context
            self.session.add_context('multiline_input', {
                'name': f'Description of: {os.path.basename(image_path)}',
                'content': summary
            })

        except subprocess.SubprocessError as e:
            self.session.add_context('assistant', {
                'name': 'file_tool_error',
                'content': f'Failed to get file summary: {str(e)}'
            })

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
