from base_classes import InteractionAction
import os
import sys
import time
import re
from pygments import highlight
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.formatters import TerminalFormatter
from pygments.util import ClassNotFound


class UiAction(InteractionAction):

    # Centralized color definitions
    COLORS = {
        'black': '30', 'red': '31', 'green': '32', 'yellow': '33',
        'blue': '34', 'magenta': '35', 'cyan': '36', 'white': '37',
        'bright_black': '90', 'bright_red': '91', 'bright_green': '92',
        'bright_yellow': '93', 'bright_blue': '94', 'bright_magenta': '95',
        'bright_cyan': '96', 'bright_white': '97', 'gray': '90'
    }

    STYLES = {
        'normal': '0', 'bold': '1', 'light': '2', 'italicized': '3',
        'underlined': '4', 'blink': '5'
    }

    def __init__(self, session):
        self.session = session

    def run(self, args=None):
        if args is None:
            args = []

    @staticmethod
    def clear_screen():
        """
        Clear the screen
        """
        if os.name == 'nt':
            os.system('cls')
        else:
            os.system('clear')
        return True

    @staticmethod
    def show_spinner(duration, message="Loading"):
        """
        Display a spinning cursor for the specified duration.

        :param duration: Time in seconds to show the spinner
        :param message: Optional message to display alongside the spinner
        """
        spinners = ['|', '/', '-', '\\']
        end_time = time.time() + duration

        print(f"{message}... ", end='', flush=True)

        while time.time() < end_time:
            for spinner in spinners:
                sys.stdout.write(spinner)
                sys.stdout.flush()
                time.sleep(0.1)
                sys.stdout.write('\b')

        print("Done!")

    @staticmethod
    def get_color_code(color):
        """
        Get the ANSI color code for a given color name or code.

        :param color: Color name or ANSI code
        :return: ANSI color code
        """
        if isinstance(color, int) or (isinstance(color, str) and color.isdigit()):
            return str(color)
        return UiAction.COLORS.get(color.lower(), '37')  # Default to white if color not found

    @staticmethod
    def get_style_code(style):
        """
        Get the ANSI style code for a given style name.

        :param style: Style name
        :return: ANSI style code
        """
        return UiAction.STYLES.get(style.lower(), '0')  # Default to normal if style not found

    @staticmethod
    def color_wrap(text, color='white', style='normal', end=''):
        """
        Wrap text with ANSI color codes.

        :param text: The text to be colored
        :param color: Color of the text (name or code)
        :param style: Style of the text
        :param end: String to append at the end
        :return: Text wrapped with color codes
        """
        color_code = UiAction.get_color_code(color)
        style_code = UiAction.get_style_code(style)

        return f"\033[{style_code};{color_code}m{text}\033[0m{end}"

    @staticmethod
    def return_color(format_string, *args, **kwargs):
        """
        Return a string with colored portions, similar to f-strings.

        :param format_string: The string with color tags
        :param args: Positional arguments for formatting
        :param kwargs: Keyword arguments for formatting
        :return: Formatted string with ANSI color codes
        """
        result = format_string

        # Replace color tags with ANSI codes
        for color, code in UiAction.COLORS.items():
            result = result.replace(f"{{{color}}}", f"\033[{code}m")
            result = result.replace(f"{{/{color}}}", "\033[0m")

        # Handle numeric color codes
        import re
        result = re.sub(r'\{(\d+)', lambda m: f"\033[{m.group(1)}m", result)
        result = re.sub(r'\{/(\d+)', lambda m: "\033[0m", result)

        # Format the string with provided arguments
        return result.format(*args, **kwargs)

    @staticmethod
    def print_color(format_string, *args, end='\n', **kwargs):
        """
        Print text with colored portions, using return_color.

        :param format_string: The string with color tags
        :param args: Positional arguments for formatting
        :param end: String appended after the last value, default a newline
        :param kwargs: Keyword arguments for formatting
        """
        colored_text = UiAction.return_color(format_string, *args, **kwargs)
        print(colored_text, end=end)

    @staticmethod
    def print(text=""):
        """
        Simple text printing function
        """
        print(text)

    @staticmethod
    def format_code_block(text):
        def highlight_block(block):
            match = re.match(r"^```(\w+)?\n(.*?)\n?```$", block, re.DOTALL)
            if match:
                language = match.group(1)
                code_content = match.group(2)
            else:
                return block  # Not a code block, return as is

            try:
                if language:
                    lexer = get_lexer_by_name(language, startinline=(language.lower() == "php"))
                else:
                    lexer = guess_lexer(code_content)
            except ClassNotFound:
                lexer = get_lexer_by_name("text", stripall=True)

            formatter = TerminalFormatter()
            highlighted_code = highlight(code_content, lexer, formatter)
            return f"```{language or ''}\n{highlighted_code}```"

        # Split the text into code blocks and non-code blocks
        parts = re.split(r'(```[\s\S]*?```)', text)

        # Process each part
        formatted_parts = [highlight_block(part) if part.startswith('```') else part for part in parts]

        # Join the parts back together
        return ''.join(formatted_parts)
