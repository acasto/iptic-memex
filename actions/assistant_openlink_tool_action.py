from base_classes import InteractionAction
import subprocess
import platform
import shlex


class AssistantOpenlinkToolAction(InteractionAction):
    """
    Action for opening URLs in the system's default browser.

    Simple usage:
    '''
    %%OPENLINK%%
    https://example.com
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
            return ['start', '']  # The empty string is needed for start command
        else:
            # Fallback - try common commands
            for cmd in ['xdg-open', 'open']:
                try:
                    subprocess.run(['which', cmd], check=True, capture_output=True)
                    return [cmd]
                except subprocess.CalledProcessError:
                    continue
            return None

    def run(self, args: dict = None, content: str = ""):
        """Process and open the URL in the default browser"""
        # Extract URL from either content or args
        url = content.strip() if content.strip() else (args.get('url', '') if args else '')

        if not url:
            self.session.add_context('assistant', {
                'name': 'openlink_error',
                'content': "No URL provided"
            })
            return

        # Basic URL validation - ensure it has a protocol
        if not (url.startswith('http://') or url.startswith('https://') or url.startswith('file://')):
            # Assume https if no protocol specified
            url = 'https://' + url

        # Get the system command for opening URLs
        open_cmd = self._get_system_open_command()
        if not open_cmd:
            self.session.add_context('assistant', {
                'name': 'openlink_error',
                'content': "Unable to determine system command for opening URLs"
            })
            return

        try:
            # Execute the command to open the URL
            cmd = open_cmd + [url]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10  # Don't wait too long
            )

            if result.returncode == 0:
                self.session.add_context('assistant', {
                    'name': 'openlink_success',
                    'content': f"Opened URL in default browser: {url}"
                })
            else:
                error_msg = result.stderr.strip() if result.stderr else "Unknown error"
                self.session.add_context('assistant', {
                    'name': 'openlink_error',
                    'content': f"Failed to open URL: {error_msg}"
                })

        except subprocess.TimeoutExpired:
            self.session.add_context('assistant', {
                'name': 'openlink_error',
                'content': "Timeout while trying to open URL"
            })
        except Exception as e:
            self.session.add_context('assistant', {
                'name': 'openlink_error',
                'content': f"Error opening URL: {str(e)}"
            })
