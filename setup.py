#!/usr/bin/env python
import os
from setuptools import setup

def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()

setup(name='iagitup',
      version='1.6.2',
      author='Giovanni Damiola',
      url='https://github.com/gdamdam/iagitup',
      license = "GNU General Public License v3.0",
      description= 'Tool to archive a git repository form GitHub to the Internet Archive.',
      long_description=read('README.md'),
      keywords = "github internetarchive",
      platforms = 'POSIX',
      packages = ['iagitup'],
      zip_safe = False,
      classifiers=[
          'Development Status :: 4 - Beta',
          'Intended Audience :: Developers',
          'Natural Language :: English',
          'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
          'Programming Language :: Python',
          'Programming Language :: Python :: 2.7'
      ],
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
