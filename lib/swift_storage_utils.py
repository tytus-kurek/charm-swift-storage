import json
import os
import re
import subprocess
import shutil
import tempfile

from subprocess import check_call, call, CalledProcessError, check_output

# Stuff copied from cinder py charm, needs to go somewhere
# common.
from misc_utils import (
    ensure_block_device,
    clean_storage,
    is_paused
)

from swift_storage_context import (
    SwiftStorageContext,
    SwiftStorageServerContext,
    RsyncContext,
)

from charmhelpers.fetch import (
    apt_upgrade,
    apt_update
)

from charmhelpers.core.unitdata import (
    Storage as KVStore,
)

import charmhelpers.core.fstab

from charmhelpers.core.host import (
    mkdir,
    mount,
    fstab_add,
    service_restart,
    lsb_release,
    CompareHostReleases,
)

from charmhelpers.core.hookenv import (
    config,
    log,
    DEBUG,
    INFO,
    WARNING,
    ERROR,
    unit_private_ip,
    local_unit,
    relation_get,
    relation_ids,
    iter_units_for_relation_name,
    ingress_address,
)

from charmhelpers.contrib.network import ufw
from charmhelpers.contrib.network.ip import get_host_ip

from charmhelpers.contrib.storage.linux.utils import (
    is_block_device,
    is_device_mounted,
)

from charmhelpers.contrib.storage.linux.loopback import (
    is_mapped_loopback_device,
)

from charmhelpers.contrib.openstack.utils import (
    configure_installation_source,
    get_os_codename_install_source,
    get_os_codename_package,
    save_script_rc as _save_script_rc,
)

from charmhelpers.contrib.openstack import (
    templating,
    context
)

from charmhelpers.core.decorators import (
    retry_on_exception,
)

PACKAGES = [
    'swift', 'swift-account', 'swift-container', 'swift-object',
    'xfsprogs', 'gdisk', 'lvm2', 'python-jinja2', 'python-psutil',
]

VERSION_PACKAGE = 'swift-account'

TEMPLATES = 'templates/'

REQUIRED_INTERFACES = {
    'proxy': ['swift-storage'],
}

ACCOUNT_SVCS = [
    'swift-account', 'swift-account-auditor',
    'swift-account-reaper', 'swift-account-replicator'
]

CONTAINER_SVCS = [
    'swift-container', 'swift-container-auditor',
    'swift-container-updater', 'swift-container-replicator',
    'swift-container-sync'
]

OBJECT_SVCS = [
    'swift-object', 'swift-object-auditor',
    'swift-object-updater', 'swift-object-replicator'
]

SWIFT_SVCS = ACCOUNT_SVCS + CONTAINER_SVCS + OBJECT_SVCS

RESTART_MAP = {
    '/etc/rsync-juju.d/050-swift-storage.conf': ['rsync'],
    '/etc/swift/account-server.conf': ACCOUNT_SVCS,
    '/etc/swift/container-server.conf': CONTAINER_SVCS,
    '/etc/swift/object-server.conf': OBJECT_SVCS,
    '/etc/swift/swift.conf': ACCOUNT_SVCS + CONTAINER_SVCS + OBJECT_SVCS
}

SWIFT_CONF_DIR = '/etc/swift'
SWIFT_RING_EXT = 'ring.gz'

FIRST = 1

# NOTE(hopem): we intentionally place this database outside of unit context so
#              that if the unit, service or even entire environment is
#              destroyed, there will still be a record of what devices were in
#              use so that when the swift charm next executes, already used
#              devices will not be unintentionally reformatted. If devices are
#              to be recycled, they will need to be manually removed from this
#              database.
# FIXME: add charm support for removing devices (see LP: #1448190)
KV_DB_PATH = '/var/lib/juju/swift_storage/charm_kvdata.db'


def ensure_swift_directories():
    '''
    Ensure all directories required for a swift storage node exist with
    correct permissions.
    '''
    dirs = [
        SWIFT_CONF_DIR,
        '/var/cache/swift',
        '/srv/node',
    ]
    [mkdir(d, owner='swift', group='swift') for d in dirs
     if not os.path.isdir(d)]


