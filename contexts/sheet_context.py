from session_handler import InteractionContext

class SheetContext(InteractionContext):
    def __init__(self, session, context_data=None):
        self.session = session
        if context_data is None:
            context_data = {}
        if 'name' not in context_data or context_data['name'] == '':
            context_data['name'] = 'Unnamed Spreadsheet'
        if 'content' not in context_data or context_data['content'] == '':
            context_data['content'] = 'No content provided'
        if 'metadata' not in context_data:
            context_data['metadata'] = {}

        self.sheet_data = context_data

    def get(self):
        return self.sheet_data

    def get_content(self):
        return self.sheet_data['content']

    def get_name(self):
        return self.sheet_data['name']

    def get_metadata(self):
        return self.sheet_data['metadata']
