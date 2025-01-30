from session_handler import InteractionAction
import subprocess


class AssistantWebsearchToolAction(InteractionAction):
    """
    Action for handling web search operations
    """
    def __init__(self, session):
        self.session = session
        self._search_prompt = self.session.conf.get_option('TOOLS', 'search_prompt', fallback=None)
        self._basic_model = self.session.conf.get_option('TOOLS', 'search_model', fallback="sonar")
        # self._basic_model = 'sonar'
        # self._advanced_model = 'sonar-pro'
        # self._advanced_model = 'sonar-reasoning'

    def run(self, args: dict, content: str = ""):
        query = args.get('query', '')
        if content:
            query = f"{query} {content}".strip()

        if not query:
            self.session.add_context('assistant', {
                'name': 'search_error',
                'content': 'No query provided'
            })
            return

        mode = self.session.get_params().get('mode', 'basic').lower()
        recency = args.get('recency')
        params = {}

        # Select model based on mode
        search_model = self._advanced_model if mode == 'advanced' else self._basic_model

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
            model_args = f"-m {search_model}"
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
