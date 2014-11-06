"""
Support for rsyncd.conf and using fragments dropped inside /etc/rsync-juju.d
"""
import os

from charmhelpers.core.host import (
    mkdir,
)

def setup_rsync():
    '''
    Ensure all directories required for rsync exist with correct permissions.
    '''
    root_dirs = [
        '/etc/rsync-juju.d',
    ]
    [mkdir(d, owner='root', group='root') for d in root_dirs
     if not os.path.isdir(d)]

    rsyncd_base = """uid = nobody
gid = nogroup
pid file = /var/run/rsyncd.pid
syslog facility = daemon
socket options = SO_KEEPALIVE

&include /etc/rsync-juju.d
"""

    f = open('/etc/rsyncd.conf','w')
    f.write(rsyncd_base)
    f.close()

