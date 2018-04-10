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

import shutil
import tempfile
from collections import namedtuple

from mock import call, patch, MagicMock
from test_utils import CharmTestCase, patch_open

import lib.swift_storage_utils as swift_utils


TO_PATCH = [
    'apt_update',
    'apt_upgrade',
    'log',
    'config',
    'configure_installation_source',
    'mkdir',
    'mount',
    'check_call',
    'call',
    'ensure_block_device',
    'clean_storage',
    'is_block_device',
    'is_device_mounted',
    'get_os_codename_package',
    'get_os_codename_install_source',
    'unit_private_ip',
    'service_restart',
    '_save_script_rc',
    'lsb_release',
    'is_paused',
    'fstab_add',
    'mount',
    'is_mapped_loopback_device',
    'ufw',
    'iter_units_for_relation_name',
    'ingress_address',
    'relation_ids',
]


PROC_PARTITIONS = """
major minor  #blocks  name

   8        0  732574584 sda
   8        1     102400 sda1
   8        2  307097600 sda2
   8        3          1 sda3
   8        5  146483200 sda5
   8        6    4881408 sda6
   8        7  274004992 sda7
   8       16  175825944 sdb
   9        0  732574584 vda
  10        0  732574584 vdb
  10        0  732574584 vdb1
 104        0 1003393784 cciss/c0d0
 105        0 1003393784 cciss/c1d0
 105        1   86123689 cciss/c1d0p1
 252        0   20971520 dm-0
 252        1   15728640 dm-1
"""

SCRIPT_RC_ENV = {
    'OPENSTACK_PORT_ACCOUNT': 6002,
    'OPENSTACK_PORT_CONTAINER': 6001,
    'OPENSTACK_PORT_OBJECT': 6000,
    'OPENSTACK_SWIFT_SERVICE_ACCOUNT': 'account-server',
    'OPENSTACK_SWIFT_SERVICE_CONTAINER': 'container-server',
    'OPENSTACK_SWIFT_SERVICE_OBJECT': 'object-server',
    'OPENSTACK_URL_ACCOUNT':
    'http://10.0.0.1:6002/recon/diskusage|"mounted":true',
    'OPENSTACK_URL_CONTAINER':
    'http://10.0.0.1:6001/recon/diskusage|"mounted":true',
    'OPENSTACK_URL_OBJECT':
    'http://10.0.0.1:6000/recon/diskusage|"mounted":true'
}


REAL_WORLD_PARTITIONS = """
major minor  #blocks  name

   8        0  117220824 sda
   8        1  117219800 sdb
   8       16  119454720 sdb1
"""

FINDMNT_FOUND_TEMPLATE = """
TARGET        SOURCE   FSTYPE OPTIONS
{}           /dev/{} xfs    rw,relatime,attr2,inode64,noquota
"""


