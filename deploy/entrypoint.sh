#!/bin/sh
# Start the App Service SSH daemon (portal console only), then the worker in the foreground.
/usr/sbin/sshd
exec python worker.py