def register_configs():
    release = get_os_codename_package('python-swift', fatal=False) or 'essex'
    configs = templating.OSConfigRenderer(templates_dir=TEMPLATES,
                                          openstack_release=release)
    configs.register('/etc/swift/swift.conf',
                     [SwiftStorageContext()])
    configs.register('/etc/rsync-juju.d/050-swift-storage.conf',
                     [RsyncContext(), SwiftStorageServerContext()])
    for server in ['account', 'object', 'container']:
        configs.register('/etc/swift/%s-server.conf' % server,
                         [SwiftStorageServerContext(),
                          context.BindHostContext(),
                          context.WorkerConfigContext()]),
    return configs


def swift_init(target, action, fatal=False):
    '''
    Call swift-init on a specific target with given action, potentially
    raising exception.
    '''
    cmd = ['swift-init', target, action]
    if fatal:
        return check_call(cmd)
    return call(cmd)


def do_openstack_upgrade(configs):
    new_src = config('openstack-origin')
    new_os_rel = get_os_codename_install_source(new_src)

    log('Performing OpenStack upgrade to %s.' % (new_os_rel))
    configure_installation_source(new_src)
    dpkg_opts = [
        '--option', 'Dpkg::Options::=--force-confnew',
        '--option', 'Dpkg::Options::=--force-confdef',
    ]
    apt_update()
    apt_upgrade(options=dpkg_opts, fatal=True, dist=True)
    configs.set_release(openstack_release=new_os_rel)
    configs.write_all()
    if not is_paused():
        for service in SWIFT_SVCS:
            service_restart(service)


def _is_storage_ready(partition):
    """
    A small helper to determine if a given device is suitabe to be used as
    a storage device.
    """
    return is_block_device(partition) and not is_device_mounted(partition)


def get_mount_point(device):
    mnt_point = None
    try:
        out = check_output(['findmnt', device])
        mnt_points = []
        for line in out.split('\n'):
            if line and not line.startswith('TARGET'):
                mnt_points.append(line.split()[0])
        if len(mnt_points) > 1:
            log('Device {} mounted in multiple times, ignoring'.format(device))
        else:
            mnt_point = mnt_points[0]
    except CalledProcessError:
        # findmnt returns non-zero rc if dev not mounted
        pass
    return mnt_point


def find_block_devices(include_mounted=False):
    found = []
    incl = ['sd[a-z]', 'vd[a-z]', 'cciss\/c[0-9]d[0-9]']

    with open('/proc/partitions') as proc:
        partitions = [p.split() for p in proc.readlines()[2:]]
    for partition in [p[3] for p in partitions if p]:
        for inc in incl:
            _re = re.compile(r'^(%s)$' % inc)
            if _re.match(partition):
                found.append(os.path.join('/dev', partition))
    if include_mounted:
        devs = [f for f in found if is_block_device(f)]
    else:
        devs = [f for f in found if _is_storage_ready(f)]
    return devs


def guess_block_devices():
    bdevs = find_block_devices(include_mounted=True)
    gdevs = []
    for dev in bdevs:
        if is_device_mounted(dev):
            mnt_point = get_mount_point(dev)
            if mnt_point and mnt_point.startswith('/srv/node'):
                gdevs.append(dev)
        else:
            gdevs.append(dev)
    return gdevs


def determine_block_devices():
    block_device = config('block-device')
    if not block_device or block_device.lower() == 'none':
        log("No storage devices specified in 'block_device' config",
            level=ERROR)
        return None

    if block_device == 'guess':
        bdevs = guess_block_devices()
    else:
        bdevs = block_device.split(' ')

    bdevs = list(set(bdevs))
    # attempt to ensure block devices, but filter out missing devs
    _none = ['None', 'none']
    valid_bdevs = \
        [x for x in map(ensure_block_device, bdevs) if str(x).lower() not in
         _none]
    log('Valid ensured block devices: %s' % valid_bdevs)
    return valid_bdevs


