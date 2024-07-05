import trafilatura
from trafilatura.settings import use_config
from session_handler import InteractionAction


class FetchFromWebAction(InteractionAction):
    def __init__(self, session):
        self.session = session
        self.token_counter = self.session.get_action('count_tokens')
        self.config = use_config()
        self.config.set("DEFAULT", "EXTRACTION_TIMEOUT", "30")

    def run(self, message=None):
        while True:
            url = input("Enter the URL to fetch (or 'q' to exit): ")
            if url.lower() == 'q':
                return

            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url

            try:
                downloaded = trafilatura.fetch_url(url)
                if downloaded is None:
                    print("Failed to download the web page.")
                    continue

                print("\nOptions:")
                print("1. Fetch main content (text only)")
                print("2. Fetch main content with HTML")
                print("3. Try another URL")
                choice = input("Enter your choice (1-3): ")

                if choice == '1':
                    content = trafilatura.extract(downloaded, config=self.config, include_links=False, include_images=False, output_format='text')
                elif choice == '2':
                    content = trafilatura.extract(downloaded, config=self.config, include_links=True, include_images=True, output_format='html')
                elif choice == '3':
                    continue
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

            except Exception as e:
                print(f"Error fetching the URL: {e}")

    def simple_fetch(self, url):
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        try:
            downloaded = trafilatura.fetch_url(url)
            if downloaded is None:
                return "Failed to download the web page.", 0

            content = trafilatura.extract(downloaded, config=self.config, include_links=False, include_images=False, output_format='text')
            if content:
                content_str = str(content)
                token_count = self.token_counter.count_tiktoken(content_str)
                return content_str, token_count
            else:
                return "No main content could be extracted from this URL.", 0

        except Exception as e:
            return f"Error fetching the URL: {e}", 0
