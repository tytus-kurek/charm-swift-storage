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

import mock
import unittest

from jinja2 import Environment

from charmhelpers.contrib.openstack.templating import get_loader


class StorageServerTemplateTestCase(unittest.TestCase):

    @mock.patch('charmhelpers.contrib.openstack.templating.log')
    def get_template_for_release_and_server(
            self,
            os_release,
            server,
            suffix,
            mock_log):

        if not server:
            server = 'object'

        loader = get_loader('./templates', os_release)
        env = Environment(loader=loader)

        return env.get_template('etc_swift_{}-server_{}.conf'.format(server,
                                                                     suffix))

    def test_os_release_not_in_templates(self):
        """Regression test for bug 1251551.

        The os_release is no longer provided as context to the templates.
        """
        for release in ('essex', 'grizzly', 'havana', 'icehouse'):
            for server in ('object', 'container', 'account'):
                for suffix in ('1', '2'):
                    template = self.get_template_for_release_and_server(
                        release,
                        server,
                        suffix)
                    with open(template.filename, 'r') as template_orig:
                        self.assertNotIn(
                            'os_release', template_orig.read(),
                            "The template '{}' contains os_release which is "
                            "no longer provided in the context.".format(
                                template.filename))

    def test_config_renders_for_all_releases_and_servers(self):
        """The configs render without syntax error."""
        for release in ('essex', 'grizzly', 'havana', 'icehouse'):
            for server in ('object', 'container', 'account'):
                for suffix in ('1', '2'):
                    template = self.get_template_for_release_and_server(
                        release,
                        server,
                        suffix)

                    result = template.render()

                    self.assertTrue(result.startswith("[DEFAULT]"))

    def test_statsd_config_for_all_releases_and_servers(self):
        """The configs contain statsd settings if statsd-host is set."""
        for release in ('grizzly', 'havana', 'icehouse', 'mitaka'):
            for server in ('object', 'container', 'account'):
                for suffix in ('1', '2'):
                    template = self.get_template_for_release_and_server(
                        release,
                        server,
                        suffix)

                    result = template.render(statsd_host='127.0.0.1')

                    self.assertIn("log_statsd_host", result)
                    self.assertIn("log_statsd_port", result)
                    self.assertIn("log_statsd_default_sample_rate", result)

                    result = template.render()

                    self.assertNotIn("log_statsd_host", result)
                    self.assertNotIn("log_statsd_port", result)
                    self.assertNotIn("log_statsd_default_sample_rate", result)
