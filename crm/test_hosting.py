"""Przygotowanie pod serwer firmy: czysty start bez danych demo, pobieranie aplikacji Solid Dialer."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from .roles import MANAGER


class StartFirmTests(TestCase):
    def test_creates_manager_with_typed_password(self):
        with mock.patch("getpass.getpass", side_effect=["Mocne-Haslo-2026", "Mocne-Haslo-2026"]):
            call_command("start_firm", username="szef", name="Jan Kowalski", stdout=mock.MagicMock())
        user = get_user_model().objects.get(username="szef")
        self.assertTrue(user.check_password("Mocne-Haslo-2026"))
        self.assertTrue(user.groups.filter(name=MANAGER).exists())
        self.assertEqual((user.first_name, user.last_name), ("Jan", "Kowalski"))

    def test_rejects_weak_password(self):
        with mock.patch("getpass.getpass", side_effect=["123", "123"] * 3):
            with self.assertRaises(CommandError):
                call_command("start_firm", username="szef", name="-", stdout=mock.MagicMock(), stderr=mock.MagicMock())
        self.assertFalse(get_user_model().objects.filter(username="szef").exists())


@override_settings(IS_SERVER=True)
class ServerModeTests(TestCase):
    def test_demo_data_is_blocked_on_server(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo", stdout=mock.MagicMock())
        with self.assertRaises(CommandError):
            call_command("setup_roles", demo=True, stdout=mock.MagicMock())
        self.assertFalse(get_user_model().objects.filter(username="agent1").exists())

    def test_login_page_hides_demo_passwords_on_server(self):
        self.assertNotContains(self.client.get("/crm/login/"), "demo12345")


class LoginDemoHintTests(TestCase):
    def test_login_page_shows_demo_hint_locally(self):
        self.assertContains(self.client.get("/crm/login/"), "Konta demo")


class LoginThrottleTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def tearDown(self):
        from django.core.cache import cache
        cache.clear()   # inne testy logują się z tego samego „adresu”

    def test_password_guessing_is_blocked_after_10_tries(self):
        from rest_framework.throttling import ScopedRateThrottle
        with mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"login": "10/min"}):
            self._guess()

    def _guess(self):
        codes = [self.client.post("/api/token/", {"username": "x", "password": f"zle{i}"}).status_code for i in range(11)]
        self.assertEqual(codes[:10], [401] * 10)
        self.assertEqual(codes[10], 429)


class DialerApkTests(TestCase):
    def test_missing_apk_says_so(self):
        with override_settings(BASE_DIR=__import__("pathlib").Path("/nie-ma-takiego-folderu")):
            r = self.client.get("/crm/dialer.apk")
        self.assertEqual(r.status_code, 404)
        self.assertIn("Solid Dialer", r.content.decode())

    def test_apk_is_downloaded(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "downloads").mkdir()
            (Path(tmp) / "downloads" / "solid-dialer.apk").write_bytes(b"PK-apk")
            with override_settings(BASE_DIR=Path(tmp)):
                r = self.client.get("/crm/dialer.apk")
                body = b"".join(r.streaming_content)
                r.close()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/vnd.android.package-archive")
        self.assertEqual(body, b"PK-apk")
