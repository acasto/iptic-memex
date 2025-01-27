import os
from session_handler import InteractionAction


class LoadImageAction(InteractionAction):
    """Action for loading images into context"""

    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)
        self.supported_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif')

    def run(self, args: list = None):
        """Load specified image file(s) into session as 'image' context"""
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
                    self.session.add_context('image', filename)
                    self.tc.run('chat')
                    break
                else:
                    print(f"Image file '{filename}' not found.")
            return

        # If args provided, try to load the image
        filename = ' '.join(args)
        if os.path.isfile(filename):
            self.session.add_context('image', filename)
            self.tc.run('chat')
        else:
            print(f"Image file '{filename}' not found.")
