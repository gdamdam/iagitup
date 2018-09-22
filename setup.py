#!/usr/bin/env python
import os
from setuptools import setup

def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()

setup(name='iagitup',
      version='1.5',
      author='Giovanni Damiola',
      url='https://github.com/gdamdam/iagitup',
      license = "GNU General Public License v3.0",
      description= 'Tool to archive a GitHub repository on the Internet Archive.',
      long_description=read('README.md'),
      keywords = "github internetarchive",
      packages = ['iagitup'],
      zip_safe = False,
      entry_points={
            'console_scripts': [
                'iagitup = iagitup.__main__:main',
            ],
      },
      install_requires=[
                    'appdirs',
                    'args',
                    'asn1crypto',
                    'cffi',
                    'clint',
                    'cryptography',
                    'docopt',
                    'enum34',
                    'gitdb2',
                    'GitPython',
                    'idna',
                    'internetarchive',
                    'ipaddress',
                    'jsonpatch',
                    'markdown2',
                    'ndg-httpsclient',
                    'packaging',
                    'pyasn1',
                    'pycparser',
                    'pyOpenSSL',
                    'pyparsing',
                    'requests',
                    'schema',
                    'six',
                    'smmap2',
                    'wheel']
     )
