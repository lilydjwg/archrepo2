import os
import re
import subprocess
from collections import namedtuple

import pyalpm

class PkgNameInfo(namedtuple('PkgNameInfo', 'name, version, release, arch')):
  def __lt__(self, other) -> bool:
    if self.name != other.name or self.arch != other.arch:
      return NotImplemented
    if self.version != other.version:
      return pyalpm.vercmp(self.version, other.version) < 0
    return float(self.release) < float(other.release)

  def __gt__(self, other) -> bool:
    # No, try the other side please.
    return NotImplemented

  @property
  def fullversion(self) -> str:
    return '%s-%s' % (self.version, self.release)

  @classmethod
  def parseFilename(cls, filename: str) -> 'PkgNameInfo':
    return cls(*trimext(filename, 3).rsplit('-', 3))

def trimext(name: str, num: int = 1) -> str:
  for _ in range(num):
    name = os.path.splitext(name)[0]
  return name

def get_pkgname_with_bash(PKGBUILD: str) -> list[str]:
  script = f"""\
. '{PKGBUILD}'
echo ${{pkgname[*]}}"""
  result = subprocess.run(
    ['bwrap', '--unshare-all', '--ro-bind', '/', '/', '--tmpfs', '/home',
     '--tmpfs', '/run', '--die-with-parent',
     '--tmpfs', '/tmp', '--proc', '/proc', '--dev', '/dev', '/bin/bash'],
    input = script,
    stdout = subprocess.PIPE,
    text = True,
  )
  if result.returncode != 0:
    raise subprocess.CalledProcessError(
      result.returncode, ['bash'], result.stdout)
  return result.output.split()

pkgfile_pat = re.compile(r'(?:^|/).+-[^-]+-[\d.]+-(?:\w+)\.pkg\.tar\.(?:xz|zst)$')

def _strip_ver(s: str) -> str:
  return re.sub(r'[<>=].*', '', s)

def get_package_info(name: str, local: bool = False) -> dict[str, str]:
  cmd = ['pacman', '-Qi' if local else '-Si', name]
  env = os.environ.copy()
  env['LANG'] = 'C'
  completed = subprocess.run(
    cmd,
    stdout = subprocess.PIPE,
    env = env,
    check = True,
  )
  out = completed.stdout.decode('latin1')

  ret: dict[str, str] = {}
  key = None
  for line in out.splitlines():
    if not line:
      continue
    if line[0] not in ' \t':
      key, value = line.split(':', 1)
      key = key.strip()
      ret[key] = value.strip()
    else:
      ret[key] += ' ' + line.strip()
  return ret
