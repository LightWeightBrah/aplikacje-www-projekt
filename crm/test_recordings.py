"""Nagrywanie rozmów: opcjonalne (domyślnie wyłączone), nagrania z dialera idą na Dysk Google, słucha manager.

Google jest tu udawany (FakeGoogle podmienia crm.recordings._http), więc testy nie łączą się z internetem."""

import json
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from . import recordings
from .models import CallRecording, CallRequest, Company, DialerDevice, RecordingConfig
from .tests import CrmTestBase


class FakeGoogle:
    """Udaje serwery Google: logowanie, Dysk (foldery, pliki) i cofanie dostępu."""

    def __init__(self):
        self.files = {}      # id → bajty / "folder"
        self.names = {}      # id → nazwa
        self.parents = {}    # id → id folderu, w którym leży
        self.revoked = []
        self.n = 0

    def _id(self):
        self.n += 1
        return f"f{self.n}"

    def __call__(self, method, url, data=None, headers=None, timeout=30):
        ok = lambda body: (200, json.dumps(body).encode())   # noqa: E731
        if url.startswith(recordings.TOKEN_URL):
            form = parse_qs(data.decode())
            if form["grant_type"] == ["authorization_code"]:
                return ok({"access_token": "a1", "refresh_token": "r1", "expires_in": 3600,
                           "scope": "openid https://www.googleapis.com/auth/drive.file email"})
            return ok({"access_token": "a2", "expires_in": 3600})
        if url.startswith(recordings.REVOKE_URL):
            self.revoked.append(parse_qs(data.decode())["token"][0])
            return 200, b""
        if url.startswith(recordings.USERINFO_URL):
            return ok({"email": "nagrania@example.com"})
        if url.startswith(recordings.UPLOAD_URL):
            new = self._id()
            meta = json.loads(data.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])
            self.names[new] = meta["name"]
            self.parents[new] = meta["parents"][0]
            self.files[new] = data.split(b"\r\n\r\n")[2].rsplit(b"\r\n--", 1)[0]
            return ok({"id": new})
        if url.startswith(recordings.FILES_URL):
            path = urlparse(url).path.rsplit("/", 1)[-1]
            if method == "POST":
                new = self._id()
                self.files[new] = "folder"
                meta = json.loads(data)
                self.names[new] = meta["name"]
                self.parents[new] = (meta.get("parents") or [None])[0]
                return ok({"id": new})
            if method == "DELETE":
                return (204, b"") if self.files.pop(path, None) is not None else (404, b"")
            if path == "files":                       # wyszukiwanie podfolderu: name = '…' and '<rodzic>' in parents
                q = parse_qs(urlparse(url).query)["q"][0]
                name = q.split("name = '", 1)[1].split("' and '", 1)[0].replace("\\'", "'")
                parent = q.split("' and '", 1)[1].split("' in parents", 1)[0]
                found = [{"id": i} for i, v in self.files.items()
                         if v == "folder" and self.names[i] == name and self.parents.get(i) == parent]
                return ok({"files": found})
            if path not in self.files:
                return 404, b"{}"
            if "alt=media" in url:
                return 200, self.files[path]
            return ok({"id": path, "trashed": False})
        raise AssertionError(f"Nieoczekiwane zapytanie: {method} {url}")