class SwiftStorageUtilsTests(CharmTestCase):

    def setUp(self):
        super(SwiftStorageUtilsTests, self).setUp(swift_utils, TO_PATCH)
        self.config.side_effect = self.test_config.get

    def test_ensure_swift_directories(self):
        with patch('os.path.isdir') as isdir:
            isdir.return_value = False
            swift_utils.ensure_swift_directories()
            ex_dirs = [
                call('/etc/swift', owner='swift', group='swift'),
                call('/var/cache/swift', owner='swift', group='swift'),
                call('/srv/node', owner='swift', group='swift')
            ]
        self.assertEqual(ex_dirs, self.mkdir.call_args_list)

    def test_swift_init_nonfatal(self):
        swift_utils.swift_init('all', 'start')
        self.call.assert_called_with(['swift-init', 'all', 'start'])

    def test_swift_init_fatal(self):
        swift_utils.swift_init('all', 'start', fatal=True)
        self.check_call.assert_called_with(['swift-init', 'all', 'start'])

    def test_fetch_swift_rings(self):
        url = 'http://someproxynode/rings'
        swift_utils.SWIFT_CONF_DIR = tempfile.mkdtemp()
        try:
            swift_utils.fetch_swift_rings(url)
            wgets = []
            for s in ['account', 'object', 'container']:
                _c = call(['wget', '%s/%s.ring.gz' % (url, s),
                           '--retry-connrefused', '-t', '10',
                           '-O', '/etc/swift/%s.ring.gz' % s])
                wgets.append(_c)
            self.assertEqual(wgets, self.check_call.call_args_list)
        except:
            shutil.rmtree(swift_utils.SWIFT_CONF_DIR)

    def test_determine_block_device_no_config(self):
        self.test_config.set('block-device', None)
        self.assertEqual(swift_utils.determine_block_devices(), None)

    def _fake_ensure(self, bdev):
        # /dev/vdz is a missing dev
        if '/dev/vdz' in bdev:
            return None
        else:
            return bdev.split('|').pop(0)

    @patch.object(swift_utils, 'ensure_block_device')
    def test_determine_block_device_single_dev(self, _ensure):
        _ensure.side_effect = self._fake_ensure
        bdevs = '/dev/vdb'
        self.test_config.set('block-device', bdevs)
        result = swift_utils.determine_block_devices()
        self.assertEqual(['/dev/vdb'], result)

    @patch.object(swift_utils, 'ensure_block_device')
    def test_determine_block_device_multi_dev(self, _ensure):
        _ensure.side_effect = self._fake_ensure
        bdevs = '/dev/vdb /dev/vdc /tmp/swift.img|1G'
        self.test_config.set('block-device', bdevs)
        result = swift_utils.determine_block_devices()
        ex = ['/dev/vdb', '/dev/vdc', '/tmp/swift.img']
        ex = list(set(ex))
        self.assertEqual(ex, result)

    @patch.object(swift_utils, 'ensure_block_device')
    def test_determine_block_device_duplicate_dev(self, _ensure):
        _ensure.side_effect = self._fake_ensure
        bdevs = '/dev/vdb /dev/vdc /dev/vdc /dev/vdb /tmp/swift.img|1G'
        self.test_config.set('block-device', bdevs)
        result = swift_utils.determine_block_devices()
        ex = ['/dev/vdb', '/dev/vdc', '/tmp/swift.img']
        ex = list(set(ex))
        self.assertEqual(ex, result)

    @patch.object(swift_utils, 'ensure_block_device')
    def test_determine_block_device_with_missing(self, _ensure):
        _ensure.side_effect = self._fake_ensure
        bdevs = '/dev/vdb /srv/swift.img|20G /dev/vdz'
        self.test_config.set('block-device', bdevs)
        result = swift_utils.determine_block_devices()
        ex = ['/dev/vdb', '/srv/swift.img']
        self.assertEqual(ex, result)

    @patch.object(swift_utils, 'check_output')
    @patch.object(swift_utils, 'find_block_devices')
    @patch.object(swift_utils, 'ensure_block_device')
    def test_determine_block_device_guess_dev(self, _ensure, _find,
                                              _check_output):
        "Devices already mounted under /srv/node/ should be returned"
        def _findmnt(cmd):
            dev = cmd[1].split('/')[-1]
            mnt_point = '/srv/node/' + dev
            return FINDMNT_FOUND_TEMPLATE.format(mnt_point, dev)
        _check_output.side_effect = _findmnt
        _ensure.side_effect = self._fake_ensure
        self.test_config.set('block-device', 'guess')
        _find.return_value = ['/dev/vdb', '/dev/sdb']
        result = swift_utils.determine_block_devices()
        self.assertTrue(_find.called)
        self.assertEqual(result, ['/dev/vdb', '/dev/sdb'])

    @patch.object(swift_utils, 'check_output')
    @patch.object(swift_utils, 'find_block_devices')
    @patch.object(swift_utils, 'ensure_block_device')
    def test_determine_block_device_guess_dev_not_eligable(self, _ensure,
                                                           _find,
                                                           _check_output):
        "Devices not mounted under /srv/node/ should not be returned"
        def _findmnt(cmd):
            dev = cmd[1].split('/')[-1]
            mnt_point = '/'
            return FINDMNT_FOUND_TEMPLATE.format(mnt_point, dev)
        _check_output.side_effect = _findmnt
        _ensure.side_effect = self._fake_ensure
        self.test_config.set('block-device', 'guess')
        _find.return_value = ['/dev/vdb']
        result = swift_utils.determine_block_devices()
        self.assertTrue(_find.called)
        self.assertEqual(result, [])

    def test_mkfs_xfs(self):
        swift_utils.mkfs_xfs('/dev/sdb')
        self.check_call.assert_called_with(
            ['mkfs.xfs', '-i', 'size=1024', '/dev/sdb']
        )

    def test_mkfs_xfs_force(self):
        swift_utils.mkfs_xfs('/dev/sdb', force=True)
        self.check_call.assert_called_with(
            ['mkfs.xfs', '-f', '-i', 'size=1024', '/dev/sdb']
        )

    @patch.object(swift_utils, 'is_device_in_ring')
    @patch.object(swift_utils, 'clean_storage')
    @patch.object(swift_utils, 'mkfs_xfs')
    @patch.object(swift_utils, 'determine_block_devices')
    def test_setup_storage_no_overwrite(self, determine, mkfs, clean,
                                        mock_is_device_in_ring):
        mock_is_device_in_ring.return_value = False
        determine.return_value = ['/dev/vdb']
        swift_utils.setup_storage()
        self.assertFalse(clean.called)
        calls = [call(['chown', '-R', 'swift:swift', '/srv/node/vdb']),
                 call(['chmod', '-R', '0755', '/srv/node/vdb'])]
        self.check_call.assert_has_calls(calls)
        self.mkdir.assert_has_calls([
            call('/srv/node', owner='swift', group='swift',
                 perms=0o755),
            call('/srv/node/vdb', group='swift', owner='swift')
        ])

    @patch.object(swift_utils, 'is_device_in_ring')
    @patch.object(swift_utils, 'clean_storage')
    @patch.object(swift_utils, 'mkfs_xfs')
    @patch.object(swift_utils, 'determine_block_devices')
    def test_setup_storage_overwrite(self, determine, mkfs, clean,
                                     mock_is_device_in_ring):
        self.test_config.set('overwrite', True)
        mock_is_device_in_ring.return_value = False
        self.is_mapped_loopback_device.return_value = None
        determine.return_value = ['/dev/vdb']
        swift_utils.setup_storage()
        clean.assert_called_with('/dev/vdb')
        self.mkdir.assert_called_with('/srv/node/vdb', owner='swift',
                                      group='swift')
        self.mount.assert_called_with('/dev/vdb', '/srv/node/vdb',
                                      filesystem='xfs')
        self.fstab_add.assert_called_with('/dev/vdb', '/srv/node/vdb',
                                          'xfs',
                                          options=None)
        calls = [call(['chown', '-R', 'swift:swift', '/srv/node/vdb']),
                 call(['chmod', '-R', '0755', '/srv/node/vdb'])]
        self.check_call.assert_has_calls(calls)
        self.mkdir.assert_has_calls([
            call('/srv/node', owner='swift', group='swift',
                 perms=0o755),
            call('/srv/node/vdb', group='swift', owner='swift')
        ])

    @patch.object(swift_utils, 'is_device_in_ring')
    @patch.object(swift_utils, 'determine_block_devices')
    def test_setup_storage_no_chmod_existing_devs(self, determine_block_devs,
                                                  mock_is_device_in_ring):
        """
        Verifies that only newly added and formatted storage devices are
        chmodded and chowned and not the entire /srv/node directory. Doing
        this will cause unnecessary write updates and for a production cluster,
        there could potentially be a lot of files to process.
        """
        determine_block_devs.return_values = ['/dev/vdb', '/dev/vdc']
        mock_is_device_in_ring.return_value = True
        swift_utils.setup_storage()
        self.assertEqual(self.check_call.call_count, 0)

    def _fake_is_device_mounted(self, device):
        if device in ["/dev/sda", "/dev/vda", "/dev/cciss/c0d0"]:
            return True
        else:
            return False

    def test_find_block_devices(self):
        self.is_block_device.return_value = True
        self.is_device_mounted.side_effect = self._fake_is_device_mounted
        with patch_open() as (_open, _file):
            _file.read.return_value = PROC_PARTITIONS
            _file.readlines = MagicMock()
            _file.readlines.return_value = PROC_PARTITIONS.split('\n')
            result = swift_utils.find_block_devices()
        ex = ['/dev/sdb', '/dev/vdb', '/dev/cciss/c1d0']
        self.assertEqual(ex, result)

    def test_find_block_devices_real_world(self):
        self.is_block_device.return_value = True
        side_effect = lambda x: x in ["/dev/sdb", "/dev/sdb1"] # flake8: noqa
        self.is_device_mounted.side_effect = side_effect
        with patch_open() as (_open, _file):
            _file.read.return_value = REAL_WORLD_PARTITIONS
            _file.readlines = MagicMock()
            _file.readlines.return_value = REAL_WORLD_PARTITIONS.split('\n')
            result = swift_utils.find_block_devices()
        expected = ["/dev/sda"]
        self.assertEqual(expected, result)

    def test_save_script_rc(self):
        self.unit_private_ip.return_value = '10.0.0.1'
        swift_utils.save_script_rc()
        self._save_script_rc.assert_called_with(**SCRIPT_RC_ENV)

    def test_assert_charm_not_supports_ipv6(self):
        self.lsb_release.return_value = {'DISTRIB_ID': 'Ubuntu',
                                         'DISTRIB_RELEASE': '12.04',
                                         'DISTRIB_CODENAME': 'precise',
                                         'DISTRIB_DESCRIPTION': 'Ubuntu 12.04'}
        self.assertRaises(Exception, swift_utils.assert_charm_supports_ipv6)

    def test_assert_charm_supports_ipv6(self):
        self.lsb_release.return_value = {'DISTRIB_ID': 'Ubuntu',
                                         'DISTRIB_RELEASE': '14.04',
                                         'DISTRIB_CODENAME': 'trusty',
                                         'DISTRIB_DESCRIPTION': 'Ubuntu 14.04'}
        swift_utils.assert_charm_supports_ipv6()

    @patch('charmhelpers.contrib.openstack.templating.OSConfigRenderer')
    def test_register_configs_pre_install(self, renderer):
        self.get_os_codename_package.return_value = None
        swift_utils.register_configs()
        renderer.assert_called_with(templates_dir=swift_utils.TEMPLATES,
                                    openstack_release='essex')

    @patch('charmhelpers.contrib.openstack.context.WorkerConfigContext')
    @patch('charmhelpers.contrib.openstack.context.BindHostContext')
    @patch.object(swift_utils, 'SwiftStorageContext')
    @patch.object(swift_utils, 'RsyncContext')
    @patch.object(swift_utils, 'SwiftStorageServerContext')
    @patch('charmhelpers.contrib.openstack.templating.OSConfigRenderer')
    def test_register_configs_post_install(self, renderer,
                                           swift, rsync, server,
                                           bind_context, worker_context):
        swift.return_value = 'swift_context'
        rsync.return_value = 'rsync_context'
        server.return_value = 'swift_server_context'
        bind_context.return_value = 'bind_host_context'
        worker_context.return_value = 'worker_context'
        self.get_os_codename_package.return_value = 'grizzly'
        configs = MagicMock()
        configs.register = MagicMock()
        renderer.return_value = configs
        swift_utils.register_configs()
        renderer.assert_called_with(templates_dir=swift_utils.TEMPLATES,
                                    openstack_release='grizzly')
        ex = [
            call('/etc/swift/swift.conf', ['swift_server_context']),
            call('/etc/rsync-juju.d/050-swift-storage.conf',
                 ['rsync_context', 'swift_context']),
            call('/etc/swift/account-server.conf', ['swift_context',
                                                    'bind_host_context',
                                                    'worker_context']),
            call('/etc/swift/object-server.conf', ['swift_context',
                                                   'bind_host_context',
                                                   'worker_context']),
            call('/etc/swift/container-server.conf', ['swift_context',
                                                      'bind_host_context',
                                                      'worker_context'])
        ]
        self.assertEqual(ex, configs.register.call_args_list)

    def test_do_upgrade(self):
        self.is_paused.return_value = False
        self.test_config.set('openstack-origin', 'cloud:precise-grizzly')
        self.get_os_codename_install_source.return_value = 'grizzly'
        swift_utils.do_openstack_upgrade(MagicMock())
        self.configure_installation_source.assert_called_with(
            'cloud:precise-grizzly'
        )
        dpkg_opts = [
            '--option', 'Dpkg::Options::=--force-confnew',
            '--option', 'Dpkg::Options::=--force-confdef',
        ]
        self.assertTrue(self.apt_update.called)
        self.apt_upgrade.assert_called_with(
            options=dpkg_opts,
            fatal=True, dist=True
        )
        services = (swift_utils.ACCOUNT_SVCS + swift_utils.CONTAINER_SVCS +
                    swift_utils.OBJECT_SVCS)
        for service in services:
            self.assertIn(call(service), self.service_restart.call_args_list)

    @patch.object(swift_utils, "is_device_in_ring")
    @patch.object(swift_utils, "mkfs_xfs")
    @patch.object(swift_utils, "determine_block_devices")
    def test_setup_storage_img(self, determine, mkfs, mock_is_device_in_ring):
        mock_is_device_in_ring.return_value = False
        determine.return_value = ["/srv/test.img", ]
        self.is_mapped_loopback_device.return_value = "/srv/test.img"
        swift_utils.setup_storage()
        self.mount.assert_called_with(
            "/srv/test.img",
            "/srv/node/test.img",
            filesystem="xfs",
        )
        self.fstab_add.assert_called_with(
            '/srv/test.img',
            '/srv/node/test.img',
            'xfs',
            options='loop,defaults'
        )

        self.mkdir.assert_has_calls([
            call('/srv/node', owner='swift', group='swift',
                 perms=0o755),
            call('/srv/node/test.img', group='swift', owner='swift')
        ])

    @patch.object(swift_utils.subprocess, "check_output")
    def test_get_device_blkid(self, mock_check_output):
        dev = '/dev/vdb'
        cmd = ['blkid', '-s', 'UUID', dev]
        ret = '/dev/vdb: UUID="808bc298-0609-4619-aaef-ed7a5ab0ebb7" \n'
        mock_check_output.return_value = ret
        uuid = swift_utils.get_device_blkid(dev)
        self.assertEqual(uuid, "808bc298-0609-4619-aaef-ed7a5ab0ebb7")
        mock_check_output.assert_called_with(cmd)

        def fake_check_output(*args, **kwargs):
            raise swift_utils.CalledProcessError('a', 'b', 'c')

        mock_check_output.side_effect = fake_check_output
        self.assertIsNone(swift_utils.get_device_blkid(dev))

    def test_grant_access(self):
        addr = '10.1.1.1'
        port = '80'
        self.ufw.grant_access = MagicMock()
        swift_utils.grant_access(addr, port)
        self.ufw.grant_access.assert_called_with(addr, port=port, index=1, proto='tcp')


    def test_revoke_access(self):
        addr = '10.1.1.1'
        port = '80'
        self.ufw.revoke_access = MagicMock()
        swift_utils.revoke_access(addr, port)
        self.ufw.revoke_access.assert_called_with(addr, port=port, proto='tcp')

    @patch.object(swift_utils, 'RsyncContext')
    @patch.object(swift_utils, 'grant_access')
    def test_setup_ufw(self, mock_grant_access, mock_rsync):
        peer_addr_1 = '10.1.1.1'
        peer_addr_2 = '10.1.1.2'
        client_addrs = ['10.3.3.1', '10.3.3.2','10.3.3.3', 'ubuntu.com']
        ports = [6660, 6661, 6662]
        self.test_config.set('object-server-port', ports[0])
        self.test_config.set('container-server-port', ports[1])
        self.test_config.set('account-server-port', ports[2])
        RelatedUnits = namedtuple('RelatedUnits', 'rid, unit')
        self.iter_units_for_relation_name.return_value = [
                RelatedUnits(rid='rid:1', unit='unit/1'),
                RelatedUnits(rid='rid:1', unit='unit/2'),
                RelatedUnits(rid='rid:1', unit='unit/3'),
                RelatedUnits(rid='rid:1', unit='unit/4')]
        self.ingress_address.side_effect = client_addrs
        context_call = MagicMock()
        context_call.return_value = {'allowed_hosts': '{} {}'
                                     ''.format(peer_addr_1, peer_addr_2)}
        mock_rsync.return_value = context_call
        swift_utils.setup_ufw()
        calls = []
        for addr in [peer_addr_1, peer_addr_2] + client_addrs:
            for port in ports:
                if addr == 'ubuntu.com':
                    calls.append(call('91.189.94.40', port))
                else:
                    calls.append(call(addr, port))
        mock_grant_access.assert_has_calls(calls)
