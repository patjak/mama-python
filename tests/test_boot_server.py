import io
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from mama.boot import iPXEHandler


class DummyHandler(iPXEHandler):
    """Subclass iPXEHandler without starting a full socket server for unit testing."""
    def __init__(self):
        self.wfile = io.BytesIO()
        self.client_address = ("192.168.2.50", 12345)
        self.headers_sent = []
        self.response_status = None

    def send_response(self, code, message=None):
        self.response_status = code

    def send_header(self, keyword, value):
        self.headers_sent.append((keyword, value))

    def end_headers(self):
        pass

    def send_error(self, code, message=None):
        self.response_status = code

    def address_string(self):
        return "192.168.2.50"


class TestBootServer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_file = pathlib.Path(self.temp_dir.name) / "vmlinuz-test"
        self.test_file.write_bytes(b"KERNELDATA12345")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_send_file_success(self):
        handler = DummyHandler()
        success = handler._send_file(self.test_file)

        self.assertTrue(success)
        self.assertEqual(handler.response_status, 200)
        self.assertEqual(handler.wfile.getvalue(), b"KERNELDATA12345")

    def test_send_file_client_disconnect(self):
        handler = DummyHandler()
        # Simulate BrokenPipeError when writing to wfile
        handler.wfile = MagicMock()
        handler.wfile.write.side_effect = BrokenPipeError("Broken pipe")

        success = handler._send_file(self.test_file)

        self.assertFalse(success)
        # Verify it didn't try to send a 500 header to a broken socket
        self.assertNotEqual(handler.response_status, 500)


if __name__ == "__main__":
    unittest.main()
