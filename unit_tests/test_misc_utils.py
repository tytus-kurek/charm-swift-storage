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
