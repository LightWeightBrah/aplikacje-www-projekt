"""SMS z przypomnieniem przed umówionym telefonem (wysyła go aplikacja dialera z telefonu agenta)."""

from datetime import datetime, timedelta
from unittest.mock import patch

from django.utils import timezone

from .models import Activity, DialerDevice, Lead, SmsReminder
from .rules import get_rules
from .sms import render_sms, when_text
from .tests import CrmTestBase


TODAY = timezone.localdate()   # prawdziwe „dziś” (w testach zegar jest potem zamrożony na jutro 12:00)


def local(days=0, hour=12, minute=0):
    """Moment w czasie lokalnym (Europe/Warsaw): `days` dni od dziś o podanej godzinie."""
    base = TODAY + timedelta(days=days)
    return timezone.make_aware(datetime(base.year, base.month, base.day, hour, minute))


class SmsReminderTests(CrmTestBase):
    NOW = local(1, 12, 0)   # „teraz” w testach: jutro w południe (w godzinach wysyłki SMS-ów)

    def setUp(self):
        super().setUp()
        self.agent1.first_name = "Julian"
        self.agent1.save()
        self.clock = patch("django.utils.timezone.now", return_value=self.NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        # telefon sparowany od razu (parowanie kodem ma limit prób, a zegar w testach stoi)
        self.token = DialerDevice.objects.create(user=self.agent1, name="Xiaomi", token="t" * 64).token

    def promised(self, at, **kwargs):
        data = dict(owner=self.agent1, phone="600 100 200", status=Lead.Status.CALLBACK, knows_caller=True,
                    next_contact_at=at, agreed_contact_at=at, last_activity_at=self.NOW - timedelta(days=1))
        data.update(kwargs)
        return self.make_lead(**data)

    def poll(self, sms=True):
        return self.client.get("/api/dialer/poll/" + ("?sms=1" if sms else ""), HTTP_X_DIALER_TOKEN=self.token)

    def done(self, sms_id, ok=True, error=""):
        return self.client.post(f"/api/dialer/sms/{sms_id}/done/", {"ok": ok, "error": error},
                                content_type="application/json", HTTP_X_DIALER_TOKEN=self.token)

    # Godzinę przed umówionym telefonem telefon dostaje SMS do wysłania, jeden raz, a w historii zostaje wpis
    def test_phone_gets_reminder_once_and_history_shows_it(self):
        lead = self.promised(self.NOW + timedelta(minutes=50))
        r = self.poll()
        self.assertEqual(r.status_code, 200, r.content)
        job = r.json()
        self.assertEqual(job["type"], "sms")
        self.assertEqual(job["phone"], "600100200")
        self.assertIn("tu Julian", job["text"])
        self.assertIn("Dziś o 12:50", job["text"])
        self.assertEqual(self.poll().status_code, 204)   # ten sam termin nie dostaje drugiego SMS-a
        self.assertEqual(self.done(job["id"]).status_code, 200)
        sms = SmsReminder.objects.get()
        self.assertEqual(sms.state, SmsReminder.State.SENT)
        note = Activity.objects.get(lead=lead)
        self.assertEqual(note.outcome, Activity.Outcome.NOTE)
        self.assertIn("SMS z przypomnieniem", note.note)

    # Stara aplikacja (bez ?sms=1) nigdy nie dostaje SMS-a, bo wzięłaby go za numer do wybrania
    def test_old_app_without_sms_flag_gets_nothing(self):
        self.promised(self.NOW + timedelta(minutes=30))
        self.assertEqual(self.poll(sms=False).status_code, 204)
        self.assertFalse(SmsReminder.objects.exists())

    # Połączenie zlecone z CRM ma pierwszeństwo przed SMS-em
    def test_call_goes_before_sms(self):
        lead = self.promised(self.NOW + timedelta(minutes=30))
        self.api(self.agent1).post("/api/dialer/call/", {"lead": lead.id}, format="json")
        self.assertEqual(self.poll().json()["type"], "call")
        self.assertEqual(self.poll().json()["type"], "sms")

    def test_no_sms_too_early_too_late_or_for_strangers(self):
        self.promised(self.NOW + timedelta(hours=3))                      # jeszcze za wcześnie (ustawione 60 min)
        self.promised(self.NOW + timedelta(minutes=3))                    # za 3 minuty to już za późno
        self.promised(self.NOW + timedelta(minutes=30), knows_caller=False)   # nie wie, kto dzwoni (domyślnie wyłączone)
        self.promised(self.NOW + timedelta(minutes=30), owner=self.agent2)    # klient innego agenta
        self.promised(self.NOW + timedelta(minutes=30), status=Lead.Status.NEW)
        # umówione przed chwilą „proszę zadzwonić za pół godziny” – SMS nie ma sensu
        self.promised(self.NOW + timedelta(minutes=30), last_activity_at=self.NOW - timedelta(minutes=5))
        self.assertEqual(self.poll().status_code, 204)

    # Po nieodebranym terminie system przesuwa ponowną próbę: stary termin nie dostaje SMS-a
    def test_moved_retry_after_missed_call_gets_no_sms(self):
        self.promised(self.NOW + timedelta(minutes=30), next_contact_at=self.NOW + timedelta(minutes=45))
        self.assertEqual(self.poll().status_code, 204)

    def test_no_sms_at_night_or_when_switched_off(self):
        self.promised(local(2, 9, 0))
        rules = get_rules()
        rules.sms_reminder_minutes = 1440
        rules.save()
        with patch("django.utils.timezone.now", return_value=local(1, 21, 0)):
            self.assertEqual(self.poll().status_code, 204)        # 21:00 – w nocy nie wysyłamy
        with patch("django.utils.timezone.now", return_value=local(2, 8, 0)):
            rules.sms_talked_enabled = False
            rules.save()
            self.assertEqual(self.poll().status_code, 204)        # wyłączone w ustawieniach
            rules.sms_talked_enabled = True
            rules.save()
            self.assertEqual(self.poll().json()["type"], "sms")   # rano o 8:00 idzie

    # Przełożony termin = nowy SMS przed nowym terminem
    def test_rescheduled_appointment_gets_new_sms(self):
        lead = self.promised(self.NOW + timedelta(minutes=30))
        self.done(self.poll().json()["id"])
        new = self.NOW + timedelta(minutes=40)
        Lead.objects.filter(pk=lead.pk).update(next_contact_at=new, agreed_contact_at=new)
        self.assertEqual(self.poll().status_code, 204)   # ten sam numer: najpierw musi minąć odstęp (60 min)
        rules = get_rules()
        rules.sms_min_gap_minutes = 5
        rules.save()
        with patch("django.utils.timezone.now", return_value=self.NOW + timedelta(minutes=6)):
            self.assertEqual(self.poll().json()["type"], "sms")
        self.assertEqual(SmsReminder.objects.count(), 2)

    def test_failed_sms_is_recorded(self):
        lead = self.promised(self.NOW + timedelta(minutes=30))
        self.done(self.poll().json()["id"], ok=False, error="brak zasięgu")
        self.assertEqual(SmsReminder.objects.get().state, SmsReminder.State.FAILED)
        self.assertIn("Nie udało się", Activity.objects.get(lead=lead).note)
        self.assertEqual(self.poll().status_code, 204)   # nie ponawiamy w kółko

    # Rozmowa w CRM („oddzwonić”, przedstawił się) wystarcza, żeby przed terminem poszedł SMS
    def test_callback_saved_in_crm_leads_to_sms(self):
        lead = self.make_lead(owner=self.agent1, phone="600100300")
        at = self.NOW + timedelta(hours=2)
        r = self.api(self.agent1).post("/api/activities/", {"lead": lead.id, "outcome": "callback", "introduced": True,
                                                            "next_contact_at": at.isoformat()}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(self.poll().status_code, 204)
        with patch("django.utils.timezone.now", return_value=at - timedelta(minutes=59)):
            self.assertEqual(self.poll().json()["phone"], "600100300")

    def test_when_text_and_render(self):
        self.assertEqual(when_text(local(1, 14, 0)), "dziś o 14:00")
        self.assertEqual(when_text(local(2, 9, 30)), "jutro o 9:30")
        self.assertTrue(when_text(local(4, 10, 0)).startswith(("w ", "we ")))
        self.assertEqual(when_text(local(20, 10, 0)), local(20).strftime("%d.%m") + " o 10:00")
        text = render_sms("Tu {imie}. {Kiedy} dzwonię ({data}, {godzina}).", self.agent1, local(2, 14, 0))
        self.assertEqual(text, f"Tu Julian. Jutro o 14:00 dzwonię ({local(2).strftime('%d.%m')}, 14:00).")

    def test_manager_sets_minutes_and_text_with_validation(self):
        api = self.api(self.manager)
        r = api.patch("/api/settings/", {"sms_reminder_minutes": 45, "sms_talked_text": "Tu {imie}, {kiedy}."}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data["sms_reminder_minutes"], r.data["sms_talked_text"]), (45, "Tu {imie}, {kiedy}."))
        self.assertEqual(api.patch("/api/settings/", {"sms_offer_text": "Tu {firma}"}, format="json").status_code, 400)
        self.assertEqual(api.patch("/api/settings/", {"sms_stranger_text": "  "}, format="json").status_code, 400)
        self.assertEqual(api.patch("/api/settings/", {"sms_min_gap_minutes": 0}, format="json").status_code, 400)
        self.assertEqual(api.patch("/api/settings/", {"sms_reminder_minutes": 5}, format="json").status_code, 400)
        self.assertEqual(self.api(self.agent1).patch("/api/settings/", {"sms_reminder_minutes": 30}, format="json").status_code, 403)

    # Trzy rodzaje SMS-ów: po ofercie, po rozmowie, „tylko termin” (ten ostatni po włączeniu)
    def test_each_kind_gets_its_own_text(self):
        rules = get_rules()
        rules.sms_offer_text, rules.sms_talked_text, rules.sms_stranger_text = "OFERTA {imie}", "ROZMOWA {imie}", "TERMIN {kiedy}"
        rules.sms_stranger_enabled = True
        rules.save()
        self.promised(self.NOW + timedelta(minutes=20), status=Lead.Status.OFFER, phone="600000001")
        self.promised(self.NOW + timedelta(minutes=30), status=Lead.Status.INTERESTED, phone="600000002")
        self.promised(self.NOW + timedelta(minutes=40), knows_caller=False, phone="600000003")
        texts = [self.poll().json()["text"] for _ in range(3)]
        self.assertEqual(texts, ["OFERTA Julian", "ROZMOWA Julian", "TERMIN dziś o 12:40"])
        self.assertEqual(list(SmsReminder.objects.order_by("pk").values_list("kind", flat=True)), ["offer", "talked", "stranger"])

    # Zabezpieczenie: dwa terminy na ten sam numer (np. dwie firmy jednej osoby) – drugi SMS dopiero po odstępie
    def test_same_number_waits_for_gap(self):
        self.promised(self.NOW + timedelta(minutes=20), phone="600 100 200")
        self.promised(self.NOW + timedelta(minutes=50), phone="+48600100200")
        self.done(self.poll().json()["id"])
        self.assertEqual(self.poll().status_code, 204)
        rules = get_rules()
        rules.sms_min_gap_minutes = 10
        rules.save()
        with patch("django.utils.timezone.now", return_value=self.NOW + timedelta(minutes=11)):
            self.assertEqual(self.poll().json()["phone"], "+48600100200")

    # Próbny SMS z ustawień: idzie z telefonu managera, a ten sam numer jest chroniony odstępem
    def test_test_sms_from_settings(self):
        api = self.api(self.manager)
        r = api.post("/api/settings/test-sms/", {"phone": "600 200 300", "text": "Tu {imie}. {Kiedy} dzwonię."}, format="json")
        self.assertEqual(r.status_code, 400)   # manager nie ma sparowanego telefonu
        self.manager.first_name = "Kasia"
        self.manager.save()
        token = DialerDevice.objects.create(user=self.manager, name="Samsung", token="m" * 64).token
        r = api.post("/api/settings/test-sms/", {"phone": "600 200 300", "text": "Tu {imie}. {Kiedy} dzwonię."}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["text"], "Tu Kasia. Dziś o 13:00 dzwonię.")
        self.assertEqual(api.get(f"/api/settings/test-sms/{r.data['id']}/").data["state"], "queued")
        self.assertEqual(self.poll().status_code, 204)   # agent go nie dostaje – tylko telefon managera
        job = self.client.get("/api/dialer/poll/?sms=1", HTTP_X_DIALER_TOKEN=token).json()
        self.assertEqual((job["type"], job["phone"]), ("sms", "600200300"))
        self.client.post(f"/api/dialer/sms/{job['id']}/done/", {"ok": True}, content_type="application/json", HTTP_X_DIALER_TOKEN=token)
        self.assertEqual(api.get(f"/api/settings/test-sms/{r.data['id']}/").data["state"], "sent")
        self.assertFalse(Activity.objects.exists())   # próbny SMS nie trafia do historii żadnego klienta
        again = api.post("/api/settings/test-sms/", {"phone": "+48 600 200 300", "text": "x"}, format="json")
        self.assertEqual(again.status_code, 400)
        self.assertIn("Zabezpieczenie", again.data["detail"])
        self.assertEqual(api.post("/api/settings/test-sms/", {"phone": "12", "text": "x"}, format="json").status_code, 400)
        self.assertEqual(self.api(self.agent1).post("/api/settings/test-sms/", {"phone": "600200301", "text": "x"}, format="json").status_code, 403)

    def test_daily_cap_per_number(self):
        rules = get_rules()
        rules.sms_min_gap_minutes = 1
        rules.save()
        for i in range(5):
            SmsReminder.objects.create(user=self.agent1, kind="test", appointment_at=self.NOW, phone="600100200",
                                       text="x", state=SmsReminder.State.SENT)
        self.promised(self.NOW + timedelta(minutes=30))
        self.assertEqual(self.poll().status_code, 204)
