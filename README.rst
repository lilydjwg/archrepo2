USAGE
=====

Install::

  pip3 install .

Edit a copy of ``archrepo.ini.example`` and then run
``archreposrv <config>``.

DEPENDENCIES
============

-  Python, >= 3.14, with sqlite support
-  setuptools
-  pyinotify, tested with 0.9.6
-  pyalpm, tested with 0.10.6

NOTE
====

-  relative symlinks may be broken when moving to the right architecture
   directory

TODO
====

-  [middle] specify what architectures we have and don't require others
-  [low] use one common command queue (now one each repo)
-  [low] verify packages

