import requests
from session_handler import InteractionAction


class BraveSearchAction(InteractionAction):
    """
    Class for performing web searches using the Brave Search API
    """

    def __init__(self, session):
        self.session = session
        self.brave = session.conf.get_all_options_from_provider("Brave")

    def run(self, message=None):
        search = input("Search query (or q to exit): ")
        if search.lower() == "q":
            return False
        else:
            results = self.search(search)
            print(f"Results: {results}")
            quit()
            if results:
                self.session.add_context('search_results', results)
            return

    def search(self, query):
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self.brave["api_key"]
        }

        params = {
            "q": query,
            "count": 5  # Limit to 5 results for brevity
        }

        response = requests.get(self.brave["endpoint"], headers=headers, params=params)

        if response.status_code == 200:
            return response.json()
        else:
            print(f"Error: {response.status_code} - {response.text}")
            return None

    def display_results(self, results):
        if not results:
            print("No results found.")
            return

        if 'web' in results and 'results' in results['web']:
            print("\nWeb Results:")
            for result in results['web']['results']:
                print(f"\nTitle: {result['title']}")
                print(f"URL: {result['url']}")
                print(f"Description: {result['description']}")
                print("-" * 50)

        # if 'mixed' in results:
        #     print("\nMixed Results Structure:")
        #     mixed = results['mixed']
        #
        #     print(f"Type: {mixed.get('type', 'Not specified')}")
        #
        #     for section in ['main', 'top', 'side']:
        #         if section in mixed:
        #             print(f"\n{section.capitalize()}:")
        #             for item in mixed[section]:
        #                 item_type = item.get('type', 'Unknown type')
        #                 item_index = item.get('index', 'No index')
        #                 item_all = item.get('all', 'Not specified')
        #                 print(f"- Type: {item_type}, Index: {item_index}, All: {item_all}")

        if 'faq' in results and 'results' in results['faq']:
            print("\nFAQs:")
            for faq in results['faq']['results']:
                print(f"Q: {faq['question']}")
                print(f"A: {faq['answer']}")
                print("-" * 30)
