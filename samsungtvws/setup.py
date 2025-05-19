#!/usr/bin/env python
from setuptools import find_packages, setup
exec(open('samsungtvws/version.py').read())

def readme():
    with open("README.md") as readme_file:
        return readme_file.read()


setup(
    name="samsungtvws",
    version=__version__,
    description="Samsung Smart TV WS API wrapper",
    long_description=readme(),
    long_description_content_type="text/markdown",
    author="Xchwarze, NickWaterton <n.waterton@outlook.com>",
    python_requires=">=3.7.0",
    url="https://github.com/NickWaterton/samsung-tv-ws-api",
    package_data={"samsungtvws": ["py.typed"]},
    packages=find_packages(exclude=("tests",)),
    install_requires=["websocket-client>=0.57.0", "requests>=2.21.0", "aiohttp>=3.8.1", "websockets>=10.2", "async_timeout>=4.0.3"],
    extras_require={
        "encrypted": ["cryptography>=35.0.0", "py3rijndael>=0.3.3"],
    },
    include_package_data=True,
    license="LGPL-3.0",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: LGPL-3.0 License",
    ],
)
