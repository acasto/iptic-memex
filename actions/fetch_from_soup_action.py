import requests
from bs4 import BeautifulSoup
from session_handler import InteractionAction
import re


class FetchFromSoupAction(InteractionAction):
    """
    Class for fetching content from web pages with token counting
    """

    def __init__(self, session):
        self.session = session
        self.token_counter = self.session.get_action('count_tokens')

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

                print(f"\nPage Title: {soup.title.string if soup.title else 'No title found'}")
                proceed = input("Do you want to proceed with this page? (y/n): ")
                if proceed.lower() != 'y':
                    continue

                content = None
                token_count = 0  # Initialize token_count here
                while True:
                    print("\nOptions:")
                    print("1. Fetch entire page")
                    print("2. Fetch by CSS selector")
                    print("3. Fetch text only (no HTML)")
                    print("4. Try another URL")
                    choice = input("Enter your choice (1-4): ")

                    if choice == '1':
                        content = str(soup)
                        token_count = self.token_counter.count_tiktoken(content)
                        print(f"\nToken count for entire page: {token_count}")
                        if token_count > 8000:  # Assuming a threshold of 8000 tokens
                            print("Warning: This content is quite large. You might want to consider fetching only text or using a selector.")
                            if input("Do you still want to proceed? (y/n): ").lower() != 'y':
                                continue
                        break
                    elif choice == '2':
                        selector = input("Enter CSS selector: ")
                        elements = soup.select(selector)
                        if elements:
                            content = '\n'.join(str(el) for el in elements)
                            token_count = self.token_counter.count_tiktoken(content)
                            print(f"\nToken count for selected content: {token_count}")
                            break
                        else:
                            print("No elements found with that selector.")
                    elif choice == '3':
                        content = soup.get_text(separator=' ')
                        content = re.sub(r'\s+', ' ', content).strip()
                        token_count = self.token_counter.count_tiktoken(content)
                        print(f"\nToken count for text-only content: {token_count}")
                        break
                    elif choice == '4':
                        break
                    else:
                        print("Invalid choice. Please try again.")

                if choice != '4' and content is not None:
                    print("\nFetched content preview:")
                    print(content[:500] + "..." if len(content) > 500 else content)
                    save = input("\nSave this content? (y/n): ")
                    if save.lower() == 'y':
                        self.session.add_context('web_content', {
                            'name': f'Web Content from {url}',
                            'content': content
                        })
                        print(f"Content saved to context. Total tokens: {token_count}")
                        return

            except requests.RequestException as e:
                print(f"Error fetching the URL: {e}")

    def simple_fetch(self, url, selector=None):
        """
        A simplified version of the fetch method for use by external scripts.
        """
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        try:
            response = requests.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            if selector:
                elements = soup.select(selector)
                if elements:
                    content = '\n'.join(str(el) for el in elements)
                else:
                    return "No elements found with that selector.", 0
            else:
                content = soup.get_text(separator=' ')
                content = re.sub(r'\s+', ' ', content).strip()

            token_count = self.token_counter.count_tiktoken(content)
            return content, token_count

        except requests.RequestException as e:
            return f"Error fetching the URL: {e}", 0
