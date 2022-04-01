#!/usr/bin/env python

from setuptools import setup
import cgroupspy

try:
    with open('README.md') as file:
        long_description = file.read()
except IOError:
    long_description = ""

setup(
    name='cgroupspy',
    version=cgroupspy.__version__,
    packages=['cgroupspy'],
    tests_require=['mock', 'nose2'],
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
