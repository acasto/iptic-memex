import requests
from base_classes import StepwiseAction, Completed


class BraveSearchAction(StepwiseAction):
    """
    Class for performing web searches using the Brave Search API
    """

    def __init__(self, session):
        self.session = session
        self.brave = session.conf.get_all_options_from_provider("Brave")

    def start(self, args=None, content: str = "") -> Completed:
        query = None
        if isinstance(args, dict):
            query = args.get('query')
        elif isinstance(args, (list, tuple)) and args:
            query = " ".join(str(a) for a in args)
        if not query:
            query = self.session.ui.ask_text("Search query (or q to exit): ")
        if str(query).strip().lower() == 'q':
            return Completed({'ok': True, 'cancelled': True})
        results = self.search(str(query))
        if results is None:
            try:
                self.session.ui.emit('error', {'message': 'Search failed.'})
            except Exception:
                pass
            return Completed({'ok': False})
        # Emit a brief summary
        try:
            self._emit_summary(results)
        except Exception:
            pass
        # Save to context automatically in Web, confirm in CLI
        save_now = True
        if getattr(self.session.ui.capabilities, 'blocking', False):
            save_now = bool(self.session.ui.ask_bool('Save results to context?', default=True))
        if save_now:
            self.session.add_context('search_results', results)
        return Completed({'ok': True, 'saved': save_now})

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
            try:
                self.session.ui.emit('error', {'message': f"Error: {response.status_code} - {response.text}"})
            except Exception:
                pass
            return None

    def _emit_summary(self, results):
        if not results:
            try:
                self.session.ui.emit('status', {'message': 'No results found.'})
            except Exception:
                pass
            return

        if 'web' in results and 'results' in results['web']:
            lines = ['Web Results:']
            for result in results['web']['results']:
                lines.append(f"\nTitle: {result.get('title','')}")
                lines.append(f"URL: {result.get('url','')}")
                lines.append(f"Description: {result.get('description','')}")
                lines.append('-' * 50)
            try:
                self.session.ui.emit('status', {'message': "\n".join(lines)})
            except Exception:
                pass

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
            lines = ['\nFAQs:']
            for faq in results['faq']['results']:
                lines.append(f"Q: {faq.get('question','')}")
                lines.append(f"A: {faq.get('answer','')}")
                lines.append('-' * 30)
            try:
                self.session.ui.emit('status', {'message': "\n".join(lines)})
            except Exception:
                pass
