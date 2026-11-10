"""Aktywacja urządzeń: obcy nie widzi nawet logowania, kod działa raz, manager blokuje urządzenia."""

from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .device_lock import COOKIE, new_code
from .models import AccessDevice, ActivationCode
from .roles import AGENT, MANAGER

FAST_HASHER = ["django.contrib.auth.hashers.MD5PasswordHasher"]   # testy bez wolnego hashowania haseł


@override_settings(DEVICE_LOCK=True, PASSWORD_HASHERS=FAST_HASHER)
class DeviceLockTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.manager = User.objects.create_user("szef", password="x")
        self.manager.groups.add(Group.objects.get_or_create(name=MANAGER)[0])
        self.agent = User.objects.create_user("agent", password="x")
        self.agent.groups.add(Group.objects.get_or_create(name=AGENT)[0])

    def activated_client(self, name="Laptop"):
        code, _ = new_code(self.manager)
        client = APIClient()
        r = client.post("/api/device/activate/", {"code": code, "name": name}, format="json")
        self.assertEqual(r.status_code, 204)
        self.assertIn(COOKIE, r.cookies)
        self.assertTrue(r.cookies[COOKIE]["httponly"])
        return client

    def test_stranger_sees_only_activation_screen(self):
        client = APIClient()
        for page in ("/crm/login/", "/crm/queue/", "/admin/", "/crm/leads/"):
            r = client.get(page)
            self.assertEqual((r.status_code, r["Location"]), (302, "/crm/activate/"), page)
        self.assertEqual(client.get("/api/docs/").status_code, 403)   # Swagger też schowany
        api = client.post("/api/token/", {"username": "szef", "password": "x"}, format="json")
        self.assertEqual(api.status_code, 403)
        self.assertEqual(api["X-Device-Lock"], "1")
        self.assertContains(client.get("/crm/activate/"), "To urządzenie nie ma dostępu")

    def test_activated_device_can_log_in(self):
        client = self.activated_client()
        self.assertEqual(client.get("/crm/login/").status_code, 200)
        r = client.post("/api/token/", {"username": "szef", "password": "x"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(client.get("/crm/activate/").status_code, 302)   # już aktywne → logowanie

    def test_code_works_once_and_only_24h(self):
        code, _ = new_code(self.manager)
        first = APIClient().post("/api/device/activate/", {"code": code.lower().replace("-", " "), "name": "A"}, format="json")
        self.assertEqual(first.status_code, 204)   # wielkość liter i odstępy bez znaczenia
        again = APIClient().post("/api/device/activate/", {"code": code, "name": "B"}, format="json")
        self.assertEqual(again.status_code, 400)
        old, _ = new_code(self.manager)
        ActivationCode.objects.filter(used_at__isnull=True).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(APIClient().post("/api/device/activate/", {"code": old, "name": "C"}, format="json").status_code, 400)
        self.assertEqual(AccessDevice.objects.count(), 1)
        self.assertFalse(AccessDevice.objects.filter(token_hash__contains=code.replace("-", "")).exists())

    def test_guessing_codes_is_throttled(self):
        from unittest import mock

        from rest_framework.throttling import ScopedRateThrottle
        with mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"device_activate": "10/min"}):
            codes = [APIClient().post("/api/device/activate/", {"code": f"AAAA-AAA{i}", "name": "x"}, format="json").status_code
                     for i in range(11)]
        self.assertEqual(codes[-1], 429)

    def test_manager_issues_codes_lists_and_revokes_devices(self):
        client = self.activated_client("Laptop szefa")
        client.force_authenticate(self.manager)
        code = client.post("/api/devices/code/").data["code"]
        phone = APIClient()
        phone.post("/api/device/activate/", {"code": code, "name": "Telefon Ani"}, format="json")
        listing = client.get("/api/devices/").data
        self.assertTrue(listing["device_lock"])
        names = {d["name"]: d for d in listing["results"]}
        self.assertTrue(names["Laptop szefa"]["this_device"])
        own = client.post(f"/api/devices/{names['Laptop szefa']['id']}/revoke/")
        self.assertEqual(own.status_code, 400)                                   # własnego nie zablokuje
        self.assertEqual(client.post(f"/api/devices/{names['Telefon Ani']['id']}/revoke/").status_code, 204)
        self.assertEqual(phone.get("/crm/login/").status_code, 302)               # zablokowany → ekran aktywacji

    def test_agent_cannot_issue_codes(self):
        client = self.activated_client()
        client.force_authenticate(self.agent)
        self.assertEqual(client.post("/api/devices/code/").status_code, 403)

    def test_dialer_app_and_apk_are_not_blocked(self):
        client = APIClient()
        self.assertNotEqual(client.post("/api/dialer/pair/", {"code": "000000"}, format="json").status_code, 302)
        self.assertNotIn("X-Device-Lock", client.get("/api/dialer/poll/"))
        self.assertNotEqual(client.get("/crm/dialer.apk").status_code, 302)

    def test_console_command_prints_a_working_code(self):
        out = StringIO()
        call_command("device_code", stdout=out)
        code = out.getvalue().split("Kod aktywacji: ")[1].split()[0]
        self.assertEqual(APIClient().post("/api/device/activate/", {"code": code, "name": "Pierwszy"}, format="json").status_code, 204)


class DeviceLockOffLocallyTests(TestCase):
    def test_lock_is_off_on_developer_computer(self):
        self.assertEqual(self.client.get("/crm/login/").status_code, 200)
