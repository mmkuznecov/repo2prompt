"""repo2prompt — turn a repository into a single text prompt for LLMs."""

from .analysis import (
    Dependency,
    FileAnalysis,
    ProjectAnalysis,
    Symbol,
    analyze_project,
    render_project_analysis,
    write_analysis_json,
)
from .core import (
    GitignoreFilter,
    IMPORTANT_FILENAMES,
    collect_files,
    detect_languages,
    generate_directory_tree,
    traverse_and_copy,
)
from .languages import EXT_TO_LANG, LANGUAGE_EXTENSIONS

__version__ = "0.4.0"
__all__ = [
    "Dependency",
    "FileAnalysis",
    "ProjectAnalysis",
    "Symbol",
    "analyze_project",
    "render_project_analysis",
    "write_analysis_json",
    "GitignoreFilter",
    "LANGUAGE_EXTENSIONS",
    "EXT_TO_LANG",
    "IMPORTANT_FILENAMES",
    "collect_files",
    "detect_languages",
    "generate_directory_tree",
    "traverse_and_copy",
]
