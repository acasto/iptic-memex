from base_classes import InteractionAction
from typing import Any, Dict, List, Optional
from core.mode_runner import run_completion
from utils.tool_args import get_str, get_list


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

        # Validate search model during initialization
        initial_model = self.session.get_tools().get('search_model', "sonar")
        self._search_model = self.validate_search_model(initial_model)

    # ---- Dynamic tool registry metadata ----
    @classmethod
    def tool_name(cls) -> str:
        return 'websearch'

    @classmethod
    def tool_aliases(cls) -> list[str]:
        return []

    @classmethod
    def tool_spec(cls, session) -> dict:
        return {
            'args': ['query', 'recency', 'domains', 'mode', 'desc'],
            'description': "Search the web. Provide 'query'. Optional 'mode' can be 'basic' or 'advanced'.",
            'required': ['query'],
            'schema': {
                'properties': {
                    'query': {"type": "string", "description": "Search query text."},
                    'recency': {"type": "string", "description": "Recency filter (e.g., 'day', 'week', 'month')."},
                    'domains': {"type": "string", "description": "Comma-separated domain filter list (e.g., 'example.com,another.com')."},
                    'mode': {"type": "string", "enum": ["basic", "advanced"], "description": "Search mode: 'basic' for simple queries or 'advanced' for deeper analysis."},
                    'content': {"type": "string", "description": "Additional terms appended to the query."},
                    'desc': {"type": "string", "description": "Optional short description for UI/status; ignored by execution.", "default": ""}
                }
            },
            'auto_submit': True,
        }

    def run(self, args: dict, content: str = ""):
        # Check if a mode is specified
        mode = get_str(args, 'mode')
        if mode:
            search_model = self.validate_search_model(mode)
        else:
            # Recheck the search model at runtime in case it was changed
            current_model = self.session.get_tools().get('search_model', self._search_model)
            search_model = self.validate_search_model(current_model)
        
        # Update the search model
        self._search_model = search_model

        # Get query from args or content
        query = get_str(args, 'query', '') or ''
        if content:
            query = f"{query} {content}".strip()

        if not query:
            self.session.add_context('assistant', {
                'name': 'search_error',
                'content': 'No query provided'
            })
            return

        recency = get_str(args, 'recency')
        params = {}

        # Add optional params if specified
        if recency:
            params['search_recency_filter'] = recency

        domains = get_list(args, 'domains')
        if domains:
            params['search_domain_filter'] = domains

        final_query = f"{self._search_prompt}\n\n{query}" if self._search_prompt else query

        # Build overrides for a subsession run
        # Push search params into extra_body so OpenAI-compatible providers forward them
        extra_body: Dict[str, Any] = {}
        if 'search_recency_filter' in params:
            extra_body['search_recency_filter'] = params['search_recency_filter']
        if 'search_domain_filter' in params:
            extra_body['search_domain_filter'] = params['search_domain_filter']

        overrides: Dict[str, Any] = {'model': self._search_model}
        if extra_body:
            overrides['extra_body'] = extra_body

        # Include citations in output when enabled
        include_citations = bool(self.session.get_tools().get('sonar_citations', True))

        # Execute completion internally
        try:
            builder = getattr(self.session, '_builder', None)
            if builder is None:
                raise RuntimeError('Internal builder is unavailable for mode run')

            res = run_completion(
                builder=builder,
                overrides=overrides,
                message=final_query,
                capture=('raw' if include_citations else 'text'),
            )

            summary = res.last_text or ''

            # Attempt to extract citations from raw response (best-effort)
            if include_citations and res.raw is not None:
                try:
                    raw = res.raw
                    content_text = None
                    citations: Optional[List[Any]] = None

                    # Navigate typical OpenAI ChatCompletion shape
                    try:
                        choice0 = getattr(raw, 'choices', [None])[0]
                        if choice0 and hasattr(choice0, 'message'):
                            msg = choice0.message
                            content_text = getattr(msg, 'content', None)
                            # Some backends attach citations at message or top-level
                            citations = getattr(msg, 'citations', None) or getattr(raw, 'citations', None)
                    except Exception:
                        pass

                    # Fall back to dict-like access
                    if citations is None:
                        try:
                            d = raw if isinstance(raw, dict) else getattr(raw, '__dict__', {})
                            # Message dict path
                            if d and 'choices' in d and d['choices']:
                                msgd = d['choices'][0].get('message') or {}
                                citations = msgd.get('citations') or d.get('citations')
                                content_text = msgd.get('content', content_text)
                        except Exception:
                            pass

                    base_text = summary or (content_text or '')
                    if citations and isinstance(citations, (list, tuple)):
                        citation_lines = [str(c) for c in citations]
                        base_text = base_text + ("\n\nCitations:\n" + "\n".join(citation_lines))
                    summary = base_text
                except Exception as e:
                    try:
                        self.session.ui.emit('warning', {'message': f"Citations extraction error: {e}"})
                    except Exception:
                        pass

            self.session.add_context('assistant', {'name': 'Search Results', 'content': summary})
        except Exception as e:
            self.session.add_context('assistant', {'name': 'search_error', 'content': f'Search failed: {e}'})
