#!/usr/bin/env python3

import os
import re
import pwd
import stat
from functools import partial
from itertools import filterfalse
import queue
import logging
import sqlite3
import socket
import time
import hashlib
import pickle
from os.path import relpath

import pyinotify
Event = pyinotify.Event
from tornado.ioloop import IOLoop
import tornado.process

from .lib import archpkg
from . import pkgreader
from . import dbutil

logger = logging.getLogger(__name__)

def same_existent_file(a, b):
  try:
    return os.path.samefile(a, b)
  except OSError:
    return False

class ActionInfo(archpkg.PkgNameInfo):
  def __new__(cls, path, action, four=None, five=None, pkgpath=None):
    if four is not None:
      return super().__new__(cls, path, action, four, five)
    file = os.path.split(pkgpath or path)[1]
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

  def __init__(self, config, base):
    self.action = []
    self._ioloop = IOLoop.current()
    self._base = base

    self._repo_dir = config.get('path')
    self.name = config.get('name')
    self._db_file = os.path.join(base, self.name + '.db.tar.gz')
    self._files_name = os.path.join(base, self._db_file.replace('.db.tar.gz', '.files.tar.gz'))
    self._command_add = config.get('command-add', 'repo-add')
    self._command_remove = config.get('command-remove', 'repo-remove')
    self._wait_time = config.getint('wait-time', 10)
    self._without_db = config.getboolean('without-db', False)
    self._auto_rename = config.getboolean('auto-rename', True)
    self._symlink_any = config.getboolean('symlink-any', True)

    notification_type = config.get('notification-type', 'null')
    if notification_type != 'null':
      self._notification_addrs = config.get('notification-addresses')
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
    # no longer have to specify io_loop in Tornado > 3.1. Let's drop them for
    # Tornado >= 5
    try:
      p = tornado.process.Subprocess(cmd)
    except OSError:
      logger.error('failed to run command.', exc_info=True)
      self.run_command()
    else:
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
    cmd1 = [cmd, self._db_file]
    cmd1.extend(items)
    self.queue_command(cmd1, callbacks)

  def _do_add(self, toadd):
    if toadd:
      files, callbacks = zip(*toadd)
      if self._without_db:
        self._do_callbacks(callbacks)
      else:
        self._do_cmd(self._command_add, files, callbacks)

  def _do_remove(self, toremove):
    if toremove:
      files, callbacks = zip(*toremove)
      if self._without_db:
        self._do_callbacks(callbacks)
      else:
        self._do_cmd(self._command_remove, files, callbacks)

  def _do_callbacks(self, callbacks):
    for cb in callbacks:
      cb()

  def add_action(self, action):
    logger.info('Adding action %r to db %r', action, self._db_file)
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
    msg = self._new_notification_msg()

    socks = {}
    for address, port in self._parse_notification_address_inet():
      try:
        af, socktype, proto, canonname, sockaddr = socket.getaddrinfo(
          address, port, 0, socket.SOCK_DGRAM, 0, 0)[0]
      except:
        logger.exception('failed to create socket to %r for notification',
                         (address, port))
        continue

      info = af, socktype, proto
      if info not in socks:
        sock = socket.socket(*info)
        socks[info] = sock
      else:
        sock = socks[info]
      sock.sendto(msg, sockaddr)
      logger.info('simple udp notification sent to %s.', (address, port))

  def _new_notification_msg(self):
    s = 'update'
    t = str(int(time.time()))
    data = s + '|' + t
    hashing = data + self._notification_secret
    sig = hashlib.sha1(hashing.encode('utf-8')).hexdigest()
    msg = data + '|' + sig
    logger.info('new notification msg: %s.', msg)
    return msg.encode('utf-8')

  def _parse_notification_address_inet(self):
    cached = self._notification_addrs
    if isinstance(cached, str):
      addresses = []
      for addr in cached.split():
        host, port = addr.rsplit(':', 1)
        port = int(port)
        addresses.append((host, port))
      cached = self._notification_addrs = tuple(addresses)
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
        if oldact != act:
          # different packages, do the latter, but record the former
          try:
              actiondict[act.name].callback(state=0)
          except:
              logger.exception('failed to run action %r.', actiondict[act.name])
        # same package, do the latter, and discard the forter
        actiondict[act.name] = act
    toadd = [(x.path, x.callback) for x in actiondict.values() if x.action == 'add']
    toremove = [(x.name, x.callback) for x in actiondict.values() if x.action == 'remove']
    self._do_add(toadd)
    self._do_remove(toremove)