@override_settings(GOOGLE_CLIENT_ID="client-id", GOOGLE_CLIENT_SECRET="client-secret")
class RecordingTests(CrmTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.google = FakeGoogle()
        p = patch("crm.recordings._http", self.google)
        p.start()
        self.addCleanup(p.stop)
        self.token = DialerDevice.objects.create(user=self.agent1, name="Samsung", token="r" * 64).token
        self.lead = self.make_lead(owner=self.agent1, company=Company.objects.create(name="Omega Logistyka"), phone="555300001")

    def connect_drive(self):
        url = self.api(self.manager).post("/api/recordings/google/connect/").json()["url"]
        state = parse_qs(urlparse(url).query)["state"][0]
        return self.client.get("/crm/recordings/google/callback/", {"state": state, "code": "kod"})

    def enable(self):
        self.connect_drive()
        self.api(self.manager).patch("/api/recordings/settings/", {"enabled": True}, format="json")

    def call(self, **kwargs):
        data = dict(user=self.agent1, lead=self.lead, phone="555300001", state=CallRequest.State.DONE,
                    answered=True, duration_seconds=95, started_at=timezone.now())
        data.update(kwargs)
        return CallRequest.objects.create(**data)

    def upload(self, call, body=b"OggS-nagranie", duration=95):
        return self.client.post(f"/api/dialer/calls/{call.pk}/recording/?duration={duration}", body,
                                content_type="audio/ogg", HTTP_X_DIALER_TOKEN=self.token)

    # Domyślnie nic nie jest nagrywane: telefon dostaje record=false, a przysłane nagranie jest odrzucane
    def test_off_by_default(self):
        CallRequest.objects.create(user=self.agent1, lead=self.lead, phone="555300001")
        job = self.client.get("/api/dialer/poll/", HTTP_X_DIALER_TOKEN=self.token).json()
        self.assertIs(job["record"], False)
        self.assertEqual(self.upload(self.call()).status_code, 409)
        self.assertFalse(CallRecording.objects.exists())
        s = self.api(self.manager).get("/api/recordings/settings/").json()
        self.assertEqual((s["enabled"], s["drive_connected"], s["active"]), (False, False, False))

    # Włączenie bez połączonego Dysku nie uruchamia nagrywania
    def test_enabled_without_drive_is_not_active(self):
        s = self.api(self.manager).patch("/api/recordings/settings/", {"enabled": True}, format="json").json()
        self.assertTrue(s["enabled"])
        self.assertFalse(s["active"])
        self.assertEqual(self.upload(self.call()).status_code, 409)

    # Ustawienia, lista i odsłuch nagrań są tylko dla managera
    def test_only_manager(self):
        agent = self.api(self.agent1)
        for url in ("/api/recordings/settings/", "/api/recordings/", "/api/recordings/1/audio/"):
            self.assertEqual(agent.get(url).status_code, 403, url)
        self.assertEqual(agent.post("/api/recordings/google/connect/").status_code, 403)

    # Bez danych Google w .env przycisk łączenia mówi, czego brakuje
    @override_settings(GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="")
    def test_connect_needs_google_keys(self):
        r = self.api(self.manager).post("/api/recordings/google/connect/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("GOOGLE_CLIENT_ID", r.json()["detail"])
        self.assertFalse(self.api(self.manager).get("/api/recordings/settings/").json()["oauth_configured"])

    # Łączenie Dysku: strona zgody Google (tylko zakres drive.file), powrót, zapisany token, folder na nagrania
    def test_connect_drive(self):
        url = self.api(self.manager).post("/api/recordings/google/connect/").json()["url"]
        q = parse_qs(urlparse(url).query)
        self.assertEqual(q["client_id"], ["client-id"])
        self.assertIn("drive.file", q["scope"][0])
        self.assertNotIn("auth/drive ", q["scope"][0] + " ")   # nie prosimy o dostęp do całego Dysku
        state = q["state"][0]
        r = self.client.get("/crm/recordings/google/callback/", {"state": state, "code": "kod"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("drive=ok", r["Location"])
        config = RecordingConfig.get()
        self.assertEqual(config.google_refresh_token, "r1")
        self.assertEqual(config.google_email, "nagrania@example.com")
        self.assertEqual(self.google.names[config.folder_id], recordings.ROOT_FOLDER_NAME)

    # Podrobiony albo cudzy link powrotu nic nie zapisuje
    def test_callback_rejects_bad_state(self):
        r = self.client.get("/crm/recordings/google/callback/", {"state": "zmyslone", "code": "kod"})
        self.assertIn("drive=error", r["Location"])
        agent_state = recordings.signing.dumps({"u": self.agent1.pk}, salt=recordings.STATE_SALT)
        r = self.client.get("/crm/recordings/google/callback/", {"state": agent_state, "code": "kod"})
        self.assertIn("drive=error", r["Location"])
        self.assertFalse(RecordingConfig.get().drive_connected)

    # Gdy manager odmówi zgody u Google, wraca do Ustawień z komunikatem
    def test_callback_denied(self):
        r = self.client.get("/crm/recordings/google/callback/", {"error": "access_denied"})
        self.assertIn("drive=error", r["Location"])

    # Włączone: telefon dostaje record=true, nagranie trafia na Dysk (folder miesiąca), drugi raz się nie dubluje
    def test_upload_goes_to_drive(self):
        self.enable()
        CallRequest.objects.create(user=self.agent1, lead=self.lead, phone="555300001")
        job = self.client.get("/api/dialer/poll/?rec=1&v=1.2", HTTP_X_DIALER_TOKEN=self.token).json()
        self.assertIs(job["record"], True)
        device = DialerDevice.objects.get(token=self.token)
        self.assertEqual((device.can_record, device.app_version), (True, "1.2"))

        call = self.call()
        r = self.upload(call)
        self.assertEqual(r.status_code, 201, r.content)
        rec = CallRecording.objects.get()
        self.assertEqual((rec.lead, rec.agent, rec.duration_seconds, rec.size_bytes), (self.lead, self.agent1, 95, 13))
        self.assertEqual(self.google.files[rec.drive_file_id], b"OggS-nagranie")
        name = self.google.names[rec.drive_file_id]
        self.assertTrue(name.endswith("Omega Logistyka 555300001.ogg"), name)
        self.assertIn(timezone.localtime(call.started_at).strftime("%Y-%m"), self.google.names.values())
        self.assertEqual(self.upload(call).status_code, 201)
        self.assertEqual(CallRecording.objects.count(), 1)

    def path_of(self, file_id):
        """Ścieżka pliku na udawanym Dysku, od folderu głównego: [„Solid CRM – nagrania”, agent, miesiąc]."""
        parts, node = [], self.google.parents.get(file_id)
        while node:
            parts.insert(0, self.google.names[node])
            node = self.google.parents.get(node)
        return parts

    # Każdy agent ma swój folder, a w nim foldery miesięcy; ten sam folder jest używany ponownie
    def test_folders_per_agent_and_month(self):
        self.enable()
        self.agent1.first_name, self.agent1.last_name = "Jan", "Kowalski"
        self.agent1.save()
        when = timezone.now()
        month = timezone.localtime(when).strftime("%Y-%m")
        self.upload(self.call(started_at=when))
        self.upload(self.call(started_at=when))
        token2 = DialerDevice.objects.create(user=self.agent2, name="Xiaomi", token="s" * 64).token
        call2 = self.call(user=self.agent2, started_at=when)
        self.client.post(f"/api/dialer/calls/{call2.pk}/recording/", b"OggS-2", content_type="audio/ogg",
                         HTTP_X_DIALER_TOKEN=token2)
        recs = list(CallRecording.objects.order_by("pk"))
        self.assertEqual(len(recs), 3)
        paths = [self.path_of(r.drive_file_id) for r in recs]
        self.assertEqual(paths[0], [recordings.ROOT_FOLDER_NAME, "Jan Kowalski (agent1)", month])
        self.assertEqual(paths[1], paths[0])
        self.assertEqual(paths[2], [recordings.ROOT_FOLDER_NAME, "agent2", month])
        self.assertEqual(self.google.parents[recs[0].drive_file_id], self.google.parents[recs[1].drive_file_id])
        folders = [n for i, n in self.google.names.items() if self.google.files[i] == "folder"]
        self.assertEqual(folders.count("Jan Kowalski (agent1)"), 1)

    # Cudze połączenie, pusty plik i nieznany telefon są odrzucane
    def test_upload_rejects_bad_requests(self):
        self.enable()
        other = self.call(user=self.agent2)
        self.assertEqual(self.upload(other).status_code, 404)
        self.assertEqual(self.upload(self.call(), body=b"").status_code, 400)
        r = self.client.post(f"/api/dialer/calls/{other.pk}/recording/", b"x", content_type="audio/ogg",
                             HTTP_X_DIALER_TOKEN="zly")
        self.assertEqual(r.status_code, 401)

    # Manager widzi nagrania leada i odsłuchuje plik (CRM pobiera go z Dysku)
    def test_manager_lists_and_plays(self):
        self.enable()
        self.upload(self.call())
        rows = self.api(self.manager).get(f"/api/recordings/?lead={self.lead.pk}").json()["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["agent_name"], "agent1")
        r = self.api(self.manager).get(f"/api/recordings/{rows[0]['id']}/audio/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "audio/ogg")
        self.assertEqual(r.content, b"OggS-nagranie")

    # Nagrania starsze niż ustawiona liczba dni znikają z Dysku i z CRM
    def test_old_recordings_are_removed(self):
        self.enable()
        self.upload(self.call())
        old = CallRecording.objects.get()
        CallRecording.objects.filter(pk=old.pk).update(started_at=timezone.now() - timedelta(days=91))
        self.upload(self.call(started_at=timezone.now()))   # nowe nagranie sprząta stare
        self.assertFalse(CallRecording.objects.filter(pk=old.pk).exists())
        self.assertNotIn(old.drive_file_id, self.google.files)
        self.assertEqual(CallRecording.objects.count(), 1)

    # Odłączenie Dysku: Google cofa dostęp, telefony przestają nagrywać
    def test_disconnect(self):
        self.enable()
        s = self.api(self.manager).post("/api/recordings/google/disconnect/").json()
        self.assertFalse(s["drive_connected"])
        self.assertEqual(self.google.revoked, ["r1"])
        CallRequest.objects.create(user=self.agent1, lead=self.lead, phone="555300001")
        job = self.client.get("/api/dialer/poll/", HTTP_X_DIALER_TOKEN=self.token).json()
        self.assertIs(job["record"], False)

    # Polityka prywatności (Google wymaga jej adresu) otwiera się bez logowania, też na serwerze z blokadą urządzeń
    @override_settings(DEVICE_LOCK=True)
    def test_privacy_page_is_public(self):
        r = self.client.get("/crm/privacy/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "drive.file")
        self.assertEqual(self.client.get("/crm/").status_code, 302)   # reszta CRM dalej zablokowana

    # Agent przy dzwonieniu widzi, że rozmowy są nagrywane (przypomnienie o poinformowaniu klienta)
    def test_dialer_status_shows_recording(self):
        self.assertFalse(self.api(self.agent1).get("/api/dialer/status/").json()["recording"])
        self.enable()
        self.client.get("/api/dialer/poll/?rec=0&v=1.2", HTTP_X_DIALER_TOKEN=self.token)
        d = self.api(self.agent1).get("/api/dialer/status/").json()
        self.assertEqual((d["recording"], d["can_record"]), (True, False))
