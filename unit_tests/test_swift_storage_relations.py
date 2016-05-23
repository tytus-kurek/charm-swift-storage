from mock import patch
import os
import json
import uuid

from test_utils import CharmTestCase, patch_open

with patch('hooks.charmhelpers.contrib.hardening.harden.harden') as mock_dec:
    mock_dec.side_effect = (lambda *dargs, **dkwargs: lambda f:
                            lambda *args, **kwargs: f(*args, **kwargs))
    with patch('hooks.lib.misc_utils.is_paused') as is_paused:
        with patch('hooks.lib.swift_storage_utils.register_configs') as _:
            import hooks.swift_storage_hooks as hooks

from lib.swift_storage_utils import PACKAGES

TO_PATCH = [
    'CONFIGS',
    # charmhelpers.core.hookenv
    'Hooks',
    'config',
    'log',
    'relation_set',
    'relation_ids',
    'relation_get',
    'relations_of_type',
    # charmhelpers.core.host
    'apt_update',
    'apt_install',
    'filter_installed_packages',
    # charmehelpers.contrib.openstack.utils
    'configure_installation_source',
    'openstack_upgrade_available',
    # swift_storage_utils
    'determine_block_devices',
    'do_openstack_upgrade',
    'ensure_swift_directories',
    'execd_preinstall',
    'fetch_swift_rings',
    'save_script_rc',
    'setup_rsync',
    'setup_storage',
    'register_configs',
    'update_nrpe_config',
    'get_ipv6_addr',
    'status_set',
    'set_os_workload_status',
]


