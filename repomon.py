#!/usr/bin/env python3
# vim:fileencoding=utf-8

import os
import re
import pwd
from functools import partial
from itertools import filterfalse
import queue
import logging
import sqlite3
import socket
import time
import hashlib
import pickle

import pyinotify
Event = pyinotify.Event
from tornado.ioloop import IOLoop
import tornado.process

import archpkg
import pkgreader
import dbutil

logger = logging.getLogger(__name__)

# handles only x86_64, i686 and any arch packages
_pkgfile_pat = re.compile(r'(?:^|/)[a-z0-9_-]+-[a-z0-9_.]+-\d+-(?:x86_64|i686|any)\.pkg\.tar\.xz$')

class ActionInfo(archpkg.PkgNameInfo):
  def __new__(cls, path, action, four=None, five=None):
    if four is not None:
      return super().__new__(cls, path, action, four, five)
    file = os.path.split(path)[1]
    self = cls.parseFilename(file)
    self.action = action
    self.path = path
    return self

  def __repr__(self):
    return '<ActionInfo: %s %s>' % (self.action, self.path)

class RepoMan:
  _timeout = None
  _cmd_queue = queue.Queue()
  _cmd_running = False

  def __init__(self, config, base, ioloop=None):
    self.action = []
    self._ioloop = ioloop or IOLoop.instance()
    self._base = base

    self._repo_dir = config.get('path')
    self._db_name = os.path.join(base, config.get('name') + '.db.tar.gz')
    self._files_name = os.path.join(base, self._db_name.replace('.db.tar.gz', '.files.tar.gz'))
    self._command_add = config.get('command-add', 'repo-add')
    self._command_remove = config.get('command-remove', 'repo-remove')
    self._wait_time = config.getint('wait-time', 10)

    notification_type = config.get('notification-type', 'null')
    if notification_type != 'null':
      self._notification_addr = config.get('notification-address')
      self._notification_secret = config.get('notification-secret')
    self.send_notification = getattr(
      self,
      'send_notification_' + notification_type.replace('-', '_'),
    )

  def queue_command(self, cmd, callbacks=None):
    self._cmd_queue.put((cmd, callbacks))
    if not self._cmd_running:
      self.run_command()

  def run_command(self):
    self.__class__._cmd_running = True
    try:
      cmd, callbacks = self._cmd_queue.get_nowait()
    except queue.Empty:
      self.send_notification()
      self.__class__._cmd_running = False
      return

    logger.info('Running cmd: %r', cmd)
    # have to specify io_loop or we'll get error tracebacks
    p = tornado.process.Subprocess(cmd, io_loop=self._ioloop)
    p.set_exit_callback(partial(self.command_done, callbacks))

  def command_done(self, callbacks, status):
    if status == 0:
      if callbacks:
        for cb in callbacks:
          cb()
      logger.info('previous command done.')
    else:
      logger.warn('previous command failed with status code %d.', status)
    self.run_command()

  def _do_cmd(self, cmd, items, callbacks):
    cmd1 = [cmd, self._db_name]
    cmd1.extend(items)
    cmd2 = [cmd, '-f', self._files_name]
    cmd2.extend(items)
    self.queue_command(cmd1)
    self.queue_command(cmd2, callbacks)

  def _do_add(self, toadd):
    if toadd:
      files, callbacks = zip(*toadd)
      self._do_cmd(self._command_add, files, callbacks)

  def _do_remove(self, toremove):
    if toremove:
      files, callbacks = zip(*toremove)
      self._do_cmd(self._command_remove, files, callbacks)

  def add_action(self, action):
    logger.info('Adding action %r to db %r', action, self._db_name)
    self._action_pending(action)

  def _action_pending(self, act):
    self.action.append(act)
    if self._timeout:
      self._ioloop.remove_timeout(self._timeout)
    self._timeout = self._ioloop.add_timeout(
      self._ioloop.time() + self._wait_time,
      self.run,
    )

  def send_notification_simple_udp(self):
    address, port = self._parse_notification_address_inet()
    try:
      af, socktype, proto, canonname, sockaddr = socket.getaddrinfo(
        address, port, 0, socket.SOCK_DGRAM, 0, 0)[0]
    except:
      logger.error('failed to create socket for notification', exc_info=True)
      return

    sock = socket.socket(af, socktype, proto)
    msg = self._new_notification_msg()
    sock.sendto(msg, sockaddr)
    sock.close()
    logger.info('simple udp notification sent.')

  def _new_notification_msg(self):
    s = 'update'
    t = str(int(time.time()))
    part1 = s + '|' + t
    part2 = hashlib.sha1(part1.encode('utf-8')).hexdigest()
    msg = part1 + '|' + part2
    logger.info('new notification msg: %s.', msg)
    return msg.encode('utf-8')

  def _parse_notification_address_inet(self):
    cached = self._notification_addr
    if isinstance(cached, str):
      host, port = cached.rsplit(':', 1)
      port = int(port)
      cached = self._notification_addr = (host, port)
    return cached

  def send_notification_null(self):
    logger.info('null notification sent.')

  def run(self):
    self._timeout = None
    actions = self.action
    self.action = []
    actiondict = {}
    for act in actions:
      if act.name not in actiondict:
        actiondict[act.name] = act
      else:
        oldact = actiondict[act.name]
        if oldact == act and oldact.action != act.action:
          # same package, opposite actions, do nothing
          del actiondict[act.name]
        else:
          # take the later action
          actiondict[act.name] = act
    toadd = [(x.path, x.callback) for x in actiondict.values() if x.action == 'add']
    toremove = [(x.name, x.callback) for x in actiondict.values() if x.action == 'remove']
    self._do_add(toadd)
    self._do_remove(toremove)

