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
    tests_require=['mock', 'nose>=1.3'],
    author='CloudSigma AG',
    author_email='dev-support@cloudsigma.com',
    maintainer='Ivelin Slavov',
    maintainer_email='ivelin.slavov@cloudsigma.com',
    url='https://github.com/cloudsigma/cgroupspy',
    keywords=[
        'cgroups',
    ],
    classifiers=[
        "Programming Language :: Python",
        "Development Status :: 4 - Beta",
        "Intended Audience :: System Administrators",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: BSD License"
    ],
    license='New BSD',
    description="Python library for managing cgroups",
    long_description=long_description,
)
