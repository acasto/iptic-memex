from session_handler import InteractionAction


class ListChatsAction(InteractionAction):

    def __init__(self, conf):
        self.conf = conf

    def run(self):
        pass

# def list_chats(self):
#     """
#     List the chat logs available in the chat directory, used mostly for output to the user
#     """
#     chat_dir = self.conf['DEFAULT'].get('chats_directory', None)
#     if chat_dir is None:
#         return None
#     chat_dir = resolve_directory_path(chat_dir)
#     if chat_dir is None:
#         return None
#     return [f for f in os.listdir(chat_dir) if os.path.isfile(os.path.join(chat_dir, f))]
