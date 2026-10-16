"""Readers: a file in, the pieces the model and keyword search take out (`indexer` decides what to do with them)."""


class ExtractError(Exception):
    """A file that can't be read (and why): recorded on the file, never fatal to the run."""
