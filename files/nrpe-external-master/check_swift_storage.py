#!/usr/bin/env python

# Copyright (C) 2014, 2017 Canonical
# All Rights Reserved
# Author: Jacek Nykis

import sys
import json
import urllib2
import argparse
import hashlib
import datetime

STATUS_OK = 0
STATUS_WARN = 1
STATUS_CRIT = 2
STATUS_UNKNOWN = 3


def generate_md5(filename):
    with open(filename, 'rb') as f:
        md5 = hashlib.md5()
        buffer = f.read(2 ** 20)
        while buffer:
            md5.update(buffer)
            buffer = f.read(2 ** 20)
    return md5.hexdigest()


def check_md5(base_url):
    url = base_url + "ringmd5"
    ringfiles = ["/etc/swift/object.ring.gz",
                 "/etc/swift/account.ring.gz",
                 "/etc/swift/container.ring.gz"]
    results = []
    try:
        data = urllib2.urlopen(url).read()
        ringmd5_info = json.loads(data)
    except urllib2.URLError:
        return [(STATUS_UNKNOWN, "Can't open url: {}".format(url))]
    except ValueError:
        return [(STATUS_UNKNOWN, "Can't parse status data")]

    for ringfile in ringfiles:
        try:
            if generate_md5(ringfile) != ringmd5_info[ringfile]:
                results.append((STATUS_CRIT, "Ringfile {} MD5 sum "
                                "mismatch".format(ringfile)))
        except IOError:
            results.append(
                (STATUS_UNKNOWN, "Can't open ringfile {}".format(ringfile)))
    if results:
        return results
    else:
        return [(STATUS_OK, "OK")]


def repl_last_timestamp(repl_info):
    """Verifies that values are not null (float expected)
    @returns (timedelta, counter)

    ie. if /var/cache/swift/* has no swift uid/gid,
    syslog will show errors and all replication values will
    return None

    {
        "replication_last": None,
        "replication_stats": None,
        "object_replication_last": None,
        "replication_time": None,
        "object_replication_time": None,
    }
    """
    delta = None
    repl_last = (repl_info.get('replication_last') or
                 repl_info.get('object_replication_last'))

    if repl_last:
        delta = datetime.datetime.now() - \
            datetime.datetime.fromtimestamp(repl_last)

    if 'replication_stats' not in repl_info:
        # no info shared by swift; considered OK
        repl_failures = None
    else:
        # raise error (-1) if NULL value or 'failure' attr does not exist
        repl_failures = (repl_info.get('replication_stats', {})
                                  .get('failure', -1))

    return (delta, repl_failures)


def check_replication(base_url, limits):
    types = ["account", "object", "container"]
    results = []
    for repl in types:
        url = base_url + "replication/" + repl
        try:
            data = urllib2.urlopen(url).read()
            repl_info = json.loads(data)
        except urllib2.URLError:
            results.append((STATUS_UNKNOWN, "Can't open url: {}".format(url)))
            continue
        except ValueError:
            results.append((STATUS_UNKNOWN, "Can't parse status data"))
            continue

        delta, repl_failures = repl_last_timestamp(repl_info)

        if not delta:
            results.append((STATUS_CRIT,
                            "'{}' replication lag not working "
                            '(perms issue? check syslog)'.format(repl)))
        else:
            seconds_since_repl = delta.days * 86400
            seconds_since_repl += delta.seconds
            if seconds_since_repl >= limits[1]:
                results.append((STATUS_CRIT,
                                "'{}' replication lag is {} "
                                "seconds".format(repl, seconds_since_repl)))
            elif seconds_since_repl >= limits[0]:
                results.append((STATUS_WARN,
                                "'{}' replication lag is {} "
                                "seconds".format(repl, seconds_since_repl)))

        if not repl_failures:
            pass
        elif repl_failures >= limits[3]:
            results.append((STATUS_CRIT,
                            "{} replication failures".format(repl_failures)))
        elif repl_failures >= limits[2]:
            results.append((STATUS_WARN,
                            "{} replication failures".format(repl_failures)))
        elif repl_failures == -1 and delta:
            # XXX(aluria): only raised if repl lag err was not triggered
            results.append((STATUS_CRIT,
                            "replication failures counter is NULL "
                            "(check syslog)"))
    if results:
        return results
    else:
        return [(STATUS_OK, "OK")]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Check swift-storage health')
    parser.add_argument('-H', '--host', dest='host', default='localhost',
                        help='Hostname to query')
    parser.add_argument('-p', '--port', dest='port', default='6000', type=int,
                        help='Port number')
    parser.add_argument('-r', '--replication', dest='check_replication',
                        type=int, nargs=4, help='Check replication status',
                        metavar=('lag_warn', 'lag_crit', 'failures_warn',
                                 'failures_crit'))
    parser.add_argument('-m', '--md5', dest='check_md5', action='store_true',
                        help='Compare server rings md5sum with local copy')
    args = parser.parse_args()

    if not args.check_replication and not args.check_md5:
        print('You must use -r or -m switch')
        sys.exit(STATUS_UNKNOWN)

    base_url = "http://{}:{}/recon/".format(args.host, args.port)
    results = []
    if args.check_replication:
        results.extend(check_replication(base_url, args.check_replication))
    if args.check_md5:
        results.extend(check_md5(base_url))

    crits = ';'.join([i[1] for i in results if i[0] == STATUS_CRIT])
    warns = ';'.join([i[1] for i in results if i[0] == STATUS_WARN])
    unknowns = ';'.join([i[1] for i in results if i[0] == STATUS_UNKNOWN])
    if crits:
        print("CRITICAL: {}".format(crits))
        sys.exit(STATUS_CRIT)
    elif warns:
        print("WARNING: {}".format(warns))
        sys.exit(STATUS_WARN)
    elif unknowns:
        print("UNKNOWN: {}".format(unknowns))
        sys.exit(STATUS_UNKNOWN)
    else:
        print("OK")
        sys.exit(0)
