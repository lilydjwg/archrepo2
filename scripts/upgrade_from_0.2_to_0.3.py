#!/usr/bin/env python3
# vim:fileencoding=utf-8

import os, sys
import sqlite3
import pickle
import logging

from myutils import enable_pretty_logging
enable_pretty_logging(logging.DEBUG)

top_dir = os.path.normpath(os.path.join(__file__, '../..'))
sys.path.append(top_dir)

from dbutil import *

def main(dbname, reponame):
  db = sqlite3.connect(dbname, isolation_level=None)
  if getver(db) != '0.2':
    raise Exception('wrong database version')

  input('Please stop the service and then press Enter.')
  try:
    db.execute('alter table pkginfo add pkgrepo text')
    db.execute('update pkginfo set pkgrepo = ?', (reponame,))
  except sqlite3.OperationalError:
    # the column is already there
    pass
  try:
    db.execute('alter table sigfiles add pkgrepo text')
    db.execute('update sigfiles set pkgrepo = ?', (reponame,))
  except sqlite3.OperationalError:
    # the column is already there
    pass

  setver(db, '0.3')
  db.close()

  input('Please re-start the service with new code and then press Enter.')

if __name__ == '__main__':
  if len(sys.argv) != 3:
    sys.exit('usage: upgrade_from_0.2_to_0.3.py info-database-file repository-name')
  main(*sys.argv[1:])
