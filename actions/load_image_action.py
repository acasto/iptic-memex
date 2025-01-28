import os
import subprocess
from session_handler import InteractionAction


class LoadImageAction(InteractionAction):
    """Action for loading images into context"""

    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)
        self.fs_handler = session.get_action('assistant_fs_handler')
        self.supported_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif')

    def run(self, args: list = None):
        """Load and optionally summarize image file(s)"""
        # Check if this is a summary request
        do_summary = args and args[0] == "summary"
        if do_summary:
            args = args[1:] if len(args) > 1 else None

        # If no args, prompt for image
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
                    if do_summary:
                        self._summarize_image(filename)
                    else:
                        self.session.add_context('image', filename)
                    self.tc.run('chat')
                    break
                else:
                    print(f"Image file '{filename}' not found.")
            return

        # If args provided, try to load the image
        filename = ' '.join(args)
        if os.path.isfile(filename):
            if do_summary:
                self._summarize_image(filename)
            else:
                self.session.add_context('image', filename)
            self.tc.run('chat')
        else:
            print(f"Image file '{filename}' not found.")

    def _summarize_image(self, image_path):
        """Helper method to get summary of an image"""
        resolved_path = self.fs_handler.resolve_path(image_path)
        if resolved_path is None:
            return

        try:
            vision_prompt = self.session.conf.get_option('TOOLS', 'vision_prompt')
            vision_model = self.session.conf.get_option('TOOLS', 'vision_model')
            if not vision_model:
                print("No vision model configured")
                return
            if not vision_prompt:
                print("No vision prompt configured")
                return

            result = subprocess.run(['memex', '-m', vision_model, '-p', vision_prompt, '-f', resolved_path],
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
        model = session.get_params().get('model')
        if not model:
            return False
        return bool(session.conf.get_option_from_model('vision', model))
