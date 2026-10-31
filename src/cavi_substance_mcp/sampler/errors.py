"""Error types shared by Sampler batch jobs, the workspace lock and the CLI exit codes."""


class BatchError(RuntimeError):
    code = 5


class AppUnavailable(BatchError):
    code = 3


class AppRunning(BatchError):
    code = 4


class LockTimeout(BatchError):
    code = 4


class JobTimeout(BatchError, TimeoutError):
    code = 6
