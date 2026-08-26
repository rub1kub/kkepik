import unittest
from unittest.mock import Mock, patch

import app as app_module


class SecretConfigurationTest(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()

    def test_admin_logins_are_disabled_without_passwords(self):
        with (
            patch.object(app_module, 'ADMIN_PASSWORD', ''),
            patch.object(app_module, 'VPN_ADMIN_PASSWORD', ''),
        ):
            admin = self.client.post('/admin/login', data={'password': 'attempt'})
            vpn = self.client.post('/vpn/admin/login', data={'password': 'attempt'})

        self.assertEqual(admin.status_code, 200)
        self.assertEqual(vpn.status_code, 200)
        with self.client.session_transaction() as session:
            self.assertNotIn('superadmin_authenticated', session)
            self.assertNotIn('admin_authenticated', session)

    def test_ssh_connection_uses_environment_configuration(self):
        client = Mock()
        with (
            patch.object(app_module, 'VPN_SSH_HOST', 'vpn.example.test'),
            patch.object(app_module, 'VPN_SSH_USERNAME', 'service-user'),
            patch.object(app_module, 'VPN_SSH_PASSWORD', 'pw'),
        ):
            app_module.connect_vpn_ssh(client)

        client.connect.assert_called_once_with(
            'vpn.example.test',
            username='service-user',
            password='pw',  # pragma: allowlist secret
            timeout=20,
            allow_agent=False,
            look_for_keys=False,
        )


if __name__ == '__main__':
    unittest.main()
