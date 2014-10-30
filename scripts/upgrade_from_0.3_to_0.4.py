#!/usr/bin/env python3
# vim:fileencoding=utf-8

import os, sys
import sqlite3
import pickle
import logging

from archrepo2.lib.nicelogger import enable_pretty_logging
enable_pretty_logging(logging.DEBUG)

from archrepo2.dbutil import *

def main(dbname):
  db = sqlite3.connect(dbname, isolation_level=None)
  if getver(db) != '0.3':
    raise Exception('wrong database version')

  base_dir = os.path.dirname(dbname)
  input('Please stop the service and then press Enter.')

  p = db.execute('select filename from sigfiles limit 1').fetchone()[0]
  newp = os.path.relpath(p, start=base_dir)
  suffix_len = len(os.path.commonprefix((newp[::-1], p[::-1])))
  old_prefix = p[:-suffix_len]
  new_prefix = newp[:-suffix_len]
  db.execute('''
             UPDATE OR REPLACE sigfiles
             SET filename = REPLACE(filename, ?, ?)
             ''', (old_prefix, new_prefix))
  db.execute('''
             UPDATE OR REPLACE pkginfo
             SET filename = REPLACE(filename, ?, ?)
             ''', (old_prefix, new_prefix))

  setver(db, '0.4')
  db.close()

  input('Please re-start the service with new code and then press Enter.')

if __name__ == '__main__':
  if len(sys.argv) != 2:
    sys.exit('usage: upgrade_from_0.3_to_0.4.py info-database-file')
  main(*sys.argv[1:])
