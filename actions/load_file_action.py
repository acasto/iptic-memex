# load_file_action.py
import os
import glob
from session_handler import InteractionAction


class LoadFileAction(InteractionAction):
    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)
        self.utils = session.utils

    def run(self, args: list = None):
        """
        Loads a specified file (or files) into the session as a 'file' context.
        If no args are passed, prompts the user repeatedly to enter a filename
        or 'q' to quit. Uses the new input handler to gather user input instead
        of raw input().
        """
        # If the user didn't specify any arguments, prompt them
        if not args:
            # Switch tab completion to 'file_path' mode
            self.tc.run('file_path')

            while True:
                # Use the new input handler for user input
                filename = self.utils.input.get_input(
                    prompt="Enter filename (or q to exit): ",
                    multiline=False,
                    allow_empty=True  # Let them press Enter with no input
                )

                if filename.lower() == 'q':
                    # Switch back to chat mode
                    self.tc.run('chat')
                    break

                # Use glob to find matching files
                files = glob.glob(filename)
                if files:
                    for file in files:
                        if os.path.isfile(file):
                            self.session.add_context('file', file)
                    # Switch tab completion back to chat mode
                    self.tc.run('chat')
                    break
                else:
                    print(f"No files found matching '{filename}'. Please try again.")

            return

        # If there are args, join them into a single filename and load
        filename = ' '.join(args)
        if os.path.isfile(filename):
            self.session.add_context('file', filename)
            self.tc.run('chat')
        else:
            print(f"File '{filename}' not found.")
