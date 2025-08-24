from base_classes import InteractionAction
import subprocess
import platform
import shlex
import urllib.parse
from utils.tool_args import get_str, get_list


class AssistantOpenlinkToolAction(InteractionAction):
    """
    Action for opening URLs in the system's default browser.

    Simple usage:
    '''
    %%OPENLINK%%
    https://example.com
    %%END%%
    '''

    Multiple URLs:
    '''
    %%OPENLINK%%
    https://docs.djangoproject.com/en/stable/topics/forms/
    https://docs.djangoproject.com/en/stable/ref/forms/api/
    https://github.com/django/django/issues/12345
    %%END%%
    '''

    Advanced usage (for future enhancement):
    '''
    %%OPENLINK%%
    url="https://example.com"
    browser="chrome"  # optional: specify browser (requires implementation)
    new_tab="true"    # optional: force new tab (requires implementation)
    %%END%%
    '''

    Future Enhancement Notes:
    - Browser specification could be added by detecting common browser executables
    - Cross-platform browser paths:
      * Chrome: "google-chrome" (Linux), "Google Chrome" (macOS), "chrome.exe" (Windows)
      * Firefox: "firefox" (Linux/macOS), "firefox.exe" (Windows)
      * Safari: "Safari" (macOS only)
    - New tab behavior varies by browser and may require browser-specific flags
    - Could use session.get_tools().get('default_browser') for user preference
    - Error handling for missing browsers or invalid URLs could be enhanced
    """

    def __init__(self, session):
        self.session = session

    def _get_system_open_command(self):
        """Get the appropriate system command for opening URLs"""
        system = platform.system().lower()

        if system == 'darwin':  # macOS
            return ['open']
        elif system == 'linux':
            return ['xdg-open']
        elif system == 'windows':
            return ['cmd', '/c', 'start', '']
        else:
            # Fallback - try common commands
            for cmd in ['xdg-open', 'open']:
                try:
                    subprocess.run(['which', cmd], check=True, capture_output=True)
                    return [cmd]
                except subprocess.CalledProcessError:
                    continue
            return None

    def _parse_urls(self, content, args):
        """Parse URLs from content or args, supporting multiple URLs"""
        urls = []

        # First try to get URLs from content (line-separated)
        if content.strip():
            # Split by newlines and clean up each line
            for line in content.strip().split('\n'):
                line = line.strip()
                if line and not line.startswith('#'):  # Skip empty lines and comments
                    urls.append(line)

        # If no URLs in content, try args
        if not urls and args:
            one = get_str(args, 'url')
            many = get_list(args, 'urls')
            if one:
                urls.append(one)
            elif many:
                urls.extend(many)

        return urls

    def _validate_url(self, url):
        """Validate URL for security and safety"""
        if not url:
            return False, "Empty URL"

        # Block potentially problematic schemes
        blocked_schemes = ['file://', 'javascript:', 'data:', 'vbscript:']
        url_lower = url.lower()

        for scheme in blocked_schemes:
            if url_lower.startswith(scheme):
                return False, f"URL scheme '{scheme}' not allowed for security reasons"

        # Basic URL structure validation
        temp_url = url
        if not (url.startswith('http://') or url.startswith('https://')):
            temp_url = 'https://' + url

        try:
            parsed_url = urllib.parse.urlparse(temp_url)
            if not parsed_url.scheme or not parsed_url.netloc:
                return False, "URL missing scheme or host"
        except Exception as e:
            return False, f"Invalid URL format: {str(e)}"

        return True, ""

    def _get_url_description(self, url):
        """Generate a simple description of what's being opened"""
        try:
            parsed = urllib.parse.urlparse(url)
            domain = parsed.netloc
            return f"Opened {domain}: {url}" if domain else f"Opened: {url}"
        except Exception:
            return f"Opened: {url}"

    def run(self, args: dict = None, content: str = ""):
        """Process and open URL(s) in the default browser"""
        # Parse URLs from input
        urls = self._parse_urls(content, args)

        if not urls:
            self.session.add_context('assistant', {
                'name': 'openlink_error',
                'content': "No URLs provided"
            })
            return

        # Limit number of URLs to prevent accidents
        max_urls = 15
        if len(urls) > max_urls:
            self.session.add_context('assistant', {
                'name': 'openlink_error',
                'content': f"Too many URLs provided ({len(urls)}). Maximum allowed is {max_urls}."
            })
            return

        # Get the system command for opening URLs
        open_cmd = self._get_system_open_command()
        if not open_cmd:
            self.session.add_context('assistant', {
                'name': 'openlink_error',
                'content': "Unable to determine system command for opening URLs"
            })
            return

        successful_urls = []
        failed_urls = []

        # Process each URL
        for url in urls:
            # Validate URL before processing
            is_valid, error_msg = self._validate_url(url)
            if not is_valid:
                failed_urls.append(f"{url}: {error_msg}")
                continue

            # Add protocol if missing
            if not (url.startswith('http://') or url.startswith('https://') or url.startswith('file://')):
                url = 'https://' + url

            try:
                # Execute the command to open the URL
                cmd = open_cmd + [url]
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    shell=False
                )

                if result.returncode == 0:
                    successful_urls.append(url)
                else:
                    error_msg = result.stderr.strip() if result.stderr else "Unknown error"
                    failed_urls.append(f"{url}: {error_msg}")

            except subprocess.TimeoutExpired:
                failed_urls.append(f"{url}: Timeout")
            except Exception as e:
                failed_urls.append(f"{url}: {str(e)}")

        # Report results
        if successful_urls and not failed_urls:
            # All succeeded
            if len(successful_urls) == 1:
                description = self._get_url_description(successful_urls[0])
                message = description
            else:
                domains = []
                for url in successful_urls:
                    try:
                        parsed = urllib.parse.urlparse(url)
                        domains.append(parsed.netloc or url)
                    except:
                        domains.append(url)
                message = f"Opened {len(successful_urls)} URLs: {', '.join(domains)}"

            self.session.add_context('assistant', {
                'name': 'openlink_success',
                'content': message
            })
        elif successful_urls and failed_urls:
            # Partial success
            message = f"Opened {len(successful_urls)} URLs successfully. Failed to open {len(failed_urls)}: {'; '.join(failed_urls)}"
            self.session.add_context('assistant', {
                'name': 'openlink_partial',
                'content': message
            })
        else:
            # All failed
            message = f"Failed to open all URLs: {'; '.join(failed_urls)}"
            self.session.add_context('assistant', {
                'name': 'openlink_error',
                'content': message
            })
