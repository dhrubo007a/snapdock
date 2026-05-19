from setuptools import setup, find_packages

setup(
    name="snapdock-cli",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "click>=8.1.7",
        "rich>=13.7.1",
        "httpx>=0.27.0",
        "websockets>=12.0",
    ],
    entry_points={
        "console_scripts": [
            "snapdock=snapdock_cli.main:cli",
        ],
    },
)
