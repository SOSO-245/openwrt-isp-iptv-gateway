"""Exercise the shipped shell login branch using temporary dummy credentials."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode


SOURCE = Path(__file__).resolve().parents[1] / "openwrt/data/www/cgi-bin/iptv-action"


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.auth = root / "auth"
        self.auth.write_text("demo:sample +&%#\\ password\n")
        self.original = self.auth.read_bytes()
        source = SOURCE.read_text()
        helpers = source[source.index('qs="$QUERY_STRING"'):source.index('cfg_file=')]
        check = source[source.index('ensure_auth(){'):source.index('history_limit(){')]
        branch = source[source.index('  auth)\n'):source.index('  change_auth)\n')]
        self.script = root / "login.sh"
        self.script.write_text(helpers + '\nauth_file="$TEST_AUTH_FILE"\n' + check +
                               '\naction=$(get action)\ncase "$action" in\n' + branch + 'esac\n')

    def login(self, user, password, body=True):
        data = urlencode({"user": user, "pass": password})
        content = data.encode() if body else b""
        env = {**os.environ, "QUERY_STRING": "action=auth" + ("" if body else "&" + data),
               "CONTENT_LENGTH": str(len(content)), "TEST_AUTH_FILE": str(self.auth)}
        result = subprocess.run(["/bin/sh", str(self.script)], input=content, env=env,
                                capture_output=True, check=True, timeout=3)
        self.assertEqual(self.auth.read_bytes(), self.original)
        return result.stdout.decode().strip()

    def test_post_body_preserves_special_characters(self):
        self.assertEqual(self.login("demo", "sample +&%#\\ password"), "OK")

    def test_legacy_query_remains_compatible(self):
        self.assertEqual(self.login("demo", "sample +&%#\\ password", body=False), "OK")

    def test_wrong_or_empty_input_does_not_change_credentials(self):
        for user, password in [("demo", "wrong"), ("wrong", "sample +&%#\\ password"), ("", ""),
                               ("demo", "sample +&%#\\ password ")]:
            with self.subTest(user=user):
                self.assertEqual(self.login(user, password), "FAIL")


if __name__ == "__main__":
    unittest.main()
