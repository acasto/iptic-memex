import requests
from base_classes import InteractionAction


class BraveSummaryAction(InteractionAction):
    """
    Class for performing web searches using the Brave Search API
    """

    def __init__(self, session):
        self.session = session
        try:
            # Try to get brave config through session params (which includes provider settings)
            params = session.get_params()
            self.brave = {k: v for k, v in params.items() if k.startswith('brave_') or k in ['api_key', 'endpoint']}
            # If empty, try to get from TOOLS section as fallback
            if not self.brave:
                self.brave = {}
                # Get common brave settings
                for key in ['api_key', 'endpoint', 'search_model']:
                    value = session.get_option('TOOLS', f'brave_{key}', fallback=None)
                    if value:
                        self.brave[key] = value
        except Exception:
            self.brave = {}

    def run(self, message=None):
        if 'api_key' not in self.brave:
            print(f"API key not found for Brave provider in configuration.\n")
            return
        while True:
            search = input("Search query (or q to exit): ")
            if search.lower() == "q":
                return
            else:
                summary = self.search_and_summarize(search)
                if summary:
                    concise_summary = self.process_summary(summary)
                    # Let the user preview the summary with the option to try again
                    print(concise_summary)
                    retry = input("\nUse results? (y/n): ")
                    if retry.lower() != "y":
                        continue
                    else:
                        self.session.add_context('search_results', concise_summary)
                        break
        self.session.get_action('reprint_chat').run()

    def simple_search_and_summarize(self, query):
        """
        A simplified version of the search_and_summarize method for use by external scripts.
        """
        summary = self.search_and_summarize(query)
        if summary:
            concise_summary = self.process_summary(summary)
            return concise_summary

    def search_and_summarize(self, query):
        web_results = self.brave_web_search(query)
        if not web_results or 'summarizer' not in web_results:
            print("No summarizer key found in the web search results.")
            return None

        summarizer_key = web_results['summarizer']['key']
        return self.brave_summarizer(summarizer_key)

    def brave_web_search(self, query):
        url = self.brave["endpoint"]
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self.brave["api_key"]
        }

        params = {
            "q": query,
            "spellcheck": False,  # disable spellcheck
            'text_decorations': False,  # disable text decorations
            'result_filter': "summarizer",  # filter results for summarizer
            'extra_snippets': True,  # enable extra snippets
            "summary": True  # enable summary
        }

        response = requests.get(url, headers=headers, params=params)

        if response.status_code == 200:
            return response.json()
        else:
            print(f"Error in web search: {response.status_code} - {response.text}")
            return None

    def brave_summarizer(self, key):
        url = self.brave["summary_endpoint"]
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self.brave["api_key"]
        }

        params = {
            "key": key,
            "entity_info": False,  # disable entity info
        }

        response = requests.get(url, headers=headers, params=params)

        if response.status_code == 200:
            return response.json()
        else:
            print(f"Error in summarizer: {response.status_code} - {response.text}")
            return None

    def process_summary(self, summary_result):
        concise_summary = ""

        if 'title' in summary_result:
            concise_summary += f"Title: {summary_result['title']}\n\n"

        if 'summary' in summary_result:
            if 'data' in summary_result['summary'][0]:
                concise_summary += "Summary:\n"
                concise_summary += f"{summary_result['summary'][0]['data']}\n"
            else:
                concise_summary += "Summary not available.\n"
            concise_summary += "\n"

        return concise_summary.strip()