def mkfs_xfs(bdev, force=False):
    """Format device with XFS filesystem.

    By default this should fail if the device already has a filesystem on it.
    """
    cmd = ['mkfs.xfs']
    if force:
        cmd.append("-f")

    cmd += ['-i', 'size=1024', bdev]
    check_call(cmd)


def devstore_safe_load(devstore):
    """Attempt to decode json data and return None if an error occurs while
    also printing a log.
    """
    if not devstore:
        return None

    try:
        return json.loads(devstore)
    except ValueError:
        log("Unable to decode JSON devstore", level=DEBUG)

    return None


def is_device_in_ring(dev, skip_rel_check=False, ignore_deactivated=True):
    """Check if device has been added to the ring.

    First check local KV store then check storage rel with proxy.
    """
    d = os.path.dirname(KV_DB_PATH)
    if not os.path.isdir(d):
        mkdir(d)
        log("Device '%s' does not appear to be in use by Swift" % (dev),
            level=INFO)
        return False

    # First check local KV store
    kvstore = KVStore(KV_DB_PATH)
    devstore = devstore_safe_load(kvstore.get(key='devices'))
    kvstore.close()
    deactivated = []
    if devstore:
        blk_uuid = get_device_blkid("/dev/%s" % (dev))
        env_uuid = os.environ.get('JUJU_ENV_UUID',
                                  os.environ.get('JUJU_MODEL_UUID'))
        masterkey = "%s@%s" % (dev, env_uuid)
        if (masterkey in devstore and
                devstore[masterkey].get('blkid') == blk_uuid and
                devstore[masterkey].get('status') == 'active'):
            log("Device '%s' appears to be in use by Swift (found in local "
                "devstore)" % (dev), level=INFO)
            return True

        for key, val in devstore.iteritems():
            if key != masterkey and val.get('blkid') == blk_uuid:
                log("Device '%s' appears to be in use by Swift (found in "
                    "local devstore) but has a different "
                    "JUJU_[ENV|MODEL]_UUID (current=%s, expected=%s). "
                    "This could indicate that the device was added as part of "
                    "a previous deployment and will require manual removal or "
                    "updating if it needs to be reformatted."
                    % (dev, key, masterkey), level=INFO)
                return True

        if ignore_deactivated:
            deactivated = [k == masterkey and v.get('blkid') == blk_uuid and
                           v.get('status') != 'active'
                           for k, v in devstore.iteritems()]

    if skip_rel_check:
        log("Device '%s' does not appear to be in use by swift (searched "
            "local devstore only)" % (dev), level=INFO)
        return False

    # Then check swift-storage relation with proxy
    for rid in relation_ids('swift-storage'):
        devstore = relation_get(attribute='device', rid=rid, unit=local_unit())
        if devstore and dev in devstore.split(':'):
            if not ignore_deactivated or dev not in deactivated:
                log("Device '%s' appears to be in use by swift (found on "
                    "proxy relation) but was not found in local devstore so "
                    "will be added to the cache" % (dev), level=INFO)
                remember_devices([dev])
                return True

    log("Device '%s' does not appear to be in use by swift (searched local "
        "devstore and proxy relation)" % (dev), level=INFO)
    return False


def get_device_blkid(dev):
    """Try to get the fs uuid of the provided device.

    If this is called for a new unformatted device we expect blkid to fail
    hence return None to indicate the device is not in use.

    :param dev: block device path
    :returns: UUID of device if found else None
    """
    try:
        blk_uuid = subprocess.check_output(['blkid', '-s', 'UUID', dev])
    except CalledProcessError:
        # If the device has not be used or formatted yet we expect this to
        # fail.
        return None

    blk_uuid = re.match(r'^%s:\s+UUID="(.+)"$' % (dev), blk_uuid.strip())
    if blk_uuid:
        return blk_uuid.group(1)
    else:
        log("Failed to obtain device UUID for device '%s' - returning None" %
            dev, level=WARNING)

    return None


