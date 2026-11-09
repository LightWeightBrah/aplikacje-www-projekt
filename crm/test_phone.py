"""Telefon agenta (dialer 1.3): kto dzwoni, rejestr połączeń, SMS-y od klientów, „na mój telefon”,
a do tego: przywracanie zamkniętego klienta i poczta głosowa przy „Nie odebrał”."""

from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework import status

from .models import Activity, CallRequest, DialerDevice, IncomingSms, Lead, SharedFile, SmsReminder
from .services import register_activity
from .tests import CrmTestBase
from .work import finish_call, minutes_by_hour


def ms(dt):
    return int(dt.timestamp() * 1000)


class PhoneTestBase(CrmTestBase):
    def setUp(self):
        super().setUp()
        self.device = DialerDevice.objects.create(user=self.agent1, name="Samsung", token="p" * 64)
        self.lead = self.make_lead(owner=self.agent1, phone="600 100 200", status=Lead.Status.OFFER)

    def dialer(self, path, body):
        return self.client.post(path, body, content_type="application/json", HTTP_X_DIALER_TOKEN=self.device.token)


class RingAndCallLogTests(PhoneTestBase):
    def test_ringing_client_is_shown_and_call_counts_to_minutes(self):
        r = self.dialer("/api/dialer/phone/ring/", {"phone": "+48600100200"})
        self.assertEqual(r.json(), {"ok": True, "lead": self.lead.pk})
        live = self.api(self.agent1).get("/api/phone/live/").data
        self.assertEqual((live["ringing"]["lead"], live["ringing"]["name"]), (self.lead.pk, "Jan Kowalski"))
        # dialer zlecony z CRM nie myli się z dzwonkiem
        self.assertIsNone(self.api(self.agent1).get("/api/dialer/status/").data["call"])
        started = timezone.now()
        r = self.dialer("/api/dialer/phone/log/", {"calls": [{"phone": "600100200", "type": "in", "seconds": 185, "at": ms(started)}]})
        self.assertEqual(r.json(), {"saved": 1})
        self.assertIsNone(self.api(self.agent1).get("/api/phone/live/").data["ringing"])
        call = CallRequest.objects.get(direction=CallRequest.Direction.IN)
        self.assertEqual((call.answered, call.duration_seconds, call.lead_id, call.state), (True, 185, self.lead.pk, "done"))
        self.assertAlmostEqual(sum(h["minutes"] for h in minutes_by_hour(self.agent1)), 185 / 60, places=1)
        self.assertTrue(Activity.objects.filter(lead=self.lead, note__contains="zadzwonił do Ciebie").exists())
        # ten sam wpis drugi raz (telefon nie dostał odpowiedzi) się nie dubluje
        self.dialer("/api/dialer/phone/log/", {"calls": [{"phone": "600100200", "type": "in", "seconds": 185, "at": ms(started)}]})
        self.assertEqual(CallRequest.objects.filter(direction=CallRequest.Direction.IN).count(), 1)

    def test_private_numbers_are_not_stored(self):
        self.dialer("/api/dialer/phone/ring/", {"phone": "777888999"})
        self.assertEqual(self.api(self.agent1).get("/api/phone/live/").data["ringing"]["lead"], None)
        r = self.dialer("/api/dialer/phone/log/", {"calls": [{"phone": "777888999", "type": "in", "seconds": 60, "at": ms(timezone.now())},
                                                             {"phone": "777888999", "type": "out", "seconds": 60, "at": ms(timezone.now())}]})
        self.assertEqual(r.json(), {"saved": 0})
        self.assertFalse(CallRequest.objects.exists())

    def test_missed_call_from_client_shows_card_until_called_back(self):
        at = timezone.now() - timedelta(minutes=5)
        self.dialer("/api/dialer/phone/log/", {"calls": [{"phone": "600100200", "type": "missed", "seconds": 0, "at": ms(at)}]})
        missed = self.api(self.agent1).get("/api/phone/live/").data["missed"]
        self.assertEqual([m["lead"] for m in missed], [self.lead.pk])
        self.assertEqual(minutes_by_hour(self.agent1)[timezone.localtime().hour]["minutes"], 0)
        # nieodebrany telefon nie jest pracą agenta: wpis w historii bez autora
        self.assertIsNone(Activity.objects.get(lead=self.lead).agent)
        self.dialer("/api/dialer/phone/log/", {"calls": [{"phone": "600100200", "type": "out", "seconds": 40, "at": ms(timezone.now())}]})
        self.assertEqual(self.api(self.agent1).get("/api/phone/live/").data["missed"], [])

    def test_call_ordered_from_crm_is_not_counted_twice(self):
        call = CallRequest.objects.create(user=self.agent1, lead=self.lead, phone=self.lead.phone,
                                          state=CallRequest.State.DIALING, started_at=timezone.now())
        finish_call(self.device, call.pk, True, 90)
        self.dialer("/api/dialer/phone/log/", {"calls": [{"phone": "600100200", "type": "out", "seconds": 90, "at": ms(timezone.now())}]})
        self.assertEqual(CallRequest.objects.count(), 1)


