import requests
from bs4 import BeautifulSoup
import trafilatura
from trafilatura.settings import use_config
from base_classes import StepwiseAction, Completed


class FetchFromWebAction(StepwiseAction):
    def __init__(self, session):
        self.session = session
        self.token_counter = self.session.get_action('count_tokens')
        self.config = use_config()
        self.trafilatura_config = use_config()
        self.trafilatura_config.set("DEFAULT", "EXTRACTION_TIMEOUT", "30")

    def start(self, args=None, content: str = "") -> Completed:
        # Ask for URL if not provided
        url = None
        if isinstance(args, dict):
            url = args.get('url')
        elif isinstance(args, (list, tuple)) and args:
            url = args[0]
        if not url:
            url = self.session.ui.ask_text("Enter the URL to fetch (or 'q' to exit): ")
        if str(url).strip().lower() == 'q':
            return Completed({'ok': True, 'cancelled': True})
        if not str(url).startswith(('http://', 'https://')):
            url = 'https://' + str(url)

        # Fetch content
        try:
            response = requests.get(str(url))
            response.raise_for_status()
            downloaded = response.text
        except requests.RequestException as e:
            try:
                self.session.ui.emit('error', {'message': f"Error fetching the URL: {e}"})
            except Exception:
                pass
            return Completed({'ok': False, 'error': 'fetch_failed', 'url': str(url)})

        # Ask for output format
        options = [
            'text_only',
            'structured_main',
            'full_html',
            'by_css_selector',
        ]
        label_map = {
            'text_only': 'Text Only (Cleaned)',
            'structured_main': 'Text with Structure (Main Content)',
            'full_html': 'Full HTML',
            'by_css_selector': 'Fetch by CSS Selector (Advanced)',
        }
        choice = self.session.ui.ask_choice("Choose output format:", [label_map[k] for k in options], default=label_map['text_only'])
        # Map back to key
        key = [k for k, v in label_map.items() if v == choice]
        mode = key[0] if key else 'text_only'

        # If CSS selector, ask for it and continue on resume
        if mode == 'by_css_selector':
            # Raise interaction to capture selector; stash URL in args for resume
            selector = self.session.ui.ask_text("Enter CSS selector:")
            # If blocking, continue below; else Web/TUI will resume with selector
            return self._finalize_from_selection(str(url), downloaded, mode, selector)

        return self._finalize_from_selection(str(url), downloaded, mode)

    def resume(self, state_token: str, response) -> Completed:
        # For Web/TUI, handle response as CSS selector
        if isinstance(response, dict) and 'response' in response:
            response = response['response']
        selector = str(response or '').strip()
        # We need the URL again; ask for it
        url = self.session.ui.ask_text("Enter the URL again to apply the selector:")
        try:
            resp = requests.get(url)
            resp.raise_for_status()
            downloaded = resp.text
        except requests.RequestException as e:
            try:
                self.session.ui.emit('error', {'message': f"Error fetching the URL: {e}"})
            except Exception:
                pass
            return Completed({'ok': False, 'error': 'fetch_failed', 'url': url})
        return self._finalize_from_selection(url, downloaded, 'by_css_selector', selector)

    def fetch_text_only(self, downloaded):
        content = trafilatura.extract(downloaded,
                                      config=self.trafilatura_config,
                                      include_links=False,
                                      include_images=False,
                                      output_format='txt')  # Changed 'text' to 'txt'
        if content:
            content_str = str(content)
            token_count = self.token_counter.count_tiktoken(content_str)
            return content_str, token_count
        else:
            return None, None

    def fetch_text_with_structure(self, downloaded_content):
        content = trafilatura.extract(downloaded_content,
                                      config=self.trafilatura_config,
                                      include_links=True,
                                      include_images=False,
                                      include_tables=True,
                                      include_formatting=True,
                                      output_format='xml',
                                      with_metadata=True,
                                      favor_recall=True
                                      )
        if content:
            # Ensure content is properly converted to string
            content_str = str(content)
            token_count = self.token_counter.count_tiktoken(content_str)
            return content_str, token_count
        else:
            return None, None

    def fetch_full_html(self, soup):
        content = str(soup)
        token_count = self.token_counter.count_tiktoken(content)
        return content, token_count

    def fetch_by_css_selector(self, soup, selector: str):
        elements = soup.select(selector)
        if elements:
            content = '\n'.join(str(el) for el in elements)
            token_count = self.token_counter.count_tiktoken(content)
            return content, token_count
        else:
            return None, None

    # --- Helper to finish and optionally save --------------------------
    def _finalize_from_selection(self, url: str, downloaded_html: str, mode: str, selector: str | None = None) -> Completed:
        soup = BeautifulSoup(downloaded_html, 'html.parser')
        if mode == 'text_only':
            content, _ = self.fetch_text_only(downloaded_html)
        elif mode == 'structured_main':
            content, _ = self.fetch_text_with_structure(downloaded_html)
        elif mode == 'full_html':
            content, _ = self.fetch_full_html(soup)
        elif mode == 'by_css_selector':
            content, _ = self.fetch_by_css_selector(soup, selector or '')
        else:
            content = None

        if content:
            content_str = str(content)
            token_count = self.token_counter.count_tiktoken(content_str)
            # Preview emit
            try:
                self.session.ui.emit('status', {'message': f"Token count: {token_count}"})
                preview = content_str[:500] + ('...' if len(content_str) > 500 else '')
                self.session.ui.emit('status', {'message': 'Fetched content preview:'})
                self.session.ui.emit('status', {'message': preview})
            except Exception:
                pass

            save_now = True
            if getattr(self.session.ui.capabilities, 'blocking', False):
                save_now = bool(self.session.ui.ask_bool("Save this content?", default=True))

            if save_now:
                metadata = trafilatura.extract_metadata(downloaded_html)
                metadata_dict = {}
                if metadata:
                    for attr in dir(metadata):
                        if not attr.startswith('_') and not callable(getattr(metadata, attr)):
                            try:
                                metadata_dict[attr] = getattr(metadata, attr)
                            except Exception:
                                pass
                self.session.add_context('web_content', {
                    'name': f'Web Content from {url}',
                    'content': content_str,
                    'metadata': metadata_dict
                })
                try:
                    self.session.ui.emit('status', {'message': f"Content saved. Total tokens: {token_count}"})
                except Exception:
                    pass
            return Completed({'ok': True, 'url': url, 'saved': save_now, 'mode': mode})
        else:
            try:
                self.session.ui.emit('warning', {'message': 'No main content could be extracted from this URL.'})
            except Exception:
                pass
            return Completed({'ok': False, 'url': url, 'error': 'no_content'})