def remember_devices(devs):
    """Add device to local store of ringed devices."""
    d = os.path.dirname(KV_DB_PATH)
    if not os.path.isdir(d):
        mkdir(d)

    kvstore = KVStore(KV_DB_PATH)
    devstore = devstore_safe_load(kvstore.get(key='devices')) or {}
    env_uuid = os.environ.get('JUJU_ENV_UUID',
                              os.environ.get('JUJU_MODEL_UUID'))
    for dev in devs:
        blk_uuid = get_device_blkid("/dev/%s" % (dev))
        key = "%s@%s" % (dev, env_uuid)
        if key in devstore and devstore[key].get('blkid') == blk_uuid:
            log("Device '%s' already in devstore (status:%s)" %
                (dev, devstore[key].get('status')), level=DEBUG)
        else:
            existing = [(k, v) for k, v in devstore.iteritems()
                        if v.get('blkid') == blk_uuid and
                        re.match("^(.+)@(.+)$", k).group(1) == dev]
            if existing:
                log("Device '%s' already in devstore but has a different "
                    "JUJU_[ENV|MODEL]_UUID (%s)" %
                    (dev, re.match(".+@(.+)$", existing[0][0]).group(1)),
                    level=WARNING)
            else:
                log("Adding device '%s' with blkid='%s' to devstore" %
                    (dev, blk_uuid),
                    level=DEBUG)
                devstore[key] = {'blkid': blk_uuid, 'status': 'active'}

    if devstore:
        kvstore.set(key='devices', value=json.dumps(devstore))

    kvstore.flush()
    kvstore.close()


def ensure_devs_tracked():
    for rid in relation_ids('swift-storage'):
        devs = relation_get(attribute='device', rid=rid, unit=local_unit())
        if devs:
            for dev in devs.split(':'):
                # this will migrate if not already in the local store
                is_device_in_ring(dev, skip_rel_check=True)


def setup_storage():
    # Ensure /srv/node exists just in case no disks
    # are detected and used.
    mkdir(os.path.join('/srv', 'node'),
          owner='swift', group='swift',
          perms=0o755)
    reformat = str(config('overwrite')).lower() == "true"
    for dev in determine_block_devices():
        if is_device_in_ring(os.path.basename(dev)):
            log("Device '%s' already in the ring - ignoring" % (dev))
            continue

        if reformat:
            clean_storage(dev)

        try:
            # If not cleaned and in use, mkfs should fail.
            mkfs_xfs(dev, force=reformat)
        except subprocess.CalledProcessError as exc:
            # This is expected is a formatted device is provided and we are
            # forcing the format.
            log("Format device '%s' failed (%s) - continuing to next device" %
                (dev, exc), level=WARNING)
            continue

        basename = os.path.basename(dev)
        _mp = os.path.join('/srv', 'node', basename)
        mkdir(_mp, owner='swift', group='swift')

        options = None
        loopback_device = is_mapped_loopback_device(dev)
        mountpoint = '/srv/node/%s' % basename
        if loopback_device:
            # If an exiting fstab entry exists using the image file as the
            # source then preserve it, otherwise use the loopback device
            # directly to avoid a secound implicit loopback device being
            # created on mount. Bug #1762390
            fstab = charmhelpers.core.fstab.Fstab()
            fstab_entry = fstab.get_entry_by_attr('mountpoint', mountpoint)
            if fstab_entry and loopback_device == fstab_entry.device:
                dev = loopback_device
            options = "loop,defaults"

        filesystem = "xfs"

        mount(dev, mountpoint, filesystem=filesystem)
        fstab_add(dev, mountpoint, filesystem, options=options)

        check_call(['chown', '-R', 'swift:swift', mountpoint])
        check_call(['chmod', '-R', '0755', mountpoint])


