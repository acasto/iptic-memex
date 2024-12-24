# utils_handler.py
class UtilsHandler:
    def __init__(self, config):
        self.config = config
        self._output = None  # Lazy load output handler
        self._input = None  # Lazy load input handler
        self._fs = None  # Lazy load filesystem handler
        self._storage = None  # Lazy load storage handler
        self._tab_completion = None  # Lazy load tab completion handler

    @property
    def output(self):
        if self._output is None:
            from utils.output_utils import OutputHandler
            self._output = OutputHandler(self.config)
        return self._output

    @property
    def input(self):
        if self._input is None:
            from utils.input_utils import InputHandler
            self._input = InputHandler(self.config, self.output)
        return self._input

    @property
    def fs(self):
        if self._fs is None:
            from utils.filesystem_utils import FileSystemHandler
            self._fs = FileSystemHandler(self.config, self.output)
        return self._fs

    @property
    def storage(self):
        if self._storage is None:
            from utils.storage_utils import StorageHandler
            self._storage = StorageHandler(self.config, self.output)
        return self._storage

    @property
    def tab_completion(self):
        if self._tab_completion is None:
            from utils.tab_completion_utils import TabCompletionHandler
            self._tab_completion = TabCompletionHandler(self.config, self.output)
        return self._tab_completion
