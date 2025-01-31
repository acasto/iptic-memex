from session_handler import InteractionAction
import subprocess


class AssistantWebsearchToolAction(InteractionAction):
    """
    Action for handling web search operations
    """
    SEARCH_MODELS = {
        'basic': 'sonar',
        'pro': 'sonar-pro',
        'reason': 'sonar-reasoning',
        'sonar': 'sonar',
        'sonar-pro': 'sonar-pro',
        'sonar-reasoning': 'sonar-reasoning'
    }

    @staticmethod
    def set_search_model(session, model_name=None):
        """Set the search model in session tools"""
        if not model_name:
            print("\nAvailable search models:")
            print("1. basic (sonar) - Basic web search")
            print("2. pro (sonar-pro) - Advanced web search")
            print("3. reason (sonar-reasoning) - Reasoning-focused search")

            choice = input("\nSelect model (1-3): ").strip()
            model_map = {'1': 'basic', '2': 'pro', '3': 'reason'}
            model_name = model_map.get(choice)
            if not model_name:
                print("Invalid selection")
                return False

        model_name = model_name.lower()
        if model_name not in AssistantWebsearchToolAction.SEARCH_MODELS:
            print(f"Invalid model: {model_name}")
            return False

        full_name = AssistantWebsearchToolAction.SEARCH_MODELS[model_name]
        session.get_tools()['search_model'] = full_name
        print(f"\nSearch model set to: {full_name}")
        print()
        return True

    def __init__(self, session):
        self.session = session
        self._search_prompt = self.session.get_tools().get('search_prompt', None)
        self._search_model = self.session.get_tools().get('search_model', "sonar")

    def run(self, args: dict, content: str = ""):
        # We can take args from the assistant command here (e.g., recency, query) but
        # for simplicity we're currently juts getting the query from content. Leaving
        # this here in case we want to expand the args in the future though.
        query = args.get('query', '')
        if content:
            query = f"{query} {content}".strip()

        if not query:
            self.session.add_context('assistant', {
                'name': 'search_error',
                'content': 'No query provided'
            })
            return

        recency = args.get('recency')
        params = {}

        # Add optional params if specified
        if recency:
            params['search_recency_filter'] = recency

        domains = args.get('domains', '').split(',') if args.get('domains') else None
        if domains:
            filtered_domains = [d.strip() for d in domains if d.strip()]
            if filtered_domains:
                params['search_domain_filter'] = filtered_domains

        final_query = f"{self._search_prompt}\n\n{query}" if self._search_prompt else query

        try:
            model_args = f"-m {self._search_model}"
            for k, v in params.items():
                if isinstance(v, list):
                    v = f"'{','.join(v)}'"
                model_args += f" --{k} {v}"

            result = subprocess.run(f'echo "{final_query}" | memex {model_args} -f -',
                                    shell=True, capture_output=True, text=True)
            summary = result.stdout if result.returncode == 0 else f"Error: {result.stderr}"

            self.session.add_context('assistant', {
                'name': 'Search Results',
                'content': summary
            })
        except subprocess.SubprocessError as e:
            self.session.add_context('assistant', {
                'name': 'search_error',
                'content': f'Search failed: {str(e)}'
            })
