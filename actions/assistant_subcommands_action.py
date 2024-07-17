from session_handler import InteractionAction


# noinspection RegExpDuplicateCharacterInClass
class AssistantSubcommandsAction(InteractionAction):

    def __init__(self, session):
        self.session = session
        self.params = session.get_params()

    def run(self, response: str = None):
        if '```' in response:
            if 'highlighting' in self.params and self.params['highlighting'] is True:
                self.session.get_action('reprint_chat').run()
        if '###DATETIME###' in response:
            import re
            if not re.search(r"['\"'`]###DATETIME###['\"'`]", response):
                # add the date and time to the assistant context
                from datetime import datetime
                timestamp = "Current date and time: " + datetime.now().strftime("%m/%d/%Y, %H:%M:%S")
                self.session.add_context('assistant', {'name': 'assistant_context', 'content': timestamp})
