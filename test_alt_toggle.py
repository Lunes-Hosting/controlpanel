"""Isolated regression tests; no live database, config secrets or API required."""

import ast
from pathlib import Path
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parent


def load_functions(path, namespace):
    # Load production functions without initializing the application's services.
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    exec(compile(tree, path, "exec", flags=__import__('__future__').annotations.compiler_flag), namespace)
    return namespace


class AltToggleTests(unittest.TestCase):
    def test_config_values_and_disabled_default(self):
        config = Mock(spec=[])
        ns = load_functions("managers/alt_settings.py", {"config": config})
        self.assertFalse(ns['alt_detection_enabled']())
        for value, expected in [(False, False), ('false', False), ('0', False),
                                (True, True), ('true', True), ('1', True)]:
            config.ALT_DETECTION_ENABLED = value
            self.assertEqual(ns['alt_detection_enabled'](), expected)

    def test_disabled_login_and_ip_history_have_no_side_effects(self):
        database, log = Mock(), Mock()
        ns = load_functions("managers/alt_detection.py", {
            'alt_detection_enabled': lambda: False,
            'DatabaseManager': database, 'webhook_log': log,
            'ALT_ENFORCE': True,
        })
        self.assertEqual(ns['check_login_ip'](2, 'a@example.com', 'a', 'member', None, '8.8.8.8'),
                         {'allowed': True})
        ns['record_user_ip'](2, '8.8.8.8')
        database.execute_query.assert_not_called()
        log.assert_not_called()

    def test_disabled_device_and_fingerprint_checks_have_no_side_effects(self):
        database, response = Mock(), Mock()
        ns = load_functions("managers/signup_signals.py", {
            'alt_detection_enabled': lambda: False, 'DatabaseManager': database,
        })
        self.assertIsNone(ns['device_token']())
        self.assertIsNone(ns['collect_signals']({'signup_fingerprint': 'existing profile'}))
        self.assertEqual(ns['find_matches'](None), {'exact': None, 'profile': None})
        ns['record_signup'](2, None)
        self.assertIs(ns['set_device_cookie'](response), response)
        database.execute_query.assert_not_called()
        response.set_cookie.assert_not_called()

    def test_signup_shared_ip_is_only_blocked_when_enabled(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                database, requests, safe_requests = Mock(), Mock(), Mock()
                database.execute_query.return_value = (1,)
                safe_requests.get.return_value.text = ''
                requests.post.return_value.json.return_value = {'errors': [{'detail': 'API reached'}]}
                ns = load_functions('managers/authentication.py', {
                    'alt_detection_enabled': lambda: enabled, 'DatabaseManager': database,
                    'requests': requests, 'safe_requests': safe_requests, 'bcrypt': Mock(),
                    'ThreadWithReturnValue': Mock(), 'webhook_log': Mock(),
                    'PTERODACTYL_URL': 'https://example.com/', 'HEADERS': {},
                })
                result = ns['register']('a@example.com', 'password', 'a', '8.8.8.8')
                self.assertEqual(result, 'IP is already registered' if enabled else 'API reached')
                if enabled:
                    requests.post.assert_not_called()
                else:
                    database.execute_query.assert_not_called()
                    requests.post.assert_called_once()


if __name__ == '__main__':
    unittest.main()
