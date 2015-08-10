import argparse
import tempfile
import unittest

import mock
import yaml

from test_utils import CharmTestCase

import actions.actions


class PauseTestCase(CharmTestCase):

    def setUp(self):
        super(PauseTestCase, self).setUp(actions.actions, ["service_pause"])

    def test_pauses_services(self):
        """Pause action pauses all of the Swift services."""
        pause_calls = []

        def fake_service_pause(svc):
            pause_calls.append(svc)
            return True

        self.service_pause.side_effect = fake_service_pause
        actions.actions.pause([])
        self.assertEqual(pause_calls, ['swift-account-auditor',
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
                                       'swift-object-updater'])

    def test_bails_out_early_on_error(self):
        """Pause action fails early if there are errors stopping a service."""
        pause_calls = []

        def maybe_kill(svc):
            if svc == "swift-container-auditor":
                return False
            else:
                pause_calls.append(svc)
                return True

        self.service_pause.side_effect = maybe_kill
        self.assertRaisesRegexp(
            Exception, "swift-container-auditor didn't stop cleanly.",
            actions.actions.pause, [])
        self.assertEqual(pause_calls, ['swift-account-auditor',
                                       'swift-account-reaper',
                                       'swift-account-replicator',
                                       'swift-account-server'])


class GetActionParserTestCase(unittest.TestCase):

    def test_definition_from_yaml(self):
        """ArgumentParser is seeded from actions.yaml."""
        actions_yaml = tempfile.NamedTemporaryFile(
            prefix="GetActionParserTestCase", suffix="yaml")
        actions_yaml.write(yaml.dump({"foo": {"description": "Foo is bar"}}))
        actions_yaml.seek(0)
        parser = actions.actions.get_action_parser(actions_yaml.name, "foo")
        self.assertEqual(parser.description, 'Foo is bar')


class MainTestCase(CharmTestCase):

    def setUp(self):
        super(MainTestCase, self).setUp(
            actions.actions, ["_get_action_name", "get_action_parser"])

    def test_invokes_pause(self):
        dummy_calls = []

        def dummy_action(args):
            dummy_calls.append(True)

        self._get_action_name.side_effect = lambda: "foo"
        self.get_action_parser = lambda: argparse.ArgumentParser()
        with mock.patch.dict(actions.actions.ACTIONS, {"foo": dummy_action}):
            actions.actions.main([])
        self.assertEqual(dummy_calls, [True])
