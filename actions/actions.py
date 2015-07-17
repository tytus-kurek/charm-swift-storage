#!/usr/bin/python

import argparse
import os
import sys
import yaml


def get_action_parser(actions_yaml_path, action_name):
    with open(actions_yaml_path) as fh:
        doc = yaml.load(fh)[action_name]["description"]
    parser = argparse.ArgumentParser(doc)
    # TODO: Add arguments for params defined in the actions.yaml
    return parser


def pause(args):
    from swift_storage_utils import SWIFT_SVCS
    from charmhelpers.core.host import service_pause
    for service in SWIFT_SVCS:
        service_pause(service)


def main(argv):
    cwd, action_name = os.path.split(__file__)
    actions_yaml_path = os.path.join(cwd, "..", "actions.yaml")
    parser = get_action_parser(actions_yaml_path, action_name)
    args = parser.parse_args(argv)
    try:
        action = globals()[action_name]
    except AttributeError:
        return "Action %s undefined" % action_name
    else:
        return action(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
