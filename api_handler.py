import importlib
from abc import ABC, abstractmethod
from typing import Any, Generator


# The APIProvider class is an abstract class that defines the methods that
# an API Provider must implement to work with the APIHandler.
class APIProvider(ABC):
    """
    Abstract class for API handlers
    """

    @abstractmethod
    def chat(self, message: Any) -> Any:
        pass

    @abstractmethod
    def stream_chat(self, message: Any) -> Generator[Any, None, None]:
        pass


# The APIHandler class is a factory class that creates an APIProvider object based
# on the provider name passed to it.
class APIHandler(APIProvider):
    """
    Factory class for API providers
    """

    def __init__(self, provider: str, conf: dict):
        self.api_provider = None
        try:
            module = importlib.import_module(f"providers.{provider.lower()}_handler")
            provider_class = getattr(module, f"{provider}Handler")
            self.api_provider = provider_class(conf)
        except (ImportError, AttributeError):
            raise ValueError(f"Provider {provider} not supported")
        except Exception as e:
            print(f"Error initializing API provider: {e}")

    def chat(self, message: Any) -> Any:
        return self.api_provider.chat(message)

    def stream_chat(self, message: Any) -> Generator[Any, None, None]:
        return self.api_provider.stream_chat(message)
