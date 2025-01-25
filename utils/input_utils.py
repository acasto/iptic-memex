# input_utils.py
from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Any, Callable, TypeVar, Dict, Type, Union, List

T = TypeVar('T')


class InputLevel(Enum):
    """
    Input validation levels, in ascending order of strictness.
    """
    RAW = 1        # No validation
    BASIC = 2      # Basic type checking
    STRICT = 3     # Full validation with custom rules


@dataclass
class InputValidator:
    """
    Container for input validation rules.
    - type_check: The expected type (callable or type) to convert the raw input
    - constraints: Optional custom validation function that returns True/False
    - error_message: Custom error message if validation fails
    """
    type_check: Callable[[str], Any]
    constraints: Optional[Callable[[Any], bool]] = None
    error_message: Optional[str] = None


class InputHandler:
    """
    Provides a structured way to gather user input with validation,
    type conversion, and error handling. Supports multiline input
    and optional repeated prompting on error.
    """

    def __init__(self, config: Any, output_handler: Optional[Any] = None) -> None:
        """
        Initialize input handler with user configuration.
        Optionally accepts an output handler for error messaging.
        """
        self.config = config
        self.output = output_handler
        self.validation_level = self._get_validation_level()

        # Default validators for common types
        self.validators: Dict[Type, InputValidator] = {
            int: InputValidator(
                type_check=int,
                constraints=lambda x: True,
                error_message="Please enter a valid integer"
            ),
            float: InputValidator(
                type_check=float,
                constraints=lambda x: True,
                error_message="Please enter a valid number"
            ),
            bool: InputValidator(
                type_check=self._convert_to_bool,
                constraints=lambda x: isinstance(x, bool),
                error_message="Please enter yes/no or true/false"
            )
        }

    def _get_validation_level(self) -> InputLevel:
        """Load validation level from config, defaulting to BASIC if invalid."""
        level_str = self.config.get_option('DEFAULT', 'input_validation', fallback='BASIC')
        try:
            return InputLevel[level_str.upper()]
        except KeyError:
            if self.output:
                self.output.warning(f"Invalid input validation level '{level_str}', using BASIC")
            return InputLevel.BASIC

    @staticmethod
    def _print_spacing(count: int) -> None:
        """Print the specified number of blank lines."""
        for _ in range(count):
            print('', file=sys.stdout)

    def get_input(
            self,
            prompt: str = "",
            validator: Optional[InputValidator] = None,
            default: Optional[Any] = None,
            allow_empty: bool = True,
            spacing: Optional[Union[int, List[int]]] = None,
            multiline: bool = False,
            continuation_char: str = "\\",
            retry_on_failure: bool = True
    ) -> str:
        """
        Get user input (single- or multi-line) with optional validation
        and spacing control.

        Args:
            prompt: The input prompt to display to the user
            validator: Optional InputValidator to validate and/or convert the input
            default: If the user just presses enter (i.e. empty string),
                     this default value will be returned
            allow_empty: Whether to allow empty input (no re-prompt if empty)
            spacing: Controls blank lines before/after the prompt.
                     If int: prints that many blank lines before & after
                     If list[int]: prints [before, after] lines
                     If None: no extra spacing
            multiline: If True, allow multiline entry. The user continues to
                       enter lines if they end with `continuation_char`, or
                       an entire line equals that char.
            continuation_char: Character (default: backslash) used for multiline
                               continuation.
            retry_on_failure: If True, will keep prompting on validation error
                              until user provides valid input. If False, a
                              validation error will return an empty string (or
                              throw an exception, depending on usage).
        """

        # Pre- and post-prompt spacing
        before_spacing = 0
        after_spacing = 0
        if spacing is not None:
            if isinstance(spacing, int):
                before_spacing = after_spacing = spacing
            elif isinstance(spacing, list) and len(spacing) == 2:
                before_spacing, after_spacing = spacing

        # Print spacing before prompt
        self._print_spacing(before_spacing)

        # Repeatedly prompt if validation fails and user wants to retry
        while True:
            # Construct the visible prompt string
            full_prompt = prompt
            if default is not None:
                full_prompt = f"{prompt} [{default}]: "

            # Gather raw input (single line or multiline)
            value = self._gather_input(full_prompt, multiline, continuation_char)

            # If user provided nothing and there's a default, use it
            if not value.strip() and default is not None:
                value = str(default)

            # If still empty, handle according to allow_empty
            if not value.strip() and not allow_empty and default is None:
                # Provide an error if user cannot provide empty
                if self.output:
                    self.output.error("Empty input is not allowed.")
                if retry_on_failure:
                    continue
                else:
                    # Return empty anyway if not retrying
                    return ""

            # Perform validation if requested
            if validator and self.validation_level != InputLevel.RAW:
                if not self._attempt_validation(value, validator, retry_on_failure):
                    # If validation fails and user wants to keep trying, continue
                    if retry_on_failure:
                        continue
                    else:
                        # Return empty or raise an error as appropriate
                        return ""

            # Spacing after input
            self._print_spacing(after_spacing)
            return value

    @staticmethod
    def _gather_input(
            prompt: str,
            multiline: bool,
            continuation_char: str
    ) -> str:
        """
        Gather user input from stdin, either single- or multi-line.
        The multiline approach appends lines ending with `continuation_char`
        (or a line that is solely that char) until we hit a termination line.
        """
        if not multiline:
            # Single line input
            try:
                return input(prompt)
            except EOFError:
                raise KeyboardInterrupt
            except KeyboardInterrupt:
                raise
        else:
            # Multiline input
            lines = []
            first_line = True

            while True:
                # Show prompt only on the first line
                current_prompt = prompt if first_line else ""
                first_line = False

                try:
                    line = input(current_prompt)
                except EOFError:
                    raise KeyboardInterrupt
                except KeyboardInterrupt:
                    raise

                # If the user typed the continuation char by itself, break
                if line.strip() == continuation_char:
                    # Could break with an empty line or continue with partial lines
                    break
                # If line ends with the continuation char, append without it
                # and keep going
                elif line.endswith(continuation_char):
                    lines.append(line[:-1])
                    # Add a newline for readability if you wish
                    # lines.append('\n')
                else:
                    # Final line
                    lines.append(line)
                    break

            # Return joined lines
            return "\n".join(lines).strip()

    def _attempt_validation(
            self,
            raw_value: str,
            validator: InputValidator,
            retry_on_failure: bool
    ) -> bool:
        """
        Try converting and validating a raw string with the given validator.
        Returns True if valid, False otherwise.
        """
        try:
            # Attempt type conversion
            converted_value = validator.type_check(raw_value)

            # If STRICT, apply custom constraints
            if (self.validation_level == InputLevel.STRICT and
                    validator.constraints and
                    not validator.constraints(converted_value)):
                if self.output:
                    self.output.error(validator.error_message or "Invalid input.")
                return False
            return True

        except (ValueError, TypeError):
            # Type conversion error
            if self.output:
                self.output.error(validator.error_message or "Invalid input.")
            return False

    @staticmethod
    def _convert_to_bool(value: str) -> bool:
        """
        Convert a string to boolean, supporting yes/no, true/false, 1/0.
        Raises ValueError if it doesn't match known patterns.
        """
        val_lower = value.strip().lower()
        if val_lower in ('yes', 'true', '1'):
            return True
        elif val_lower in ('no', 'false', '0'):
            return False
        else:
            raise ValueError("Invalid boolean input")

    def get_int(self, prompt: str = "", **kwargs) -> int:
        """Convenience method for integer input."""
        return int(self.get_input(prompt, validator=self.validators[int], **kwargs))

    def get_float(self, prompt: str = "", **kwargs) -> float:
        """Convenience method for float input."""
        return float(self.get_input(prompt, validator=self.validators[float], **kwargs))

    def get_bool(self, prompt: str = "", **kwargs) -> bool:
        """Convenience method for boolean input."""
        raw = self.get_input(prompt, validator=self.validators[bool], **kwargs)
        # We rely on the type conversion in the validator to get a bool
        return self._convert_to_bool(raw)
