#!/usr/bin/python

import argparse
import os
import sys
import yaml

from charmhelpers.core.host import service_pause
from charmhelpers.core.hookenv import action_fail, status_set

from lib.swift_storage_utils import SWIFT_SVCS


def get_action_parser(actions_yaml_path, action_name):
    """Make an argparse.ArgumentParser seeded from actions.yaml definitions."""
    with open(actions_yaml_path) as fh:
        doc = yaml.load(fh)[action_name]["description"]
    parser = argparse.ArgumentParser(description=doc)
    # TODO: Add arguments for params defined in the actions.yaml
    return parser


def pause(args):
    """Pause all the swift services.

    @raises Exception if any services fail to stop
    """
    for service in SWIFT_SVCS:
        stopped = service_pause(service)
        if not stopped:
            raise Exception("{} didn't stop cleanly.".format(service))
    status_set(
        "maintenance", "Paused. Use 'resume' action to resume normal service.")


# A dictionary of all the defined actions to callables (which take
# parsed arguments).
ACTIONS = {"pause": pause}


def main(argv):
    action_name = _get_action_name()
    actions_yaml_path = _get_actions_yaml_path()
    parser = get_action_parser(actions_yaml_path, action_name)
    args = parser.parse_args(argv)
    try:
        action = ACTIONS[action_name]
    except KeyError:
        return "Action %s undefined" % action_name
    else:
        try:
            action(args)
        except Exception as e:
            action_fail(str(e))


def _get_action_name():
    """Return the name of the action."""
    return os.path.basename(__file__)


def _get_actions_yaml_path():
    """Return the path to actions.yaml"""
    cwd = os.path.dirname(__file__)
    return os.path.join(cwd, "..", "actions.yaml")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
