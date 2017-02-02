#!/usr/bin/python
#
# Copyright 2016 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
    remember_devices,
    REQUIRED_INTERFACES,
    assess_status,
    ensure_devs_tracked,
    VERSION_PACKAGE,
)

from lib.misc_utils import pause_aware_restart_on_change

from charmhelpers.core.hookenv import (
    Hooks, UnregisteredHookError,
    config,
    log,
    relation_get,
    relation_ids,
    relation_set,
    relations_of_type,
    status_set,
)

from charmhelpers.fetch import (
    apt_install,
    apt_update,
    filter_installed_packages
)
from charmhelpers.core.host import (
    add_to_updatedb_prunepath,
    rsync,
)

from charmhelpers.core.sysctl import create as create_sysctl

from charmhelpers.payload.execd import execd_preinstall

from charmhelpers.contrib.openstack.utils import (
    configure_installation_source,
    openstack_upgrade_available,
    set_os_workload_status,
    os_application_version_set,
)
from charmhelpers.contrib.network.ip import (
    get_ipv6_addr
)
from charmhelpers.contrib.charmsupport import nrpe
from charmhelpers.contrib.hardening.harden import harden

from distutils.dir_util import mkpath

hooks = Hooks()
CONFIGS = register_configs()
NAGIOS_PLUGINS = '/usr/local/lib/nagios/plugins'
SUDOERS_D = '/etc/sudoers.d'
STORAGE_MOUNT_PATH = '/srv/node'


@hooks.hook('install.real')
@harden()
def install():
    status_set('maintenance', 'Executing pre-install')
    execd_preinstall()
    configure_installation_source(config('openstack-origin'))
    status_set('maintenance', 'Installing apt packages')
    apt_update()
    apt_install(PACKAGES, fatal=True)
    status_set('maintenance', 'Setting up storage')
    setup_storage()
    ensure_swift_directories()


@hooks.hook('config-changed')
@pause_aware_restart_on_change(RESTART_MAP)
@harden()
def config_changed():
    if config('prefer-ipv6'):
        status_set('maintenance', 'Configuring ipv6')
        assert_charm_supports_ipv6()

    ensure_swift_directories()
    setup_rsync()

    if not config('action-managed-upgrade') and \
            openstack_upgrade_available('swift'):
        status_set('maintenance', 'Running openstack upgrade')
        do_openstack_upgrade(configs=CONFIGS)

    setup_storage()

    for rid in relation_ids('swift-storage'):
        swift_storage_relation_joined(rid=rid)

    CONFIGS.write_all()

    save_script_rc()
    if relations_of_type('nrpe-external-master'):
        update_nrpe_config()

    sysctl_dict = config('sysctl')
    if sysctl_dict:
        create_sysctl(sysctl_dict, '/etc/sysctl.d/50-swift-storage-charm.conf')

    add_to_updatedb_prunepath(STORAGE_MOUNT_PATH)


@hooks.hook('upgrade-charm')
@harden()
def upgrade_charm():
    apt_install(filter_installed_packages(PACKAGES), fatal=True)
    update_nrpe_config()
    ensure_devs_tracked()


@hooks.hook()
def swift_storage_relation_joined(rid=None):
    rel_settings = {
        'zone': config('zone'),
        'object_port': config('object-server-port'),
        'container_port': config('container-server-port'),
        'account_port': config('account-server-port'),
    }

    devs = determine_block_devices() or []
    devs = [os.path.basename(d) for d in devs]
    rel_settings['device'] = ':'.join(devs)
    # Keep a reference of devices we are adding to the ring
    remember_devices(devs)

    if config('prefer-ipv6'):
        rel_settings['private-address'] = get_ipv6_addr()[0]

    relation_set(relation_id=rid, **rel_settings)


@hooks.hook('swift-storage-relation-changed')
@pause_aware_restart_on_change(RESTART_MAP)
def swift_storage_relation_changed():
    rings_url = relation_get('rings_url')
    swift_hash = relation_get('swift_hash')
    if '' in [rings_url, swift_hash] or None in [rings_url, swift_hash]:
        log('swift_storage_relation_changed: Peer not ready?')
        sys.exit(0)

    CONFIGS.write('/etc/rsync-juju.d/050-swift-storage.conf')
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


@hooks.hook('update-status')
@harden()
def update_status():
    log('Updating status.')


def main():
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log('Unknown hook {} - skipping.'.format(e))
    set_os_workload_status(CONFIGS, REQUIRED_INTERFACES,
                           charm_func=assess_status)
    os_application_version_set(VERSION_PACKAGE)


if __name__ == '__main__':
    main()
