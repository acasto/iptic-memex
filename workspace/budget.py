"""Call allowances shared with helper sessions, without workflow-specific core flags."""

from core.runtime.contracts import RunStopped


class CallAllowance:
    """A worker's reservation includes any model-backed tools it invokes."""

    def __init__(self, limit):
        self.limit, self.used = int(limit), 0

    def before_call(self, session, index):
        if self.used >= self.limit:
            raise RunStopped('budget', 'Worker call allowance exhausted, including helpers')
        self.used += 1
