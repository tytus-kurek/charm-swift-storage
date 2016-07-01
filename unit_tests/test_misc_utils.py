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
import tempfile
import unittest
import shutil

from mock import patch

from lib.misc_utils import ensure_block_device


class EnsureBlockDeviceTestCase(unittest.TestCase):

    @patch("lib.misc_utils.is_block_device")
    def test_symlinks_are_resolved(self, mock_function):
        """
        Ensure symlinks pointing to block devices are resolved when passed to
        ensure_block_device.
        """
        # Create a temporary symlink pointing to /dev/null
        temp_dir = tempfile.mkdtemp()
        link_path = os.path.join(temp_dir, "null_link")
        os.symlink("/dev/null", link_path)
        result = ensure_block_device(link_path)
        assert mock_function.called
        self.assertEqual("/dev/null", result)
        shutil.rmtree(temp_dir)
