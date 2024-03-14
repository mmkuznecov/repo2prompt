import argparse
from .core import traverse_and_copy


def main():
    parser = argparse.ArgumentParser(
        description="Copy repository files to a text prompt for LLM."
    )
    parser.add_argument("repo_path", help="Path to the repository.")
    parser.add_argument(
        "--pattern", default="*.py", help="Pattern of files to include, e.g., '*.py'."
    )
    parser.add_argument("--output", default="prompt.txt", help="Output file name.")
    args = parser.parse_args()

    traverse_and_copy(args.repo_path, args.pattern, args.output)
