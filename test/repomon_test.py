#!/usr/bin/env python3
# vim:fileencoding=utf-8

import os
import sys
import time
import configparser
import subprocess
import logging
import shutil
import tarfile

from myutils import enable_pretty_logging
enable_pretty_logging(logging.DEBUG)

class Command:
  def __init__(self, ctx, args):
    self.args = args
    self.ctx = ctx
    self.run()

class WaitCommand(Command):
  cmd = 'wait'
  def run(self):
    t = self.ctx['wait_time'] + 2
    logging.info('waiting for %d seconds...', t)
    time.sleep(t)

class BaseDirCommand(Command):
  cmd = 'base_dir'
  def run(self):
    base_dir = self.args[0]
    logging.info('base_dir set to %s.', base_dir)
    self.ctx['base_dir'] = base_dir

class AddCommand(Command):
  cmd = 'add'
  def run(self):
    arch, file = self.args
    srcfile = os.path.join(self.ctx['base_dir'], file)
    dstfile = os.path.join(self.ctx['repo_dir'], arch, file)
    logging.info('adding file %s', file)
    shutil.copyfile(srcfile, dstfile)

class RemoveCommand(Command):
  cmd = 'remove'
  def run(self):
    arch, file = self.args
    file = arch + '/' + file
    dstfile = os.path.join(self.ctx['repo_dir'], file)
    os.unlink(dstfile)
    logging.info('removing file %s', file)

class CheckYCommand(Command):
  cmd = 'checky'
  def run(self):
    f = os.path.join(self.ctx['repo_dir'], self.args[0])
    r = os.path.isfile(f)
    if not r:
      logging.error('checky assertion failed: %s is not a file.', f)

class CheckNCommand(Command):
  cmd = 'checkn'
  def run(self):
    f = os.path.join(self.ctx['repo_dir'], self.args[0])
    r = os.path.exists(f)
    if r:
      logging.error('checkn assertion failed: %s exists.', f)

class CheckPCommand(Command):
  cmd = 'checkp'
  def run(self):
    arch, what = self.args
    dbfile = os.path.join(self.ctx['repo_dir'], arch, self.ctx['repo_name'] + '.db')
    db = tarfile.open(dbfile)
    name, ver = what.split('=', 1)
    if ver == 'null':
      pkg = [x for x in db.getnames() if '/' not in x and x.startswith(name+'-') and x[len(name)+1:].count('-') != 1]
      if pkg:
        logging.error('checkp assertion failed: package %s still exists in database: %r', name, pkg)
    else:
      try:
        db.getmember('%s-%s' % (name, ver))
      except KeyError:
        logging.error('checkp assertion failed: package %s does not exist in database.', what)
    db.close()

def build_command_map(cmdcls=Command, cmdmap={}):
  for cls in cmdcls.__subclasses__():
    cmdmap[cls.cmd] = cls
    build_command_map(cls)
  return cmdmap

def build_action_ctx(conf):
  ctx = {}
  ctx['repo_dir'] = conf.get('path')
  ctx['repo_name'] = conf.get('name')
  ctx['wait_time'] = conf.getint('wait-time', 10)
  ctx['base_dir'] = ''
  return ctx

def run_action_file(conf, actlines):
  cmdmap = build_command_map()
  ctx = build_action_ctx(conf)
  for l in actlines:
    l = l.rstrip()
    if not l or l.startswith('#'):
      continue
    cmd, *args = l.split()
    cmd = cmd.rstrip(':')
    try:
      cmdmap[cmd](ctx, args)
    except:
      logging.error('error running action: %s', l, exc_info=True)
  logging.info('done running action file.')

class Server:
  def __init__(self, conffile):
    self.conffile = conffile

  def start(self):
    server_path = os.path.join('.', os.path.normpath(os.path.join(__file__, '../../archreposrv')))
    logging.debug('server path: %s', server_path)
    logging.info('starting server...')
    self.p = subprocess.Popen([server_path, self.conffile])

  def stop(self):
    logging.info('quitting server...')
    p = self.p
    p.send_signal(2)
    ret = p.wait()
    if ret == 0:
      logging.info('server exited normally.')
    else:
      logging.error('server exited with error code %d.' % ret)

def main(conffile, actfile):
  config = configparser.ConfigParser()
  config.read(conffile)

  dest_dir = config['repository'].get('path')
  if os.path.isdir(dest_dir):
    ans = input('Repository directory for testing exists. Removing? [y/N] ')
    if ans not in 'Yy':
      logging.warn('user cancelled.')
      sys.exit(1)
    else:
      logging.info('removing already existing testing repo...')
      shutil.rmtree(dest_dir)
  os.mkdir(dest_dir)
  for d in ('any', 'i686', 'x86_64'):
    p = os.path.join(dest_dir, d)
    os.mkdir(p)

  server = Server(conffile)
  server.start()

  with open(actfile) as acts:
    run_action_file(config['repository'], acts)

  server.stop()
  logging.info('removing testing repo...')
  shutil.rmtree(dest_dir)

if __name__ == '__main__':
  if len(sys.argv) != 3:
    sys.exit('usage: %s repo_config test_action' % os.path.split(sys.argv[0])[-1])
  main(sys.argv[1], sys.argv[2])
