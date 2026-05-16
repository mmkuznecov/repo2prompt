from setuptools import setup, find_packages

setup(
    name="repo2prompt",
    version="0.2.0",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[
        "pathspec>=0.10",
    ],
    entry_points={
        "console_scripts": [
            "repo2prompt=repo2prompt.cli:main",
        ],
    },
    author="Mikhail Kuznetsov",
    author_email="mmkuznecov2002@gmail.com",
    description=(
        "A tool to copy repository contents to a text file for LLM prompts, "
        "with .gitignore support, language detection, and auto-inclusion of "
        "important project files."
    ),
    keywords="repository, text, cli, LLM, gitignore",
    url="http://github.com/mmkuznecov/repo2prompt",
)
