from base_classes import InteractionAction
import subprocess


class AssistantWebsearchToolAction(InteractionAction):
    """
    Action for handling web search operations
    """
    SEARCH_MODELS = {
        'basic': 'sonar',
        'advanced': 'reasoning-pro',
        'pro': 'sonar-pro',
        'reason': 'sonar-reasoning',
        'reasoning': 'sonar-reasoning',  # Adding an alias for clarity
        'reasoning-pro': 'sonar-reasoning-pro',
        'deep-research': 'sonar-deep-research',  # Add the new model
        'sonar': 'sonar',
        'sonar-pro': 'sonar-pro',
        'sonar-reasoning': 'sonar-reasoning',
        'sonar-reasoning-pro': 'sonar-reasoning-pro',
        'sonar-deep-research': 'sonar-deep-research'  # Add the new model
    }

    @staticmethod
    def set_search_model(session, model_name=None):
        """Set the search model in session tools (Stepwise-friendly prompt)."""
        ui = getattr(session, 'ui', None)
        if not model_name:
            options = [
                'basic (sonar) - Basic web search',
                'pro (sonar-pro) - Advanced web search',
                'reasoning (sonar-reasoning) - Reasoning-focused search',
                'reasoning-pro (sonar-reasoning-pro) - Advanced reasoning-focused search',
                'deep-research (sonar-deep-research) - Comprehensive research & analysis',
            ]
            if ui and hasattr(ui, 'ask_choice'):
                choice = ui.ask_choice('Select search model:', options, default=options[0])
                model_map = {
                    options[0]: 'basic',
                    options[1]: 'pro',
                    options[2]: 'reason',
                    options[3]: 'reasoning-pro',
                    options[4]: 'deep-research',
                }
                model_name = model_map.get(choice)
            else:
                model_name = 'basic'

        model_name = (model_name or '').lower()
        if model_name not in AssistantWebsearchToolAction.SEARCH_MODELS:
            if ui:
                try: ui.emit('error', {'message': f"Invalid model: {model_name}"})
                except Exception: pass
            return False

        full_name = AssistantWebsearchToolAction.SEARCH_MODELS[model_name]
        session.get_tools()['search_model'] = full_name
        if ui:
            try: ui.emit('status', {'message': f"Search model set to: {full_name}"})
            except Exception: pass
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
        self.memex_runner = session.get_action('memex_runner')

        # Validate search model during initialization
        initial_model = self.session.get_tools().get('search_model', "sonar")
        self._search_model = self.validate_search_model(initial_model)

    def run(self, args: dict, content: str = ""):
        # Check if a mode is specified
        mode = args.get('mode')
        if mode:
            search_model = self.validate_search_model(mode)
        else:
            # Recheck the search model at runtime in case it was changed
            current_model = self.session.get_tools().get('search_model', self._search_model)
            search_model = self.validate_search_model(current_model)
        
        # Update the search model
        self._search_model = search_model

        # Get query from args or content
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
            # Build the argument list for memex
            memex_args = ['-m', self._search_model]

            for key, value in params.items():
                memex_args.append(f'--{key}')
                if isinstance(value, list):
                    memex_args.append(','.join(value))
                else:
                    memex_args.append(str(value))

            # Check if citations should be included (default to True)
            include_citations = self.session.get_tools().get('sonar_citations', True)

            # Add the raw flag if we want to parse citations
            if include_citations:
                memex_args.append('-r')

            # Add the file argument to read from stdin
            memex_args.extend(['-f', '-'])

            # Run the command using the memex_runner, passing the query as stdin
            result = self.memex_runner.run(*memex_args, input=final_query, text=True, capture_output=True)

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
                        try:
                            self.session.ui.emit('warning', {'message': f"Error parsing raw response: {str(e)}"})
                        except Exception:
                            pass
                        summary = result.stdout

            self.session.add_context('assistant', {
                'name': 'Search Results',
                'content': summary
            })
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            self.session.add_context('assistant', {
                'name': 'search_error',
                'content': f'Search failed: {str(e)}'
            })