class VoicemailTests(PhoneTestBase):
    def test_no_answer_with_seconds_is_voicemail_not_talk(self):
        call = CallRequest.objects.create(user=self.agent1, lead=self.lead, phone=self.lead.phone,
                                          state=CallRequest.State.DIALING, started_at=timezone.now())
        finish_call(self.device, call.pk, True, 18)   # odebrała poczta głosowa
        r = self.api(self.agent1).post("/api/activities/", {"lead": self.lead.pk, "outcome": "no_answer"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        call.refresh_from_db()
        self.assertFalse(call.answered)
        self.assertIsNone(Activity.objects.get(pk=r.data["id"]).call_seconds)
        self.assertEqual(sum(h["minutes"] for h in minutes_by_hour(self.agent1)), 0)

    def test_long_call_with_no_answer_click_still_counts(self):
        call = CallRequest.objects.create(user=self.agent1, lead=self.lead, phone=self.lead.phone,
                                          state=CallRequest.State.DIALING, started_at=timezone.now())
        finish_call(self.device, call.pk, True, 240)
        self.api(self.agent1).post("/api/activities/", {"lead": self.lead.pk, "outcome": "no_answer"}, format="json")
        call.refresh_from_db()
        self.assertTrue(call.answered)


class IncomingSmsTests(PhoneTestBase):
    def test_client_sms_is_saved_with_email_suggestion(self):
        r = self.dialer("/api/dialer/phone/sms/", {"sender": "+48600100200", "text": "Mój mail: biuro@firma.pl", "at": ms(timezone.now())})
        self.assertEqual(r.json(), {"saved": True})
        rows = self.api(self.agent1).get("/api/phone/sms/").data["results"]
        self.assertEqual((rows[0]["kind"], rows[0]["email"], rows[0]["lead"]), ("client", "biuro@firma.pl", self.lead.pk))
        self.assertEqual(self.api(self.agent1).get("/api/phone/live/").data["sms_unseen"], 1)
        self.api(self.agent1).post("/api/phone/sms/", {"all": True}, format="json")
        self.assertEqual(self.api(self.agent1).get("/api/phone/live/").data["sms_unseen"], 0)
        self.assertTrue(Activity.objects.filter(lead=self.lead, note__contains="biuro@firma.pl").exists())

    def test_network_says_client_is_available(self):
        self.dialer("/api/dialer/phone/sms/", {"sender": "T-Mobile", "text": "Abonent 600 100 200 jest juz dostepny. Mozesz zadzwonic."})
        available = self.api(self.agent1).get("/api/phone/live/").data["available"]
        self.assertEqual([a["lead"] for a in available], [self.lead.pk])
        self.api(self.agent1).post("/api/phone/sms/", {"ids": [available[0]["id"]]}, format="json")
        self.assertEqual(self.api(self.agent1).get("/api/phone/live/").data["available"], [])

    def test_private_and_advert_sms_are_dropped(self):
        self.assertEqual(self.dialer("/api/dialer/phone/sms/", {"sender": "777888999", "text": "Cześć, jutro kino?"}).json(), {"saved": False})
        self.assertEqual(self.dialer("/api/dialer/phone/sms/", {"sender": "Allegro", "text": "Promocja -50%!"}).json(), {"saved": False})
        self.assertFalse(IncomingSms.objects.exists())

    def test_other_agent_does_not_see_my_sms(self):
        self.dialer("/api/dialer/phone/sms/", {"sender": "600100200", "text": "Proszę o ofertę"})
        self.assertEqual(self.api(self.agent2).get("/api/phone/sms/").data["results"], [])


@override_settings(ALLOWED_HOSTS=["testserver"])
class SelfPhoneTests(PhoneTestBase):
    def test_text_goes_to_private_number_without_flood_block(self):
        api = self.api(self.agent1)
        self.assertEqual(api.post("/api/phone/self/", {"text": "x"}, format="json").status_code, 400)   # brak numeru
        self.assertEqual(api.patch("/api/phone/self/", {"private_phone": "12"}, format="json").status_code, 400)
        api.patch("/api/phone/self/", {"private_phone": "501 502 503"}, format="json")
        for text in ("adres: Kwiatowa 5", "drugi tekst"):
            r = api.post("/api/phone/self/", {"text": text}, format="json")
            self.assertEqual(r.status_code, 201, r.data)
            job = self.client.get("/api/dialer/poll/?sms=1", HTTP_X_DIALER_TOKEN=self.device.token).json()
            self.assertEqual((job["type"], job["phone"], job["text"]), ("sms", "501502503", text))
            self.client.post(f"/api/dialer/sms/{job['id']}/done/", {"ok": True}, content_type="application/json",
                             HTTP_X_DIALER_TOKEN=self.device.token)
            self.assertEqual(api.get(f"/api/phone/self/{r.data['id']}/").data["state"], "sent")
        self.assertEqual(SmsReminder.objects.filter(kind=SmsReminder.Kind.SELF, state="sent").count(), 2)

    def test_file_link_works_for_24_hours(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        api = self.api(self.agent1)
        api.patch("/api/phone/self/", {"private_phone": "501502503"}, format="json")
        r = api.post("/api/phone/self/", {"file": SimpleUploadedFile("umowa.pdf", b"%PDF-1", "application/pdf")}, format="multipart")
        self.assertEqual(r.status_code, 201, r.data)
        text = SmsReminder.objects.get(pk=r.data["id"]).text
        link = text[text.index("http"):].split()[0]
        self.assertIn("/crm/f/", link)
        got = self.client.get(link.replace("http://testserver", ""))
        self.assertEqual((got.status_code, got.content), (200, b"%PDF-1"))
        SharedFile.objects.update(created_at=timezone.now() - timedelta(hours=25))
        self.assertEqual(self.client.get(link.replace("http://testserver", "")).status_code, 404)


class ReopenTests(CrmTestBase):
    def test_skipped_by_mistake_can_be_brought_back(self):
        lead = self.make_lead(owner=self.agent1, status=Lead.Status.INTERESTED,
                              next_contact_at=timezone.now() + timedelta(hours=1), next_contact_note="wyślij ofertę")
        register_activity(lead, self.agent1, Activity.Outcome.SKIPPED, skip_reason="Inny powód")
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.LOST)
        self.assertEqual(self.api(self.agent2).post(f"/api/my-leads/{lead.pk}/reopen/").status_code, 404)
        r = self.api(self.agent1).post(f"/api/my-leads/{lead.pk}/reopen/")
        self.assertEqual(r.status_code, 200, r.data)
        lead.refresh_from_db()
        self.assertEqual((lead.status, lead.next_contact_note), (Lead.Status.INTERESTED, "wyślij ofertę"))
        self.assertTrue(lead.activities.filter(note__startswith="Przywrócony do dzwonienia").exists())
        self.assertEqual(self.api(self.agent1).post(f"/api/my-leads/{lead.pk}/reopen/").status_code, 400)   # już otwarty

    def test_sold_client_is_not_reopened(self):
        lead = self.make_lead(owner=self.agent1, status=Lead.Status.WON)
        self.assertEqual(self.api(self.agent1).post(f"/api/my-leads/{lead.pk}/reopen/").status_code, 400)


class AvailableCardTests(PhoneTestBase):
    def test_card_only_for_agreed_contacts_and_says_why(self):
        fresh = self.make_lead(owner=self.agent1, phone="600 300 400")   # nowa firma z bazy – bez karty
        call = CallRequest.objects.create(user=self.agent1, lead=self.lead, phone=self.lead.phone,
                                          state=CallRequest.State.DIALING, started_at=timezone.now() - timedelta(minutes=12))
        finish_call(self.device, call.pk, False, 0)
        self.dialer("/api/dialer/phone/sms/", {"sender": "Play", "text": "Numer 600 300 400 jest juz dostepny."})
        self.dialer("/api/dialer/phone/sms/", {"sender": "Play", "text": "Numer 600 100 200 jest juz dostepny."})
        cards = self.api(self.agent1).get("/api/phone/live/").data["available"]
        self.assertEqual([c["lead"] for c in cards], [self.lead.pk])
        self.assertEqual(cards[0]["last_call_answered"], False)
        self.assertIsNotNone(cards[0]["last_call_at"])
        # SMS o nowej firmie jest dalej w zakładce SMS
        self.assertIn(fresh.pk, [s["lead"] for s in self.api(self.agent1).get("/api/phone/sms/").data["results"]])


class PauseAndHangupTests(PhoneTestBase):
    def test_paused_phone_gets_no_calls_but_stays_paired(self):
        api = self.api(self.agent1)
        self.assertTrue(api.patch("/api/dialer/status/", {"paused": True}, format="json").data["paused"])
        self.assertEqual(api.post("/api/dialer/call/", {"lead": self.lead.pk}, format="json").status_code, 400)
        st = api.get("/api/dialer/status/").data
        self.assertEqual((st["paired"], st["online"], st["paused"]), (True, False, True))
        self.assertFalse(api.get("/api/phone/live/").data["paired"])   # linki tel: zwykłe
        api.patch("/api/dialer/status/", {"paused": False}, format="json")
        self.assertEqual(api.post("/api/dialer/call/", {"lead": self.lead.pk}, format="json").status_code, 201)

    def test_hangup_reaches_the_phone(self):
        api = self.api(self.agent1)
        self.assertEqual(api.post("/api/dialer/hangup/").status_code, 400)   # nic nie dzwoni
        call_id = api.post("/api/dialer/call/", {"lead": self.lead.pk}, format="json").data["id"]
        self.client.get("/api/dialer/poll/", HTTP_X_DIALER_TOKEN=self.device.token)   # telefon wybiera
        ask = lambda: self.client.get(f"/api/dialer/calls/{call_id}/hangup/", HTTP_X_DIALER_TOKEN=self.device.token).json()
        self.assertEqual(ask(), {"hangup": False})
        self.assertEqual(api.post("/api/dialer/hangup/").status_code, 200)
        self.assertEqual(ask(), {"hangup": True})
