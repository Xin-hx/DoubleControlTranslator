import unittest
from unittest.mock import patch

from plugin.dashboard.plugin_api import _lifecycle_ws_allowed


class _Client:
    def __init__(self, host): self.host = host


class _Socket:
    def __init__(self, host): self.client = _Client(host)


class LifecycleWebSocketSecurityTests(unittest.TestCase):
    def test_remote_peer_rejected_even_if_authenticated(self):
        with patch("plugin.dashboard.plugin_api._hermes_ws_admitted", return_value=True):
            self.assertFalse(_lifecycle_ws_allowed(_Socket("192.0.2.40")))

    def test_non_ip_testclient_peer_rejected(self):
        with patch("plugin.dashboard.plugin_api._hermes_ws_admitted", return_value=True):
            self.assertFalse(_lifecycle_ws_allowed(_Socket("testclient")))

    def test_loopback_still_requires_hermes_auth_host_origin_checks(self):
        with patch("plugin.dashboard.plugin_api._hermes_ws_admitted", return_value=False):
            self.assertFalse(_lifecycle_ws_allowed(_Socket("127.0.0.1")))
        with patch("plugin.dashboard.plugin_api._hermes_ws_admitted", return_value=True):
            self.assertTrue(_lifecycle_ws_allowed(_Socket("::1")))


if __name__ == "__main__": unittest.main()