class EventHandler(pyinotify.ProcessEvent):
  def my_init(self, filter_pkg, supported_archs, config, wm):
    self.filter_pkg = filter_pkg
    self.moved_away = {}
    self.repomans = {}
    # TODO: use an expiring dict
    self.our_links = set()
    self._ioloop = IOLoop.current()

    base = config.get('path')
    dbname = config.get('info-db', os.path.join(base, 'pkginfo.db'))
    new_db = not os.path.exists(dbname)
    self._db = sqlite3.connect(dbname, isolation_level=None) # isolation_level=None means autocommit
    self._db_dir = os.path.dirname(dbname)
    if new_db:
      dbutil.setver(self._db, '0.4')
    else:
      assert dbutil.getver(self._db) == '0.4', 'wrong database version, please upgrade (see scripts directory)'
    self._db.execute('''create table if not exists pkginfo
                        (filename text unique,
                         pkgrepo text,
                         pkgname text,
                         pkgarch text,
                         pkgver text,
                         forarch text,
                         owner text,
                         mtime int,
                         state int,
                         info blob)''')
    self._db.execute('''create table if not exists sigfiles
                        (filename text unique,
                         pkgrepo text)''')

    self._supported_archs = supported_archs
    dirs = [os.path.join(base, x) for x in self._supported_archs]
    self.files = files = set()
    for d in dirs:
      os.makedirs(d, exist_ok=True)
      for f in os.listdir(d):
        p = os.path.join(d, f)
        if os.path.exists(p): # filter broken symlinks
          files.add(p)
      wm.add_watch(d, pyinotify.ALL_EVENTS)
      self.repomans[d] = RepoMan(config, d)
      self.name = self.repomans[d].name
      self._auto_rename = self.repomans[d]._auto_rename
      self._symlink_any = self.repomans[d]._symlink_any

    self._initial_update(files)

  def _initial_update(self, files):
    oldfiles = {f[0] for f in self._db.execute('select filename from pkginfo where pkgrepo = ?', (self.name,))}
    oldfiles.update(f[0] for f in self._db.execute('select filename from sigfiles where pkgrepo = ?', (self.name,)))
    oldfiles = {os.path.join(self._db_dir, f) for f in oldfiles}

    for f in sorted(filterfalse(self.filter_pkg, files - oldfiles),
                    key=pkgsortkey):
      self.dispatch(f, 'add')

    for f in sorted(filterfalse(self.filter_pkg, oldfiles - files),
                    key=pkgsortkey):
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

  def process_IN_CREATE(self, event):
    file = event.pathname
    if os.path.islink(file):
      if file in self.our_links:
        self.our_links.remove(file)
      else:
        logger.debug('Symlinked: %s', file)
        self.dispatch(file, 'add')
        self.files.add(file)
    else:
      logger.debug('Linked: %s', file)
      self.dispatch(file, 'add')
      self.files.add(file)

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
    if path.endswith('.sig'):
      act = ActionInfo(path, action, pkgpath=path[:-4])
      callback = self._signature_changed
    else:
      act = ActionInfo(path, action)
      callback = self._real_dispatch

    d, file = os.path.split(path)
    base, arch = os.path.split(d)

    # rename if a packager has added to a wrong directory
    if self._auto_rename and action == 'add' and act.arch != arch:
      newd = os.path.join(base, act.arch)
      newpath = os.path.join(newd, file)
      if not same_existent_file(path, newpath):
        os.rename(path, newpath)

        act.path = newpath
        path = newpath
        arch = act.arch
        d = newd

    if self._symlink_any and act.arch == 'any':
      for newarch in self._supported_archs:
        if newarch == arch:
          # this file itself
          continue
        newd = os.path.join(base, newarch)
        newpath = os.path.join(newd, file)
        if action == 'add':
          oldpath = os.path.join('..', arch, file)
          if not same_existent_file(oldpath, newpath):
            os.makedirs(newd, exist_ok=True)
            try:
              self.our_links.add(newpath)
              os.symlink(oldpath, newpath)
            except FileExistsError:
              pass
            callback(newd, ActionInfo(newpath, action))
        else:
          try:
            os.unlink(newpath)
            # this will be detected and handled later
          except FileNotFoundError:
            # someone deleted the file for us
            pass

    callback(d, act)

  def _real_dispatch(self, d, act):
    if act.action == 'add':
      arch = os.path.split(d)[1]
      def callback(stat, state=1):
        self._db.execute(
          'update pkginfo set state = 0 where pkgname = ? and forarch = ? and pkgrepo = ?',
          (act.name, arch, self.name)
        )
        mtime = int(stat.st_mtime)
        try:
          owner = pwd.getpwuid(stat.st_uid).pw_name
        except KeyError:
          owner = 'uid_%d' % stat.st_uid

        try:
          info = pkgreader.readpkg(act.path)
        except:
          logger.error('failed to read info for package %s', act.path, exc_info=True)
          info = None
        info = pickle.dumps(info)

        self._db.execute(
          '''insert or replace into pkginfo
             (filename, pkgrepo, pkgname, pkgarch, pkgver, forarch, state, owner, mtime, info) values
             (?,        ?,       ?,       ?,       ?,      ?,       ?,     ?,     ?,     ?)''',
          (relpath(act.path, start=self._db_dir),
                       self.name, act.name, act.arch, act.fullversion, arch, state, owner, mtime, info))
        logger.info('Action %r done.', act)

      # stat path here, so that it is more unlikely to have disappeared since
      callback = partial(callback, os.stat(act.path))
    else:
      rpath = relpath(act.path, start=self._db_dir)
      res = self._db.execute(
        'select state from pkginfo where filename = ? and state = 1 and pkgrepo = ? limit 1',
        (rpath, self.name)
      )
      if tuple(res) == ():
        # the file isn't in repo database, just delete from our info database
        logger.debug('deleting entry for not-in-database package: %s', rpath)
        self._db.execute('delete from pkginfo where filename = ? and pkgrepo = ?', (rpath, self.name))
        return
      def callback(state=any):
        '''``state`` is not used'''
        self._db.execute('delete from pkginfo where filename = ? and pkgrepo = ?', (rpath, self.name))

    act.callback = callback
    self.repomans[d].add_action(act)

  def _signature_changed(self, d, action):
    path = action.path
    action = action.action
    logger.info('%s signature %s.', action, path)

    # Touch the pacakge file so that we'll repo-add it again to include the
    # sig change later.
    pkg = path[:-4]
    try:
      st = os.lstat(pkg)
      if stat.S_ISREG(st.st_mode):
        logger.info('touching %s.', pkg)
        os.close(os.open(pkg, os.O_WRONLY))
        os.utime(pkg)
    except FileNotFoundError:
      pass

    rpath = relpath(path, start=self._db_dir)
    if action == 'add':
      self._db.execute('''insert or replace into sigfiles
                          (filename, pkgrepo) values (?, ?)''',
                       (rpath, self.name))
    else:
      self._db.execute('''delete from sigfiles where filename = ? and pkgrepo = ?''',
                       (rpath, self.name))

