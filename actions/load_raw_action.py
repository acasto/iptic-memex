import os
import glob
from session_handler import InteractionAction


class LoadRawAction(InteractionAction):

    def __init__(self, session):
        self.session = session
        self.tc = session.utils.tab_completion
        self.tc.set_session(session)

    def run(self, args: list = None):
        if not args:
            self.tc.run('file_path')
            while True:
                filename = input(f"Enter filename (or q to exit): ")
                if filename == 'q':
                    self.tc.run('chat')
                    break
                files = glob.glob(filename)
                if files:
                    for file in files:
                        if os.path.isfile(file):
                            self.session.add_context('raw', file)
                    self.tc.run('chat')  # set the completion back to chat mode
                    break
                else:
                    print(f"No files found matching {filename}")
            return

        filename = ' '.join(args)
        if os.path.isfile(filename):
            self.session.add_context('raw', filename)
            self.tc.run('chat')  # set the completion back to chat mode
        else:
            print(f"File {filename} not found.")
