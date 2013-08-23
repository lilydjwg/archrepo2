#!/usr/bin/env python3
# vim:fileencoding=utf-8

from subprocess import getoutput
allpkgs = getoutput(r"locate -be --regex '\.pkg\.tar\.xz$'").split('\n')

from archrepo2.pkgreader import readpkg
for p in allpkgs:
  print('reading package:', p)
  d = readpkg(p)
  print('desc:', d.get('pkgdesc', '(nothing)'))
