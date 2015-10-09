import time

import amulet
import swiftclient

from charmhelpers.contrib.openstack.amulet.deployment import (
    OpenStackAmuletDeployment
)

from charmhelpers.contrib.openstack.amulet.utils import (
    OpenStackAmuletUtils,
    DEBUG,
)

# Use DEBUG to turn on debug logging
u = OpenStackAmuletUtils(DEBUG)


class SwiftStorageBasicDeployment(OpenStackAmuletDeployment):
    """Amulet tests on a basic swift-storage deployment."""

    def __init__(self, series, openstack=None, source=None, stable=False):
        """Deploy the entire test environment."""
        super(SwiftStorageBasicDeployment, self).__init__(series, openstack,
                                                          source, stable)
        self._add_services()
        self._add_relations()
        self._configure_services()
        self._deploy()
        self._initialize_tests()

    def _add_services(self):
        """Add services

           Add the services that we're testing, where swift-storage is local,
           and the rest of the service are from lp branches that are
           compatible with the local charm (e.g. stable or next).
           """
        this_service = {'name': 'swift-storage'}
        other_services = [{'name': 'mysql'}, {'name': 'keystone'},
                          {'name': 'glance'}, {'name': 'swift-proxy'}]
        super(SwiftStorageBasicDeployment, self)._add_services(this_service,
                                                               other_services)

    def _add_relations(self):
        """Add all of the relations for the services."""
        relations = {
            'keystone:shared-db': 'mysql:shared-db',
            'swift-proxy:identity-service': 'keystone:identity-service',
            'swift-storage:swift-storage': 'swift-proxy:swift-storage',
            'glance:identity-service': 'keystone:identity-service',
            'glance:shared-db': 'mysql:shared-db',
            'glance:object-store': 'swift-proxy:object-store'
        }
        super(SwiftStorageBasicDeployment, self)._add_relations(relations)

    def _configure_services(self):
        """Configure all of the services."""
        keystone_config = {'admin-password': 'openstack',
                           'admin-token': 'ubuntutesting'}
        swift_proxy_config = {
            'zone-assignment': 'manual',
            'replicas': '1',
            'swift-hash': 'fdfef9d4-8b06-11e2-8ac0-531c923c8fae'
        }
        swift_storage_config = {'zone': '1',
                                'block-device': 'vdb',
                                'overwrite': 'true'}
        configs = {'keystone': keystone_config,
                   'swift-proxy': swift_proxy_config,
                   'swift-storage': swift_storage_config}
        super(SwiftStorageBasicDeployment, self)._configure_services(configs)

    def _initialize_tests(self):
        """Perform final initialization before tests get run."""
        # Access the sentries for inspecting service units
        self.mysql_sentry = self.d.sentry.unit['mysql/0']
        self.keystone_sentry = self.d.sentry.unit['keystone/0']
        self.glance_sentry = self.d.sentry.unit['glance/0']
        self.swift_proxy_sentry = self.d.sentry.unit['swift-proxy/0']
        self.swift_storage_sentry = self.d.sentry.unit['swift-storage/0']

        u.log.debug('openstack release val: {}'.format(
            self._get_openstack_release()))
        u.log.debug('openstack release str: {}'.format(
            self._get_openstack_release_string()))

        # Let things settle a bit before moving forward
        time.sleep(30)

        # Authenticate admin with keystone
        self.keystone = u.authenticate_keystone_admin(self.keystone_sentry,
                                                      user='admin',
                                                      password='openstack',
                                                      tenant='admin')

        # Authenticate admin with glance endpoint
        self.glance = u.authenticate_glance_admin(self.keystone)

        # Authenticate swift user
        keystone_relation = self.keystone_sentry.relation(
            'identity-service', 'swift-proxy:identity-service')
        ep = self.keystone.service_catalog.url_for(service_type='identity',
                                                   endpoint_type='publicURL')
        self.swift = swiftclient.Connection(
            authurl=ep,
            user=keystone_relation['service_username'],
            key=keystone_relation['service_password'],
            tenant_name=keystone_relation['service_tenant'],
            auth_version='2.0')

        # Create a demo tenant/role/user
        self.demo_tenant = 'demoTenant'
        self.demo_role = 'demoRole'
        self.demo_user = 'demoUser'
        if not u.tenant_exists(self.keystone, self.demo_tenant):
            tenant = self.keystone.tenants.create(tenant_name=self.demo_tenant,
                                                  description='demo tenant',
                                                  enabled=True)
            self.keystone.roles.create(name=self.demo_role)
            self.keystone.users.create(name=self.demo_user,
                                       password='password',
                                       tenant_id=tenant.id,
                                       email='demo@demo.com')

        # Authenticate demo user with keystone
        self.keystone_demo = \
            u.authenticate_keystone_user(self.keystone, user=self.demo_user,
                                         password='password',
                                         tenant=self.demo_tenant)

    def test_100_services(self):
        """Verify the expected services are running on the corresponding
           service units."""
        u.log.debug('Checking system services...')
        swift_storage_services = ['swift-account',
                                  'swift-account-auditor',
                                  'swift-account-reaper',
                                  'swift-account-replicator',
                                  'swift-container',
                                  'swift-container-auditor',
                                  'swift-container-replicator',
                                  'swift-container-updater',
                                  'swift-object',
                                  'swift-object-auditor',
                                  'swift-object-replicator',
                                  'swift-object-updater']
        if self._get_openstack_release() >= self.precise_icehouse:
            swift_storage_services.append('swift-container-sync')
        service_names = {
            self.mysql_sentry: ['mysql'],
            self.keystone_sentry: ['keystone'],
            self.glance_sentry: ['glance-registry',
                                 'glance-api'],
            self.swift_proxy_sentry: ['swift-proxy'],
            self.swift_storage_sentry: swift_storage_services
        }

        ret = u.validate_services_by_name(service_names)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

    def test_102_users(self):
        """Verify all existing roles."""
        u.log.debug('Checking keystone users...')
        user1 = {'name': 'demoUser',
                 'enabled': True,
                 'tenantId': u.not_null,
                 'id': u.not_null,
                 'email': 'demo@demo.com'}
        user2 = {'name': 'admin',
                 'enabled': True,
                 'tenantId': u.not_null,
                 'id': u.not_null,
                 'email': 'juju@localhost'}
        user3 = {'name': 'glance',
                 'enabled': True,
                 'tenantId': u.not_null,
                 'id': u.not_null,
                 'email': u'juju@localhost'}
        user4 = {'name': 'swift',
                 'enabled': True,
                 'tenantId': u.not_null,
                 'id': u.not_null,
                 'email': u'juju@localhost'}
        expected = [user1, user2, user3, user4]
        actual = self.keystone.users.list()

        ret = u.validate_user_data(expected, actual)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

    def test_104_keystone_service_catalog(self):
        """Verify that the service catalog endpoint data is valid."""
        u.log.debug('Checking keystone service catalog...')
        endpoint_id = {'adminURL': u.valid_url,
                       'region': 'RegionOne',
                       'publicURL': u.valid_url,
                       'internalURL': u.valid_url,
                       'id': u.not_null}

        expected = {'image': [endpoint_id], 'object-store': [endpoint_id],
                    'identity': [endpoint_id]}
        actual = self.keystone_demo.service_catalog.get_endpoints()

        ret = u.validate_svc_catalog_endpoint_data(expected, actual)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

    def test_106_swift_object_store_endpoint(self):
        """Verify the swift object-store endpoint data."""
        u.log.debug('Checking keystone endpoint for swift object store...')
        endpoints = self.keystone.endpoints.list()
        admin_port = internal_port = public_port = '8080'
        expected = {'id': u.not_null,
                    'region': 'RegionOne',
                    'adminurl': u.valid_url,
                    'internalurl': u.valid_url,
                    'publicurl': u.valid_url,
                    'service_id': u.not_null}

        ret = u.validate_endpoint_data(endpoints, admin_port, internal_port,
                                       public_port, expected)
        if ret:
            message = 'object-store endpoint: {}'.format(ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_200_swift_storage_swift_storage_relation(self):
        """Verify the swift-storage to swift-proxy swift-storage relation
           data."""
        u.log.debug('Checking swift:swift-proxy swift-storage relation...')
        unit = self.swift_storage_sentry
        relation = ['swift-storage', 'swift-proxy:swift-storage']
        expected = {
            'account_port': '6002',
            'zone': '1',
            'object_port': '6000',
            'container_port': '6001',
            'private-address': u.valid_ip,
            'device': 'vdb'
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('swift-storage swift-storage', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_202_swift_proxy_swift_storage_relation(self):
        """Verify the swift-proxy to swift-storage swift-storage relation
           data."""
        u.log.debug('Checking swift-proxy:swift swift-storage relation...')
        unit = self.swift_proxy_sentry
        relation = ['swift-storage', 'swift-storage:swift-storage']
        expected = {
            'private-address': u.valid_ip,
            'trigger': u.not_null,
            'rings_url': u.valid_url,
            'swift_hash': u.not_null
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('swift-proxy swift-storage', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_300_swift_config(self):
        """Verify the data in the swift-hash section of the swift config
           file."""
        u.log.debug('Checking swift config...')
        unit = self.swift_storage_sentry
        conf = '/etc/swift/swift.conf'
        swift_proxy_relation = self.swift_proxy_sentry.relation(
            'swift-storage', 'swift-storage:swift-storage')
        expected = {
            'swift_hash_path_suffix': swift_proxy_relation['swift_hash']
        }

        ret = u.validate_config_data(unit, conf, 'swift-hash', expected)
        if ret:
            message = "swift config error: {}".format(ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_302_account_server_config(self):
        """Verify the data in the account server config file."""
        u.log.debug('Checking swift account-server config...')
        unit = self.swift_storage_sentry
        conf = '/etc/swift/account-server.conf'
        expected = {
            'DEFAULT': {
                'bind_ip': '0.0.0.0',
                'bind_port': '6002',
                'workers': '1'
            },
            'pipeline:main': {
                'pipeline': 'recon account-server'
            },
            'filter:recon': {
                'use': 'egg:swift#recon',
                'recon_cache_path': '/var/cache/swift'
            },
            'app:account-server': {
                'use': 'egg:swift#account'
            }
        }

        for section, pairs in expected.iteritems():
            ret = u.validate_config_data(unit, conf, section, pairs)
            if ret:
                message = "account server config error: {}".format(ret)
                amulet.raise_status(amulet.FAIL, msg=message)

    def test_304_container_server_config(self):
        """Verify the data in the container server config file."""
        u.log.debug('Checking swift container-server config...')
        unit = self.swift_storage_sentry
        conf = '/etc/swift/container-server.conf'
        expected = {
            'DEFAULT': {
                'bind_ip': '0.0.0.0',
                'bind_port': '6001',
                'workers': '1'
            },
            'pipeline:main': {
                'pipeline': 'recon container-server'
            },
            'filter:recon': {
                'use': 'egg:swift#recon',
                'recon_cache_path': '/var/cache/swift'
            },
            'app:container-server': {
                'use': 'egg:swift#container',
                'allow_versions': 'true'
            }
        }

        for section, pairs in expected.iteritems():
            ret = u.validate_config_data(unit, conf, section, pairs)
            if ret:
                message = "container server config error: {}".format(ret)
                amulet.raise_status(amulet.FAIL, msg=message)

    def test_306_object_server_config(self):
        """Verify the data in the object server config file."""
        u.log.debug('Checking swift object-server config...')
        unit = self.swift_storage_sentry
        conf = '/etc/swift/object-server.conf'
        expected = {
            'DEFAULT': {
                'bind_ip': '0.0.0.0',
                'bind_port': '6000',
                'workers': '1'
            },
            'pipeline:main': {
                'pipeline': 'recon object-server'
            },
            'filter:recon': {
                'use': 'egg:swift#recon',
                'recon_cache_path': '/var/cache/swift'
            },
            'app:object-server': {
                'use': 'egg:swift#object',
                'threads_per_disk': '4'
            },
            'object-replicator': {
                'concurrency': '1'
            }
        }

        for section, pairs in expected.iteritems():
            ret = u.validate_config_data(unit, conf, section, pairs)
            if ret:
                message = "object server config error: {}".format(ret)
                amulet.raise_status(amulet.FAIL, msg=message)

    def test_400_swift_backed_image_create(self):
        """Create an instance in glance, which is backed by swift, and validate
        that some of the metadata for the image match in glance and swift."""
        u.log.debug('Checking swift objects and containers with a '
                    'swift-backed glance image...')

        # Create swift-backed glance image
        img_new = u.create_cirros_image(self.glance, "cirros-image-1")
        img_id = img_new.id
        img_md5 = img_new.checksum
        img_size = img_new.size

        # Validate that swift object's checksum/size match that from glance
        headers, containers = self.swift.get_account()
        if len(containers) != 1:
            msg = "Expected 1 swift container, found {}".format(
                len(containers))
            amulet.raise_status(amulet.FAIL, msg=msg)

        container_name = containers[0].get('name')

        headers, objects = self.swift.get_container(container_name)
        if len(objects) != 1:
            msg = "Expected 1 swift object, found {}".format(len(objects))
            amulet.raise_status(amulet.FAIL, msg=msg)

        swift_object_size = objects[0].get('bytes')
        swift_object_md5 = objects[0].get('hash')

        if img_size != swift_object_size:
            msg = "Glance image size {} != swift object size {}".format(
                img_size, swift_object_size)
            amulet.raise_status(amulet.FAIL, msg=msg)

        if img_md5 != swift_object_md5:
            msg = "Glance image hash {} != swift object hash {}".format(
                img_md5, swift_object_md5)
            amulet.raise_status(amulet.FAIL, msg=msg)

        # Cleanup
        u.delete_resource(self.glance.images, img_id, msg="glance image")
        u.log.info('OK')

    def test_900_restart_on_config_change(self):
        """Verify that the specified services are restarted when the config
           is changed."""
        u.log.info('Checking that conf files and system services respond '
                   'to a charm config change...')
        sentry = self.swift_storage_sentry
        juju_service = 'swift-storage'

        # Expected default and alternate values
        set_default = {'object-server-threads-per-disk': '4'}
        set_alternate = {'object-server-threads-per-disk': '2'}

        # Config file affected by juju set config change, and
        # services which are expected to restart upon config change
        services = {'swift-object-server': 'object-server.conf',
                    'swift-object-auditor': 'object-server.conf',
                    'swift-object-replicator': 'object-server.conf',
                    'swift-object-updater': 'object-server.conf'}

        # Make config change, check for service restarts
        u.log.debug('Making config change on {}...'.format(juju_service))
        mtime = u.get_sentry_time(sentry)
        self.d.configure(juju_service, set_alternate)

        sleep_time = 40
        for s, conf_file in services.iteritems():
            u.log.debug("Checking that service restarted: {}".format(s))
            conf_file_abs = '/etc/swift/{}'.format(conf_file)
            if not u.validate_service_config_changed(sentry, mtime, s,
                                                     conf_file_abs,
                                                     sleep_time=sleep_time,
                                                     pgrep_full=True):
                self.d.configure(juju_service, set_default)
                msg = "service {} didn't restart after config change".format(s)
                amulet.raise_status(amulet.FAIL, msg=msg)
            sleep_time = 0

        self.d.configure(juju_service, set_default)

    def _assert_services(self, should_run):
        swift_storage_services = ['swift-account-auditor',
                                  'swift-account-reaper',
                                  'swift-account-replicator',
                                  'swift-account-server',
                                  'swift-container-auditor',
                                  'swift-container-replicator',
                                  'swift-container-server',
                                  'swift-container-sync',
                                  'swift-container-updater',
                                  'swift-object-auditor',
                                  'swift-object-replicator',
                                  'swift-object-server',
                                  'swift-object-updater']
        if self._get_openstack_release() < self.precise_icehouse:
            swift_storage_services.remove('swift-container-sync')

        u.get_unit_process_ids(
            {self.swift_storage_sentry: swift_storage_services},
            expect_success=should_run)
        # No point using validate_unit_process_ids, since we don't
        # care about how many PIDs, merely that they're running, so
        # would populate expected with either True or False. This
        # validation is already performed in get_process_id_list

    def _test_pause(self):
        u.log.info("Testing pause action")
        self._assert_services(should_run=True)
        pause_action_id = u.run_action(self.swift_storage_sentry, "pause")
        assert u.wait_on_action(pause_action_id), "Pause action failed."

        self._assert_services(should_run=False)

    def _test_resume(self):
        u.log.info("Testing resume action")
        # service is left paused by _test_pause
        self._assert_services(should_run=False)
        resume_action_id = u.run_action(self.swift_storage_sentry, "resume")
        assert u.wait_on_action(resume_action_id), "Resume action failed."

        self._assert_services(should_run=True)

    def test_910_pause_resume_actions(self):
        """Pause and then resume swift-storage."""
        u.log.debug('Checking pause/resume actions...')
        self._test_pause()
        self._test_resume()

    def test_920_no_restart_on_config_change_when_paused(self):
        """Verify that the specified services are not restarted when the config
           is changed and the unit is paused."""
        if self._get_openstack_release() <= self.precise_icehouse:
            return

        u.log.info('Checking that system services do not get restarted  '
                   'when charm config changes but unit is paused...')
        sentry = self.swift_storage_sentry
        juju_service = 'swift-storage'

        # Expected default and alternate values
        set_default = {'object-server-threads-per-disk': '4'}
        set_alternate = {'object-server-threads-per-disk': '2'}

        services = ['swift-account-server',
                    'swift-account-auditor',
                    'swift-account-reaper',
                    'swift-account-replicator',
                    'swift-container-server',
                    'swift-container-auditor',
                    'swift-container-replicator',
                    'swift-container-updater',
                    'swift-object-server',
                    'swift-object-auditor',
                    'swift-object-replicator',
                    'swift-object-updater',
                    'swift-container-sync']
        if self._get_openstack_release() < self.precise_icehouse:
            services.remove('swift-container-sync')

        # Pause the unit
        u.log.debug('Pausing the unit...')
        pause_action_id = u.run_action(sentry, "pause")
        assert u.wait_on_action(pause_action_id), "Pause action failed."

        # Make config change, check for service restarts
        u.log.debug('Making config change on {}...'.format(juju_service))
        self.d.configure(juju_service, set_alternate)

        for service in services:
            u.log.debug("Checking that service didn't start while "
                        "paused: {}".format(service))
            # No explicit assert because get_process_id_list will do it for us
            u.get_process_id_list(
                sentry, service, expect_success=False)

        self.d.configure(juju_service, set_default)
        resume_action_id = u.run_action(sentry, "resume")
        assert u.wait_on_action(resume_action_id), "Resume action failed."
