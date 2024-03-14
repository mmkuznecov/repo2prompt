from setuptools import setup, find_packages

setup(
    name="repo2prompt",
    version="0.1.0",
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'repo2prompt=repo2prompt.cli:main',
        ],
    },
    author="Mikhail Kuznetsov",
    author_email="mmkuznecov2002@gmail.com",
    description="A tool to copy repository contents to a text file for LLM prompts.",
    keywords="repository, text, cli, LLM",
    url="http://github.com/mmkuznecov/repo2prompt",  # Optionally, your project's repository URL
)