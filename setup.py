import re
from setuptools import setup

# Read version from lc.py
with open("lc.py") as f:
    version = re.search(r'^__version__\s*=\s*"([^"]+)"', f.read(), re.M).group(1)

with open("README.md") as f:
    long_description = f.read()

setup(
    name="lc-cli",
    version=version,
    description="A simple, interactive OpenAI-compatible chat client for the terminal",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/abotsis/lc-cli",
    license="MIT",
    py_modules=["lc"],
    install_requires=[
        "openai>=1.0.0",
        "prompt_toolkit>=3.0.0",
        "rich>=13.0.0",
    ],
    entry_points={
        "console_scripts": [
            "lc=lc:main",
        ],
    },
    python_requires=">=3.8",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Topic :: Utilities",
    ],
)
