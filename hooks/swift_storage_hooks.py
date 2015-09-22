#!/usr/bin/python

import os
import sys

from lib.swift_storage_utils import (
    PACKAGES,
    RESTART_MAP,
    SWIFT_SVCS,
    determine_block_devices,
    do_openstack_upgrade,
    ensure_swift_directories,
    fetch_swift_rings,
    register_configs,
    save_script_rc,
    setup_storage,
    assert_charm_supports_ipv6,
    setup_rsync,
)

from lib.misc_utils import pause_aware_restart_on_change

from charmhelpers.core.hookenv import (
    Hooks, UnregisteredHookError,
    config,
    log,
    relation_get,
    relation_set,
    relations_of_type,
)

from charmhelpers.fetch import (
    apt_install,
    apt_update,
    filter_installed_packages
)
from charmhelpers.core.host import rsync
from charmhelpers.payload.execd import execd_preinstall

from charmhelpers.contrib.openstack.utils import (
    configure_installation_source,
    openstack_upgrade_available,
)
from charmhelpers.contrib.network.ip import (
    get_ipv6_addr
)
from charmhelpers.contrib.charmsupport import nrpe

from distutils.dir_util import mkpath

hooks = Hooks()
CONFIGS = register_configs()
NAGIOS_PLUGINS = '/usr/local/lib/nagios/plugins'
SUDOERS_D = '/etc/sudoers.d'


@hooks.hook()
def install():
    execd_preinstall()
    configure_installation_source(config('openstack-origin'))
    apt_update()
    apt_install(PACKAGES, fatal=True)
    setup_storage()
    ensure_swift_directories()


@hooks.hook('config-changed')
@pause_aware_restart_on_change(RESTART_MAP)
def config_changed():
    if config('prefer-ipv6'):
        assert_charm_supports_ipv6()

    ensure_swift_directories()
    setup_rsync()

    if not config('action-managed-upgrade') and \
            openstack_upgrade_available('swift'):
        do_openstack_upgrade(configs=CONFIGS)
    CONFIGS.write_all()

    save_script_rc()
    if relations_of_type('nrpe-external-master'):
        update_nrpe_config()


@hooks.hook('upgrade-charm')
def upgrade_charm():
    apt_install(filter_installed_packages(PACKAGES), fatal=True)
    update_nrpe_config()


@hooks.hook()
def swift_storage_relation_joined():
    devs = [os.path.basename(dev) for dev in determine_block_devices()]
    rel_settings = {
        'zone': config('zone'),
        'object_port': config('object-server-port'),
        'container_port': config('container-server-port'),
        'account_port': config('account-server-port'),
        'device': ':'.join(devs),
    }

    if config('prefer-ipv6'):
        rel_settings['private-address'] = get_ipv6_addr()[0]

    relation_set(**rel_settings)


@hooks.hook('swift-storage-relation-changed')
@pause_aware_restart_on_change(RESTART_MAP)
def swift_storage_relation_changed():
    rings_url = relation_get('rings_url')
    swift_hash = relation_get('swift_hash')
    if '' in [rings_url, swift_hash] or None in [rings_url, swift_hash]:
        log('swift_storage_relation_changed: Peer not ready?')
        sys.exit(0)
    CONFIGS.write('/etc/swift/swift.conf')
    fetch_swift_rings(rings_url)


@hooks.hook('nrpe-external-master-relation-joined')
@hooks.hook('nrpe-external-master-relation-changed')
def update_nrpe_config():
    # python-dbus is used by check_upstart_job
    apt_install('python-dbus')
    log('Refreshing nrpe checks')
    if not os.path.exists(NAGIOS_PLUGINS):
        mkpath(NAGIOS_PLUGINS)
    rsync(os.path.join(os.getenv('CHARM_DIR'), 'files', 'nrpe-external-master',
                       'check_swift_storage.py'),
          os.path.join(NAGIOS_PLUGINS, 'check_swift_storage.py'))
    rsync(os.path.join(os.getenv('CHARM_DIR'), 'files', 'nrpe-external-master',
                       'check_swift_service'),
          os.path.join(NAGIOS_PLUGINS, 'check_swift_service'))
    rsync(os.path.join(os.getenv('CHARM_DIR'), 'files', 'sudo',
                       'swift-storage'),
          os.path.join(SUDOERS_D, 'swift-storage'))

    # Find out if nrpe set nagios_hostname
    hostname = nrpe.get_nagios_hostname()
    current_unit = nrpe.get_nagios_unit_name()
    nrpe_setup = nrpe.NRPE(hostname=hostname)

    # check the rings and replication
    nrpe_setup.add_check(
        shortname='swift_storage',
        description='Check swift storage ring hashes and replication'
                    ' {%s}' % current_unit,
        check_cmd='check_swift_storage.py {}'.format(
            config('nagios-check-params'))
    )
    nrpe.add_init_service_checks(nrpe_setup, SWIFT_SVCS, current_unit)
    nrpe_setup.write()


def main():
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log('Unknown hook {} - skipping.'.format(e))


if __name__ == '__main__':
    main()
