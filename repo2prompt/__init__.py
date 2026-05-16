"""repo2prompt — turn a repository into a single text prompt for LLMs."""

from .core import (
    GitignoreFilter,
    LANGUAGE_EXTENSIONS,
    IMPORTANT_FILENAMES,
    collect_files,
    detect_languages,
    generate_directory_tree,
    traverse_and_copy,
)

__version__ = "0.2.0"
__all__ = [
    "GitignoreFilter",
    "LANGUAGE_EXTENSIONS",
    "IMPORTANT_FILENAMES",
    "collect_files",
    "detect_languages",
    "generate_directory_tree",
    "traverse_and_copy",
]