def filter_pkg(regex, path):
  if isinstance(path, Event):
    path = path.pathname
  return not regex.search(path)

def pkgsortkey(path):
  pkg = archpkg.PkgNameInfo.parseFilename(os.path.split(path)[1])
  return (pkg.name, pkg.arch, pkg)

def repomon(config):
  wm = pyinotify.WatchManager()

  supported_archs = config.get('supported-archs', 'i686 x86_64').split()
  if 'any' not in supported_archs:
    supported_archs.append('any')
  # assume none of the archs has regex meta characters
  regex = re.compile(r'(?:^|/)[^.].*-[^-]+-[\d.]+-(?:' + '|'.join(supported_archs) + r')\.pkg\.tar\.xz(?:\.sig)?$')

  filter_func = partial(filter_pkg, regex)
  handler = EventHandler(
    filter_func,
    filter_pkg = filter_func,
    supported_archs = supported_archs,
    config = config,
    wm = wm,
  )
  ioloop = IOLoop.current()
  ret = [pyinotify.TornadoAsyncNotifier(
    wm,
    default_proc_fun=handler,
    ioloop = ioloop,
  )]

  if config.get('spool-directory'):
    wm = pyinotify.WatchManager()
    handler = SpoolHandler(
      filter_func,
      filter_pkg = filter_func,
      path = config.get('spool-directory'),
      dstpath = os.path.join(config.get('path'), 'any'),
      wm = wm,
    )
    ret.append(pyinotify.TornadoAsyncNotifier(
      wm, default_proc_fun=handler,
      ioloop = ioloop,
    ))

  return ret

class SpoolHandler(pyinotify.ProcessEvent):
  def my_init(self, filter_pkg, path, dstpath, wm):
    self.filter_pkg = filter_pkg
    self.dstpath = dstpath

    files = set()
    for f in os.listdir(path):
      p = os.path.join(path, f)
      if os.path.exists(p): # filter broken symlinks
        files.add(p)

    wm.add_watch(path, pyinotify.IN_CLOSE_WRITE | pyinotify.IN_CREATE |
                 pyinotify.IN_MOVED_TO)
    self._initial_update(files)

  def _initial_update(self, files):
    for f in sorted(filterfalse(self.filter_pkg, files),
                    key=pkgsortkey):
      self.dispatch(f)

  def process_IN_CLOSE_WRITE(self, event):
    logger.debug('Writing done: %s', event.pathname)
    self.dispatch(event.pathname)

  def process_IN_CREATE(self, event):
    file = event.pathname
    if os.path.islink(file):
      logger.debug('Symlinked: %s', file)
    else:
      logger.debug('Linked: %s', file)
    self.dispatch(file)

  def process_IN_MOVED_TO(self, event):
    logger.debug('Moved here: %s', event.pathname)
    self.dispatch(event.pathname)

  def dispatch(self, path):
    filename = os.path.basename(path)
    os.rename(path, os.path.join(self.dstpath, filename))
