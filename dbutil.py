import sqlite3

def getver(db):
  try:
    ver = tuple(db.execute('select ver from version_info limit 1'))[0][0]
  except sqlite3.OperationalError:
    ver = '0.1' # This version has no version info
  return ver

def setver(db, ver):
  db.execute('''create table if not exists version_info
                (ver text)''')
  c = tuple(db.execute('select count(*) from version_info'))[0][0]
  if c == 1:
    db.execute('update version_info set ver=?', (ver,))
  else:
    db.execute('insert into version_info (ver) values (?)', (ver,))
  db.commit()
