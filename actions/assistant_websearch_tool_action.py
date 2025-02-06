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
        'sonar-reasoning': 'sonar-reasoning',
        'sonar-reasoning-pro': 'sonar-reasoning-pro'
    }

    @staticmethod
    def set_search_model(session, model_name=None):
        """Set the search model in session tools"""
        if not model_name:
            print("\nAvailable search models:")
            print("1. basic (sonar) - Basic web search")
            print("2. pro (sonar-pro) - Advanced web search")
            print("3. reasoning (sonar-reasoning) - Reasoning-focused search")
            print("4. reasoning-pro (sonar-reasoning-pro) - Advanced reasoning-focused search")

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

    def validate_search_model(self, model_name):
        """
        Validate and return the correct search model name
        Returns the default 'sonar' model if invalid
        """
        if not model_name:
            return 'sonar'

        model_name = model_name.lower()
        if model_name in self.SEARCH_MODELS:
            return self.SEARCH_MODELS[model_name]

        # If the full model name is provided (e.g., 'sonar-pro')
        if model_name in self.SEARCH_MODELS.values():
            return model_name

        return 'sonar'  # Default to basic search model

    def __init__(self, session):
        self.session = session
        self._search_prompt = self.session.get_tools().get('search_prompt', None)

        # Validate search model during initialization
        initial_model = self.session.get_tools().get('search_model', "sonar")
        self._search_model = self.validate_search_model(initial_model)

    def run(self, args: dict, content: str = ""):
        # Recheck search model at runtime in case it was changed
        current_model = self.session.get_tools().get('search_model', self._search_model)
        self._search_model = self.validate_search_model(current_model)

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

            # Check if citations should be included (default to True)
            include_citations = self.session.get_tools().get('sonar_citations', True)

            # Add raw flag if we want to parse citations
            if include_citations:
                model_args += ' -r'

            result = subprocess.run(f'echo "{final_query}" | memex {model_args} -f -',
                                    shell=True, capture_output=True, text=True)

            if result.returncode != 0:
                summary = f"Error: {result.stderr}"
            else:
                summary = result.stdout
                # Parse citations if enabled
                if include_citations:
                    try:
                        # The response is a string representation of a ChatCompletion object
                        # Look for the content and citations using string parsing
                        import re

                        # Extract content using regex
                        content_match = re.search(r"content='([^']*)'", summary)
                        content = content_match.group(1) if content_match else summary

                        # Extract citations list using regex
                        citations_match = re.search(r"citations=\[(.*?)]", summary)
                        if citations_match:
                            # Split the citations string and clean up each citation
                            citations = [c.strip(" '") for c in citations_match.group(1).split(',')]
                            # Append citations to the content
                            citation_text = "\n\nCitations:\n" + "\n".join(citations)
                            summary = content + citation_text
                        else:
                            summary = content

                    except Exception as e:
                        print(f"Error parsing raw response: {str(e)}")
                        summary = result.stdout

            self.session.add_context('assistant', {
                'name': 'Search Results',
                'content': summary
            })
        except subprocess.SubprocessError as e:
            self.session.add_context('assistant', {
                'name': 'search_error',
                'content': f'Search failed: {str(e)}'
            })