class EventHandler(pyinotify.ProcessEvent):
  def my_init(self, config, wm, ioloop=None):
    self.moved_away = {}
    self.repomans = {}
    self._ioloop = ioloop or IOLoop.instance()

    base = config.get('path')
    dbname = config.get('info-db', os.path.join(base, 'pkginfo.db'))
    new_db = not os.path.exists(dbname)
    self._db = sqlite3.connect(dbname, isolation_level=None) # isolation_level=None means autocommit
    if new_db:
      dbutil.setver(self._db, '0.2')
    else:
      assert dbutil.getver(self._db) == '0.2', 'wrong database version, please upgrade (see scripts directory)'
    self._db.execute('''create table if not exists pkginfo
                        (filename text unique,
                         pkgname text,
                         pkgarch text,
                         pkgver text,
                         forarch text,
                         owner text,
                         mtime int,
                         state int,
                         info text)''')

    dirs = [os.path.join(base, x) for x in ('any', 'i686', 'x86_64')]
    self.files = files = set()
    for d in dirs:
      files.update(os.path.join(d, f) for f in os.listdir(d))
      wm.add_watch(d, pyinotify.ALL_EVENTS)
      self.repomans[d] = RepoMan(config, d, self._ioloop)

    self._initial_update(files)

  def _initial_update(self, files):
    oldfiles = {f[0] for f in self._db.execute('select filename from pkginfo')}

    for f in sorted(filterfalse(filterPkg, files - oldfiles), key=pkgsortkey):
      self.dispatch(f, 'add')

    for f in sorted(filterfalse(filterPkg, oldfiles - files), key=pkgsortkey):
      self.dispatch(f, 'remove')

  def process_IN_CLOSE_WRITE(self, event):
    logger.debug('Writing done: %s', event.pathname)
    self.dispatch(event.pathname, 'add')
    self.files.add(event.pathname)

  def process_IN_DELETE(self, event):
    logger.debug('Removing: %s', event.pathname)
    self.dispatch(event.pathname, 'remove')
    try:
      self.files.remove(event.pathname)
    except KeyError:
      # symlinks haven't been added
      pass

  def movedOut(self, event):
    logger.debug('Moved away: %s', event.pathname)
    self.dispatch(event.pathname, 'remove')

  def process_IN_MOVED_FROM(self, event):
    self.moved_away[event.cookie] = self._ioloop.add_timeout(
      self._ioloop.time() + 0.1,
      partial(self.movedOut, event),
    )
    self.files.remove(event.pathname)

  def process_IN_MOVED_TO(self, event):
    if event.pathname in self.files:
      logger.warn('Overwritten: %s', event.pathname)
    self.files.add(event.pathname)

    if event.cookie in self.moved_away:
      self._ioloop.remove_timeout(self.moved_away[event.cookie])
    else:
      logger.debug('Moved here: %s', event.pathname)
      self.dispatch(event.pathname, 'add')

  def dispatch(self, path, action):
    act = ActionInfo(path, action)
    d, file = os.path.split(path)

    base, arch = os.path.split(d)

    # rename if a packager has added to a wrong directory
    # but not for a link that has arch=any, as that's what we created
    if action == 'add' and act.arch != arch and not (os.path.islink(path) and act.arch == 'any'):
      newd = os.path.join(base, act.arch)
      newpath = os.path.join(newd, file)
      os.rename(path, newpath)

      act.path = newpath
      path = newpath
      arch = act.arch
      d = newd

    if arch == 'any':
      for newarch in ('i686', 'x86_64'):
        newd = os.path.join(base, newarch)
        newpath = os.path.join(newd, file)
        if action == 'add':
          try:
            os.symlink(os.path.join('..', arch, file), newpath)
          except FileExistsError:
            pass
          else:
            # XXX: this should be removed as soon as symlinks are supported
            self._real_dispatch(newd, ActionInfo(newpath, action))
        else:
          try:
            os.unlink(newpath)
            # this will be detected and handled later
          except FileNotFoundError:
            # someone deleted the file for us
            pass

    self._real_dispatch(d, act)

  def _real_dispatch(self, d, act):
    if act.action == 'add':
      arch = os.path.split(d)[1]
      def callback():
        self._db.execute('update pkginfo set state = 0 where pkgname = ? and forarch = ?', (act.name, arch))
        stat = os.stat(act.path)
        mtime = int(stat.st_mtime)
        try:
          owner = pwd.getpwuid(stat.st_uid).pw_name
        except KeyError:
          owner = 'uid_%d' % stat.st_uid

        try:
          info = pkgreader.readpkg(act.path)
        except:
          logger.error('failed to read info for package %s', act.path)
          info = None
        info = pickle.dumps(info)

        self._db.execute(
          '''insert or replace into pkginfo
             (filename, pkgname, pkgarch, pkgver, forarch, state, owner, mtime, info) values
             (?,        ?,       ?,       ?,      ?,       ?,     ?,     ?,     ?)''',
          (act.path, act.name, act.arch, act.fullversion, arch, 1, owner, mtime, info))

    else:
      res = self._db.execute('select state from pkginfo where filename = ? and state = 1 limit 1', (act.path,))
      if tuple(res) == ():
        # the file isn't in repo database, just delete from our info database
        logger.debug('deleting entry for not-in-database package: %s', act.path)
        self._db.execute('delete from pkginfo where filename = ?', (act.path,))
        return
      def callback():
        self._db.execute('delete from pkginfo where filename = ?', (act.path,))

    act.callback = callback
    self.repomans[d].add_action(act)

  # def process_default(self, event):
  #   print(event)

def filterPkg(path):
  if isinstance(path, Event):
    path = path.pathname
  return not _pkgfile_pat.search(path)

def pkgsortkey(path):
  pkg = archpkg.PkgNameInfo.parseFilename(os.path.split(path)[1])
  return (pkg.name, pkg.arch, pkg)

def repomon(config):
  wm = pyinotify.WatchManager()
  ioloop = IOLoop.instance()

  handler = EventHandler(
    filterPkg,
    config=config,
    wm=wm,
    ioloop=ioloop,
  )
  return pyinotify.TornadoAsyncNotifier(
    wm,
    ioloop,
    default_proc_fun=handler,
  )
