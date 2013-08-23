import tarfile
import logging

logger = logging.getLogger(__name__)

multikeys = {'depend', 'makepkgopt', 'optdepend', 'replaces', 'conflict',
             'provides', 'license', 'backup', 'group', 'makedepend', 'checkdepend'}

def _add_to_dict(d, key, value):
  if key in multikeys:
    if key in d:
      d[key].append(value)
    else:
      d[key] = [value]
  else:
    assert key not in d, 'unexpected multi-value key "%s"' % key
    d[key] = value

def readpkg(file):
  tar = tarfile.open(file)
  info = tar.next()
  if not info or info.name != '.PKGINFO':
    logger.warn('%s is not a nice package!', file)
    info = '.PKGINFO' # have to look further
  f = tar.extractfile(info)
  data = f.read().decode()
  tar.close()

  d = {}
  key = None
  for l in data.split('\n'):
    if l.startswith('#'):
      continue
    if not l:
      continue
    if '=' not in l:
      value += l
    else:
      if key is not None:
        _add_to_dict(d, key, value)
      key, value = l.split(' = ', 1)
  _add_to_dict(d, key, value)

  return d
