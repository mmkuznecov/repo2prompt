# repo2prompt

`repo2prompt` is a CLI tool designed to facilitate the copying of repository files into a text file, creating a structured prompt for language. It allows filtering by file types and includes a visual representation of the project structure.

## Installation

Before installing `repo2prompt`, ensure you have Python 3.6 or later installed on your system.

1. Clone the repository:

```bash
git clone https://github.com/mmkuznecov/repo2prompt.git
```

2. Install the package:

```bash
pip install -e .
```

## Usage

To use `repo2prompt`, run the following command from your terminal:

```bash
repo2prompt <path to repository> --pattern <your pattern> --output <your ouput path>
```

### Parameters:

- `<path to repository>`: The path to the repository you want to process.
- `--pattern`: Optional. The pattern of files to include (e.g., `*.py`). Defaults to `*.py`.
- `--output`: Optional. The name of the output file. Defaults to `prompt.txt`.

### Example:

```bash
repo2prompt ./myproject --pattern "*.js" --output js_files_prompt.txt
```

This command will generate a `js_files_prompt.txt` file containing a visual tree structure of the `myproject` directory, followed by the contents of all `.js` files.
