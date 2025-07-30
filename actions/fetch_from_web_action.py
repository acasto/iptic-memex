import requests
from bs4 import BeautifulSoup
import trafilatura
from trafilatura.settings import use_config
from base_classes import InteractionAction


class FetchFromWebAction(InteractionAction):
    def __init__(self, session):
        self.session = session
        self.token_counter = self.session.get_action('count_tokens')
        self.config = use_config()
        self.trafilatura_config = use_config()
        self.trafilatura_config.set("DEFAULT", "EXTRACTION_TIMEOUT", "30")

    def run(self, message=None):
        while True:
            url = input("Enter the URL to fetch (or 'q' to exit): ")
            if url.lower() == 'q':
                return

            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url

            try:
                response = requests.get(url)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')
                downloaded = response.text  # Store the downloaded content

                print("\nChoose the desired output format:")
                print("1. Text Only (Cleaned)")
                print("2. Text with Structure (Main Content)")
                print("3. Full HTML")
                print("4. Fetch by CSS Selector (Advanced)")
                choice = input("Enter your choice (1-4): ")

                if choice == '1':
                    content, token_count = self.fetch_text_only(downloaded)
                elif choice == '2':
                    content, token_count = self.fetch_text_with_structure(downloaded)
                elif choice == '3':
                    content, token_count = self.fetch_full_html(soup)
                elif choice == '4':
                    content, token_count = self.fetch_by_css_selector(soup)
                else:
                    print("Invalid choice. Please try again.")
                    continue

                if content:
                    # Ensure content is a string
                    content_str = str(content)

                    token_count = self.token_counter.count_tiktoken(content_str)
                    print(f"\nToken count for content: {token_count}")
                    print("\nFetched content preview:")
                    print(content_str[:500] + "..." if len(content_str) > 500 else content_str)

                    save = input("\nSave this content? (y/n): ")
                    if save.lower() == 'y':
                        metadata = trafilatura.extract_metadata(downloaded)
                        metadata_dict = {}
                        if metadata:
                            for attr in dir(metadata):
                                if not attr.startswith('_') and not callable(getattr(metadata, attr)):
                                    metadata_dict[attr] = getattr(metadata, attr)

                        self.session.add_context('web_content', {
                            'name': f'Web Content from {url}',
                            'content': content_str,
                            'metadata': metadata_dict
                        })
                        print(f"Content saved to context. Total tokens: {token_count}")
                        return
                else:
                    print("No main content could be extracted from this URL.")

            except requests.RequestException as e:
                print(f"Error fetching the URL: {e}")

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

    def fetch_by_css_selector(self, soup):
        selector = input("Enter CSS selector: ")
        elements = soup.select(selector)
        if elements:
            content = '\n'.join(str(el) for el in elements)
            token_count = self.token_counter.count_tiktoken(content)
            return content, token_count
        else:
            return None, None
