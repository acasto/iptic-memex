from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Any, Callable, TypeVar, Dict, Type, Union, List
import sys

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
    - type_check: The expected type
    - constraints: Optional custom validation function
    - error_message: Custom error message for invalid input
    """
    type_check: Type
    constraints: Optional[Callable[[Any], bool]] = None

    error_message: Optional[str] = None


class InputHandler:
    """
    Provides a structured way to gather user input with validation,
    type conversion, and error handling.
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
                int,
                lambda x: True,
                "Please enter a valid integer"
            ),
            float: InputValidator(
                float,
                lambda x: True,
                "Please enter a valid number"
            ),
            bool: InputValidator(
                bool,
                lambda x: isinstance(x, bool) or x.lower() in ('yes', 'no', 'true', 'false', '1', '0'),
                "Please enter yes/no or true/false"
            )
        }

    def _get_validation_level(self) -> Type[InputLevel[Any]] | InputLevel:
        """Load validation level from config."""
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
            allow_empty: bool = False,
            spacing: Optional[Union[int, List[int]]] = None
    ) -> str:
        """
        Get raw input with optional validation and spacing control.

        Args:
            prompt: The input prompt to display
            validator: Optional input validator
            default: Default value if input is empty
            allow_empty: Whether to allow empty input
            spacing: Controls blank lines before and after the prompt.
                    If int: prints same number of lines before and after
                    If list[int]: prints [before, after] lines
                    If None: no extra spacing (default: None)
        """
        # Handle spacing before prompt
        if spacing is not None:
            before_spacing = spacing if isinstance(spacing, int) else spacing[0]
            self._print_spacing(before_spacing)

        while True:
            # Handle default value in prompt
            full_prompt = prompt
            if default is not None:
                full_prompt = f"{prompt} [{default}]: "

            try:
                value = input(full_prompt)

                # Handle empty input
                if not value:
                    if default is not None:
                        value = default
                    break  # Always break on empty input for Ctrl-C handling

                # Validate if needed
                if validator and self.validation_level != InputLevel.RAW:
                    if not self._validate_input(value, validator):
                        continue

                break

            except EOFError:
                raise KeyboardInterrupt
            except KeyboardInterrupt:
                raise

        # Handle spacing after input
        if spacing is not None:
            after_spacing = spacing if isinstance(spacing, int) else spacing[1]
            self._print_spacing(after_spacing)

        return value

    def _validate_input(self, value: Any, validator: InputValidator) -> bool:
        """Apply validation rules based on current validation level."""
        try:
            # Type checking
            converted = validator.type_check(value)

            # Custom constraints if any
            if (self.validation_level == InputLevel.STRICT
                    and validator.constraints
                    and not validator.constraints(converted)):
                if self.output:
                    self.output.error(validator.error_message or "Invalid input")
                return False

            return True

        except (ValueError, TypeError):
            if self.output:
                self.output.error(validator.error_message or "Invalid input")
            return False

    def get_int(self, prompt: str = "", **kwargs) -> int:
        """Get validated integer input."""
        return int(self.get_input(prompt, self.validators[int], **kwargs))

    def get_float(self, prompt: str = "", **kwargs) -> float:
        """Get validated float input."""
        return float(self.get_input(prompt, self.validators[float], **kwargs))

    def get_bool(self, prompt: str = "", **kwargs) -> bool:
        """Get validated boolean input."""
        value = self.get_input(prompt, self.validators[bool], **kwargs).lower()
        return value in ('yes', 'true', '1')
