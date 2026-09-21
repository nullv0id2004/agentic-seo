#!/bin/sh
# App Service SSH sessions do not inherit the container's app settings. Export them into a profile
# script so a login shell sees the same environment as the worker process (secrets are still only
# ever reachable from inside the container, through the portal's SSH tunnel).
python3 - <<'PY'
import os, shlex
with open("/etc/profile.d/appsvc.sh", "w") as f:
    for k, v in os.environ.items():
        if k.isidentifier():
            f.write(f"export {k}={shlex.quote(v)}\n")
PY
# Start the App Service SSH daemon (portal console only), then the worker in the foreground.
/usr/sbin/sshd
exec python worker.py
