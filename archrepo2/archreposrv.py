#!/usr/bin/env python3

import sys
import configparser
import logging

from tornado.ioloop import IOLoop

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

def main():
  conffile = sys.argv[1]
  config = configparser.ConfigParser(default_section='multi')
  config.read(conffile)
  repos = check_and_get_repos(config)

  notifiers = [repomon(config[repo]) for repo in repos]

  ioloop = IOLoop.instance()
  logger.info('starting archreposrv.')
  try:
    ioloop.start()
  except KeyboardInterrupt:
    ioloop.close()
    for notifier in notifiers:
      notifier.stop()
    print()

if __name__ == '__main__':
  main()