@retry_on_exception(3, base_delay=2, exc_type=CalledProcessError)
def fetch_swift_rings(rings_url):
    """Fetch rings from leader proxy unit.

    Note that we support a number of retries if a fetch fails since we may
    have hit the very small update window on the proxy side.
    """
    log('Fetching swift rings from proxy @ %s.' % rings_url, level=INFO)
    target = SWIFT_CONF_DIR
    tmpdir = tempfile.mkdtemp(prefix='swiftrings')
    try:
        synced = []
        for server in ['account', 'object', 'container']:
            url = '%s/%s.%s' % (rings_url, server, SWIFT_RING_EXT)
            log('Fetching %s.' % url, level=DEBUG)
            ring = '%s.%s' % (server, SWIFT_RING_EXT)
            cmd = ['wget', url, '--retry-connrefused', '-t', '10', '-O',
                   os.path.join(tmpdir, ring)]
            check_call(cmd)
            synced.append(ring)

        # Once all have been successfully downloaded, move them to actual
        # location.
        for f in synced:
            os.rename(os.path.join(tmpdir, f), os.path.join(target, f))
    finally:
        shutil.rmtree(tmpdir)


def save_script_rc():
    env_vars = {}
    ip = unit_private_ip()
    for server in ['account', 'container', 'object']:
        port = config('%s-server-port' % server)
        url = 'http://%s:%s/recon/diskusage|"mounted":true' % (ip, port)
        svc = server.upper()
        env_vars.update({
            'OPENSTACK_PORT_%s' % svc: port,
            'OPENSTACK_SWIFT_SERVICE_%s' % svc: '%s-server' % server,
            'OPENSTACK_URL_%s' % svc: url,
        })
    _save_script_rc(**env_vars)


def assert_charm_supports_ipv6():
    """Check whether we are able to support charms ipv6."""
    _release = lsb_release()['DISTRIB_CODENAME'].lower()
    if CompareHostReleases(_release) < "trusty":
        raise Exception("IPv6 is not supported in the charms for Ubuntu "
                        "versions less than Trusty 14.04")


def concat_rsync_fragments():
    log('Concatenating rsyncd.d fragments')
    rsyncd_dir = '/etc/rsyncd.d'
    rsyncd_conf = ""
    for filename in sorted(os.listdir(rsyncd_dir)):
        with open(os.path.join(rsyncd_dir, filename), 'r') as fragment:
            rsyncd_conf += fragment.read()
    with open('/etc/rsyncd.conf', 'w') as f:
        f.write(rsyncd_conf)


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

    f = open('/etc/rsyncd.conf', 'w')
    f.write(rsyncd_base)
    f.close()


def assess_status(configs):
    """Assess status of current unit"""
    if is_paused():
        return ("maintenance",
                "Paused. Use 'resume' action to resume normal service.")
    else:
        return ("active", "Unit is ready")


def grant_access(address, port):
    """Grant TCP access to address and port via UFW

    :side effect: calls ufw.grant_access
    :return: None
    """
    log('granting access: {}:{}'.format(address, port), level='DEBUG')
    ufw.grant_access(address, port=str(port), proto='tcp',
                     index=FIRST)


def revoke_access(address, port):
    """Revoke TCP access to address and port via UFW

    :side effect: calls ufw.revoke_access
    :return: None
    """
    log('revoking access: {}'.format(address), level='DEBUG')
    ufw.revoke_access(address, port=port, proto='tcp')


def setup_ufw():
    """Setup UFW firewall to ensure only swift-storage clients and storage
    peers have access to the swift daemons.

    :side effect: calls several external functions
    :return: None
    """
    ports = [config('object-server-port'),
             config('container-server-port'),
             config('account-server-port')]

    # Storage peers
    allowed_hosts = RsyncContext()().get('allowed_hosts', '').split(' ')

    # Storage clients (swift-proxy)
    allowed_hosts += [get_host_ip(ingress_address(rid=u.rid, unit=u.unit))
                      for u in iter_units_for_relation_name('swift-storage')]

    # Grant access for peers and clients
    for host in allowed_hosts:
        for port in ports:
            grant_access(host, port)

    # Default deny for storage ports
    for port in ports:
        ufw.modify_access(src=None, dst='any', port=port,
                          proto='tcp', action='reject')
