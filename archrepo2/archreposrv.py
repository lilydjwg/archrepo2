#!/usr/bin/env python3

import sys
import signal
import asyncio
import configparser
import logging

from .lib.nicelogger import enable_pretty_logging
enable_pretty_logging(logging.DEBUG)

from .repomon import repomon

logger = logging.getLogger(__name__)

def check_and_get_repos(config):
  repos = config['multi'].get('repos', 'repository')
  for field in ('name', 'path'):
    if config['multi'].get(field, None) is not None:
      raise ValueError('config %r cannot have default value.' % field)

  repos = {repo.strip() for repo in repos.split(',')}
  for field in ('name', 'path'):
    vals = [config[repo].get(field) for repo in repos]
    if len(vals) != len(set(vals)):
      raise ValueError('duplicate %s in different repositories.' % field)

  return repos

async def amain(config):
  notifiers = []
  for repo in check_and_get_repos(config):
    notifiers.extend(repomon(config[repo]))

  logger.info('starting archreposrv.')
  stop_event = asyncio.Event()
  loop = asyncio.get_running_loop()
  for sig in (signal.SIGINT, signal.SIGTERM):
    loop.add_signal_handler(sig, stop_event.set)
  try:
    # Block until we get SIGINT/SIGTERM.
    await stop_event.wait()
  finally:
    for notifier in notifiers:
      notifier.stop()

def main():
  if len(sys.argv) != 2:
    sys.exit('usage: archreposrv <config>')
  conffile = sys.argv[1]
  config = configparser.ConfigParser(default_section='multi')
  config.read(conffile)
  asyncio.run(amain(config))

if __name__ == '__main__':
  if sys.version_info < (3, 14):
    raise OSError('Python 3.14+ required.')
  main()
