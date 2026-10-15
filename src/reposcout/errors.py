class RepoScoutError(Exception):
    """Base class for errors shown to the user with a clear message."""


class ProviderError(RepoScoutError):
    """An embedding or LLM provider could not be reached or returned bad data."""


class IndexMismatchError(RepoScoutError):
    """The index was built with a different embedding model than the one configured."""


class IndexNotFoundError(RepoScoutError):
    """No index exists yet for this repository."""
