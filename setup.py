#!/usr/bin/env python3

from setuptools import setup, find_packages
import archrepo2

setup(
  name = 'archrepo2',
  version = archrepo2.__version__,
  packages = find_packages(),
  install_requires = ['tornado>2.4.1', 'pyinotify', 'distribute'],
  entry_points = {
    'console_scripts': [
      'archreposrv = archrepo2.archreposrv:main',
    ],
  },

  author = 'lilydjwg',
  author_email = 'lilydjwg@gmail.com',
  description = 'Arch Linux repository manager',
  license = 'MIT',
  keywords = 'archlinux linux',
  url = 'https://github.com/lilydjwg/archrepo2',
)
