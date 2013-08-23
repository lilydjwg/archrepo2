USAGE
=====

Install::

  python3 setup.py install

Edit a copy of ``archrepo.ini.example`` and then run
``archreposrv <config>``.

DEPENDENCIES
============

-  Python, >= 3.3, with sqlite support
-  distribute
-  tornado, > 2.4.1
-  pyinotify, tested with 0.9.4

NOTE
====

-  relative symlinks may be broken when moving to the right architecture
   directory

TODO
====

-  [high] adding and then removing it before adding complete will result
   in not-in-database removing
-  [middle] specify what architectures we have and don't require others
-  [low] fork to background
-  [low] use one common command queue (now one each repo)
-  [low] verify packages

