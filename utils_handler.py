from utils.output_utils import OutputHandler
from utils.input_utils import InputHandler
from utils.stream_utils import StreamHandler
from utils.filesystem_utils import FileSystemHandler
from utils.storage_utils import StorageHandler
from utils.tab_completion_utils import TabCompletionHandler


class UtilsHandler:
    """
    Container for utility services.
    Now receives SessionConfig instead of full ConfigHandler.
    """
    
    def __init__(self, config):
        """
        Initialize UtilsHandler with either SessionConfig (new) or ConfigHandler (legacy)
        
        :param config: SessionConfig instance (preferred) or ConfigHandler (legacy)
        """
        self.config = config
        self._output = None
        self._input = None
        self._stream = None
        self._fs = None
        self._storage = None
        self._tab_completion = None

    @property
    def output(self):
        if self._output is None:
            self._output = OutputHandler(self.config)
        return self._output

    @property
    def input(self):
        if self._input is None:
            self._input = InputHandler(self.config, self.output)
        return self._input

    @property
    def stream(self):
        if self._stream is None:
            self._stream = StreamHandler(self.config, self.output)
        return self._stream

    @property
    def fs(self):
        if self._fs is None:
            self._fs = FileSystemHandler(self.config, self.output)
        return self._fs

    @property
    def storage(self):
        if self._storage is None:
            self._storage = StorageHandler(self.config, self.output)
        return self._storage

    @property
    def tab_completion(self):
        if self._tab_completion is None:
            self._tab_completion = TabCompletionHandler(self.config, self.output)
        return self._tab_completion