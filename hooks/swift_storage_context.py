import re

from charmhelpers.core.hookenv import (
    config,
    log,
    related_units,
    relation_get,
    relation_ids,
    unit_private_ip,
)

from charmhelpers.contrib.openstack.context import (
    OSContextGenerator,
)

from charmhelpers.contrib.network.ip import (
    get_ipv6_addr,
)
class SwiftStorageContext(OSContextGenerator):
    interfaces = ['swift-storage']

    def __call__(self):
        rids = relation_ids('swift-storage')
        if not rids:
            return {}

        swift_hash = None
        for rid in rids:
            for unit in related_units(rid):
                if not swift_hash:
                    swift_hash = relation_get('swift_hash', rid=rid,
                                              unit=unit)
        if not swift_hash:
            log('No swift_hash passed via swift-storage relation. '
                'Peer not ready?')
            return {}
        return {'swift_hash': swift_hash}


class RsyncContext(OSContextGenerator):
    interfaces = []

    def enable_rsyncd(self):
        with open('/etc/default/rsync') as _in:
            default = _in.read()
        _m = re.compile('^RSYNC_ENABLE=(.*)$', re.MULTILINE)
        if not re.search(_m, default):
            with open('/etc/default/rsync', 'a+') as out:
                out.write('RSYNC_ENABLE=true\n')
        else:
            with open('/etc/default/rsync', 'w') as out:
                out.write(_m.sub('RSYNC_ENABLE=true', default))

    def __call__(self):
        if config('prefer-ipv6'):
            local_ip = '%s' % get_ipv6_addr()
        else:
            local_ip = unit_private_ip()

        self.enable_rsyncd()
        return {
            'local_ip': local_ip
        }


class SwiftStorageServerContext(OSContextGenerator):
    interfaces = []

    def __call__(self):
        import psutil
        multiplier = int(config('worker-multiplier')) or 1
        if config('prefer-ipv6'):
            bind_ip = '::'
        else:
            bind_ip = '0.0.0.0'

        ctxt = {
            'local_ip': unit_private_ip(),
            'account_server_port': config('account-server-port'),
            'container_server_port': config('container-server-port'),
            'object_server_port': config('object-server-port'),
            'workers': str(psutil.NUM_CPUS * multiplier),
            'object_server_threads_per_disk': config(
                'object-server-threads-per-disk'),
            'bind_ip': bind_ip,
        }
        return ctxt
