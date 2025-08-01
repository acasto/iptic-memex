from base_classes import InteractionContext


class SearchResultsContext(InteractionContext):
    """
    Class for handling web search results
    """

    def __init__(self, session, results=None):
        """
        Initialize the file context
        :param results: the data to process
        """
        self.session = session
        self.file = {}  # dictionary to hold the file name and content
        self.search_results = {'name': 'Web Search Results', 'content': results}

    def get(self):
        """
        Get a formated string of the file content ready to be inserted into the chat
        """
        return self.search_results
