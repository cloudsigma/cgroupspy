#!/usr/bin/env python

import os
import re
from setuptools import setup

with open(os.path.join(os.path.dirname(__file__), 'cgroupspy', '__init__.py')) as file:
    version = re.search(r'^__version__ = "([^"]+)"', file.read(), re.MULTILINE).group(1)

try:
    with open(os.path.join(os.path.dirname(__file__), 'README.md')) as file:
        long_description = file.read()
except IOError:
    long_description = ""

setup(
    name='cgroupspy',
    version=version,
    packages=['cgroupspy'],
    project_urls={
        'Documentation': 'https://github.com/cloudsigma/cgroupspy/tree/master/docs',
        'Changelog': 'https://github.com/cloudsigma/cgroupspy/blob/master/CHANGELOG.md',
        'Issues': 'https://github.com/cloudsigma/cgroupspy/issues',
    },
    author='CloudSigma AG',
    author_email='dev-support@cloudsigma.com',
    maintainer='Miguel Trujillo',
    maintainer_email='miguel@cloudsigma.com',
    url='https://github.com/cloudsigma/cgroupspy',
    keywords=[
        'cgroups',
    ],
    classifiers=[
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
        'Programming Language :: Python :: 3.14',
        'Programming Language :: Python :: 3.15',
        'Operating System :: POSIX :: Linux',
        "Development Status :: 4 - Beta",
        "Intended Audience :: System Administrators",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: BSD License"
    ],
    license='New BSD',
    description="Python library for managing cgroups",
    long_description=long_description,
    long_description_content_type='text/markdown',
    python_requires=">=2.7, !=3.0.*, !=3.1.*, !=3.2.*, !=3.3.*, !=3.4.*, !=3.5.*",
)