class SwiftStorageRelationsTests(CharmTestCase):

    def setUp(self):
        super(SwiftStorageRelationsTests, self).setUp(hooks,
                                                      TO_PATCH)
        self.config.side_effect = self.test_config.get
        self.relation_get.side_effect = self.test_relation.get

    def test_install_hook(self):
        self.test_config.set('openstack-origin', 'cloud:precise-havana')
        hooks.install()
        self.configure_installation_source.assert_called_with(
            'cloud:precise-havana',
        )
        self.assertTrue(self.apt_update.called)
        self.apt_install.assert_called_with(PACKAGES, fatal=True)

        self.assertTrue(self.setup_storage.called)
        self.assertTrue(self.execd_preinstall.called)

    def test_config_changed_no_upgrade_available(self):
        self.openstack_upgrade_available.return_value = False
        self.relations_of_type.return_value = False
        with patch_open() as (_open, _file):
            _file.read.return_value = "foo"
            hooks.config_changed()
        self.assertFalse(self.do_openstack_upgrade.called)
        self.assertTrue(self.CONFIGS.write_all.called)
        self.assertTrue(self.setup_rsync.called)

    def test_config_changed_upgrade_available(self):
        self.openstack_upgrade_available.return_value = True
        self.relations_of_type.return_value = False
        with patch_open() as (_open, _file):
            _file.read.return_value = "foo"
            hooks.config_changed()
        self.assertTrue(self.do_openstack_upgrade.called)
        self.assertTrue(self.CONFIGS.write_all.called)

    def test_config_changed_with_openstack_upgrade_action(self):
        self.openstack_upgrade_available.return_value = True
        self.test_config.set('action-managed-upgrade', True)

        with patch_open() as (_open, _file):
            _file.read.return_value = "foo"
            hooks.config_changed()

        self.assertFalse(self.do_openstack_upgrade.called)

    def test_config_changed_nrpe_master(self):
        self.openstack_upgrade_available.return_value = False
        self.relations_of_type.return_value = True
        with patch_open() as (_open, _file):
            _file.read.return_value = "foo"
            hooks.config_changed()
        self.assertTrue(self.CONFIGS.write_all.called)
        self.assertTrue(self.setup_rsync.called)
        self.assertTrue(self.update_nrpe_config.called)

    @patch.object(hooks, 'assert_charm_supports_ipv6')
    def test_config_changed_ipv6(self, mock_assert_charm_supports_ipv6):
        self.test_config.set('prefer-ipv6', True)
        self.openstack_upgrade_available.return_value = False
        self.relations_of_type.return_value = False
        with patch_open() as (_open, _file):
            _file.read.return_value = "foo"
            hooks.config_changed()
        self.assertTrue(self.CONFIGS.write_all.called)
        self.assertTrue(self.setup_rsync.called)

    @patch.object(hooks, 'ensure_devs_tracked')
    def test_upgrade_charm(self, mock_ensure_devs_tracked):
        self.filter_installed_packages.return_value = [
            'python-psutil']
        hooks.upgrade_charm()
        self.apt_install.assert_called_with([
            'python-psutil'], fatal=True)
        self.assertTrue(self.update_nrpe_config.called)
        self.assertTrue(mock_ensure_devs_tracked.called)

    @patch('hooks.lib.swift_storage_utils.get_device_blkid',
           lambda dev: str(uuid.uuid4()))
    @patch.object(hooks.os, 'environ')
    @patch('hooks.lib.swift_storage_utils.os.path.isdir', lambda *args: True)
    @patch.object(hooks, 'relation_set')
    @patch('hooks.lib.swift_storage_utils.local_unit')
    @patch('hooks.lib.swift_storage_utils.relation_ids', lambda *args: [])
    @patch('hooks.lib.swift_storage_utils.KVStore')
    @patch.object(uuid, 'uuid4', lambda: 'a-test-uuid')
    def _test_storage_joined_single_device(self, mock_kvstore, mock_local_unit,
                                           mock_rel_set, mock_environ,
                                           env_key):
        test_uuid = uuid.uuid4()
        test_environ = {env_key: test_uuid}
        mock_environ.get.side_effect = test_environ.get
        mock_local_unit.return_value = 'test/0'
        kvstore = mock_kvstore.return_value
        kvstore.__enter__.return_value = kvstore
        kvstore.get.return_value = None
        self.determine_block_devices.return_value = ['/dev/vdb']

        hooks.swift_storage_relation_joined()

        mock_rel_set.assert_called_with(
            relation_id=None,
            device='vdb', object_port=6000, account_port=6002,
            zone=1, container_port=6001
        )

        kvstore.get.return_value = None
        rel_settings = {}

        def fake_kv_set(key, value):
            rel_settings[key] = value

        kvstore.set.side_effect = fake_kv_set

        def fake_kv_get(key):
            return rel_settings.get(key)

        kvstore.get.side_effect = fake_kv_get
        devices = {"vdb@%s" % (test_uuid):
                   {"status": "active",
                    "blkid": 'a-test-uuid'}}
        kvstore.set.assert_called_with(key='devices',
                                       value=json.dumps(devices))

    def test_storage_joined_single_device_juju_1(self):
        '''Ensure use of JUJU_ENV_UUID for Juju < 2'''
        self._test_storage_joined_single_device(env_key='JUJU_ENV_UUID')

    def test_storage_joined_single_device_juju_2(self):
        '''Ensure use of JUJU_MODEL_UUID for Juju >= 2'''
        self._test_storage_joined_single_device(env_key='JUJU_MODEL_UUID')

    @patch('hooks.lib.swift_storage_utils.get_device_blkid',
           lambda dev: '%s-blkid-uuid' % os.path.basename(dev))
    @patch.object(hooks.os, 'environ')
    @patch('hooks.lib.swift_storage_utils.os.path.isdir', lambda *args: True)
    @patch.object(hooks, 'relation_set')
    @patch('hooks.lib.swift_storage_utils.relation_ids', lambda *args: [])
    @patch('hooks.lib.swift_storage_utils.KVStore')
    @patch.object(uuid, 'uuid4', lambda: 'a-test-uuid')
    def test_storage_joined_ipv6(self, mock_kvstore, mock_rel_set,
                                 mock_environ):
        kvstore = mock_kvstore.return_value
        kvstore.__enter__.return_value = kvstore
        kvstore.get.return_value = None

        self.determine_block_devices.return_value = ['/dev/vdb']
        self.test_config.set('prefer-ipv6', True)
        self.get_ipv6_addr.return_value = ['2001:db8:1::1']

        hooks.swift_storage_relation_joined()
        args = {
            'relation_id': None,
            'device': 'vdb', 'object_port': 6000,
            'account_port': 6002, 'zone': 1, 'container_port': 6001,
            'private-address': '2001:db8:1::1',
        }
        mock_rel_set.assert_called_with(**args)
        kvstore.get.assert_called_with(key='devices')

    @patch('hooks.lib.swift_storage_utils.get_device_blkid',
           lambda dev: '%s-blkid-uuid' % os.path.basename(dev))
    @patch.object(hooks.os, 'environ')
    @patch('hooks.lib.swift_storage_utils.os.path.isdir', lambda *args: True)
    @patch('hooks.lib.swift_storage_utils.local_unit')
    @patch('hooks.lib.swift_storage_utils.relation_ids', lambda *args: [])
    @patch('hooks.lib.swift_storage_utils.KVStore')
    @patch.object(uuid, 'uuid4', lambda: 'a-test-uuid')
    def test_storage_joined_multi_device(self, mock_kvstore, mock_local_unit,
                                         mock_environ):
        test_uuid = uuid.uuid4()
        test_environ = {'JUJU_ENV_UUID': test_uuid}
        mock_environ.get.side_effect = test_environ.get
        self.determine_block_devices.return_value = ['/dev/vdb', '/dev/vdc',
                                                     '/dev/vdd']
        mock_local_unit.return_value = 'test/0'
        kvstore = mock_kvstore.return_value
        kvstore.__enter__.return_value = kvstore
        kvstore.get.return_value = None
        rel_settings = {}

        def fake_kv_set(key, value):
            rel_settings[key] = value

        kvstore.set.side_effect = fake_kv_set

        def fake_kv_get(key):
            return rel_settings.get(key)

        kvstore.get.side_effect = fake_kv_get

        hooks.swift_storage_relation_joined()
        devices = {"vdb@%s" % (test_uuid): {"status": "active",
                                            "blkid": 'vdb-blkid-uuid'},
                   "vdd@%s" % (test_uuid): {"status": "active",
                                            "blkid": 'vdd-blkid-uuid'},
                   "vdc@%s" % (test_uuid): {"status": "active",
                                            "blkid": 'vdc-blkid-uuid'}}
        kvstore.set.assert_called_with(
            key='devices', value=json.dumps(devices)
        )

    @patch('hooks.lib.swift_storage_utils.get_device_blkid',
           lambda dev: '%s-blkid-uuid' % os.path.basename(dev))
    @patch.object(hooks.os, 'environ')
    @patch('hooks.lib.swift_storage_utils.os.path.isdir', lambda *args: True)
    @patch('hooks.lib.swift_storage_utils.local_unit')
    @patch('hooks.lib.swift_storage_utils.relation_ids', lambda *args: [])
    @patch('hooks.lib.swift_storage_utils.KVStore')
    def test_storage_joined_dev_exists_unknown_juju_env_uuid(self,
                                                             mock_kvstore,
                                                             mock_local_unit,
                                                             mock_environ):
        test_uuid = uuid.uuid4()
        test_environ = {'JUJU_ENV_UUID': test_uuid}
        mock_environ.get.side_effect = test_environ.get
        self.determine_block_devices.return_value = ['/dev/vdb', '/dev/vdc',
                                                     '/dev/vdd']
        mock_local_unit.return_value = 'test/0'
        kvstore = mock_kvstore.return_value
        kvstore.__enter__.return_value = kvstore
        kvstore.get.return_value = None
        store = {'vdb@%s' % (uuid.uuid4()): {"status": "active",
                                             "blkid": 'vdb-blkid-uuid'}}

        def fake_kv_set(key, value):
            store[key] = value

        kvstore.set.side_effect = fake_kv_set

        def fake_kv_get(key):
            return store.get(key)

        kvstore.get.side_effect = fake_kv_get

        hooks.swift_storage_relation_joined()
        devices = {"vdb@%s" % (test_uuid): {"status": "active",
                                            "blkid": 'vdb-blkid-uuid'},
                   "vdd@%s" % (test_uuid): {"status": "active",
                                            "blkid": 'vdd-blkid-uuid'},
                   "vdc@%s" % (test_uuid): {"status": "active",
                                            "blkid": 'vdc-blkid-uuid'}}
        kvstore.set.assert_called_with(
            key='devices', value=json.dumps(devices)
        )

    @patch('sys.exit')
    def test_storage_changed_missing_relation_data(self, exit):
        hooks.swift_storage_relation_changed()
        exit.assert_called_with(0)

    def test_storage_changed_with_relation_data(self):
        self.test_relation.set({
            'swift_hash': 'foo_hash',
            'rings_url': 'http://swift-proxy.com/rings/',
        })
        hooks.swift_storage_relation_changed()
        self.CONFIGS.write.assert_called_with('/etc/swift/swift.conf')
        self.fetch_swift_rings.assert_called_with(
            'http://swift-proxy.com/rings/'
        )

    @patch('sys.argv')
    def test_main_hook_missing(self, _argv):
        hooks.main()
        self.assertTrue(self.log.called)
