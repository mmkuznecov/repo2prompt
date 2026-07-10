from setuptools import setup, find_packages

setup(
    name="repo2prompt",
    version="0.4.0",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "pathspec>=0.10",
    ],
    extras_require={
        "dev": ["pytest>=7", "ruff>=0.6"],
    },
    entry_points={
        "console_scripts": [
            "repo2prompt=repo2prompt.cli:main",
        ],
    },
    author="Mikhail Kuznetsov",
    author_email="mmkuznecov2002@gmail.com",
    description=(
        "Create LLM-ready repository prompts with dependency maps, symbol "
        "signatures, .gitignore support, and language detection."
    ),
    keywords="repository, code map, dependency graph, LLM, prompt, gitignore",
    url="http://github.com/mmkuznecov/repo2prompt",
)
