"""Solid CRM: kolejka „Następny klient”, oferty z wysyłką, prowizja, dialer i informacje dla zespołu."""

from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.test import override_settings
from django.utils import timezone
from rest_framework import status

from .models import Activity, Announcement, CallRequest, Lead, LeadBatch, Offer, OfferConfig, Product, SaleItem
from .tests import CrmTestBase


def plan(name="M", price="70", promo="85", extra="25"):
    return Product.objects.create(name=name, price=Decimal(price), promo_price=Decimal(promo),
                                  extra_price=Decimal(extra), unit="mies.", features="Bez limitu GB\nRoaming UE 23 GB")


class NextWorkTests(CrmTestBase):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.callback = self.make_lead(owner=self.agent1, phone="555100001", status=Lead.Status.CALLBACK,
                                       next_contact_at=now - timedelta(minutes=10))
        self.offer = self.make_lead(owner=self.agent1, phone="555100002", status=Lead.Status.OFFER,
                                    next_contact_at=now - timedelta(minutes=1))
        self.retry = self.make_lead(owner=self.agent1, phone="555100003", attempts_count=1,
                                    next_contact_at=now - timedelta(minutes=30))

    def next(self):
        return self.api(self.agent1).post("/api/work/next/")

    # „Zapisz i dalej”: zapis wyniku z ?next=1 oddaje od razu następnego klienta (jedno zapytanie)
    def test_save_with_next_returns_next_client(self):
        r = self.api(self.agent1).post("/api/activities/?next=1", {"lead": self.offer.id, "outcome": "no_answer"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        self.assertEqual(r.data["next"]["reason"], "callback")
        self.assertEqual(r.data["next"]["lead"]["id"], self.callback.id)
        self.assertIn("activities", r.data["next"]["lead"])
        plain = self.api(self.agent1).post("/api/activities/", {"lead": self.callback.id, "outcome": "note", "note": "x"}, format="json")
        self.assertNotIn("next", plain.data)

    # „Następny klient” oddaje od razu pełną kartę (historia, segment) – ekran nie pyta drugi raz
    def test_next_returns_full_lead_card(self):
        lead = self.next().data["lead"]
        self.assertIn("activities", lead)
        self.assertIn("segment", lead)
        self.assertIn("owner_no_answers", lead)

    def test_offer_follow_up_comes_first_then_callback_then_retry(self):
        r = self.next()
        self.assertEqual((r.data["reason"], r.data["lead"]["id"]), ("offer", self.offer.id))
        Lead.objects.filter(pk=self.offer.pk).update(next_contact_at=timezone.now() + timedelta(days=1))
        self.assertEqual(self.next().data["lead"]["id"], self.callback.id)
        Lead.objects.filter(pk=self.callback.pk).update(next_contact_at=timezone.now() + timedelta(days=1))
        self.assertEqual(self.next().data["reason"], "retry")

    def test_new_lead_from_own_base_when_nothing_is_due(self):
        Lead.objects.filter(owner=self.agent1).update(next_contact_at=timezone.now() + timedelta(days=1))
        base = LeadBatch.objects.create(name="Moja", assigned_to=self.agent1)
        fresh = self.make_lead(batch=base, phone="555100009")
        r = self.next()
        self.assertEqual((r.data["reason"], r.data["lead"]["id"]), ("new", fresh.id))

    def test_nothing_to_do_returns_404(self):
        Lead.objects.filter(owner=self.agent1).update(next_contact_at=timezone.now() + timedelta(days=1))
        self.assertEqual(self.next().status_code, status.HTTP_404_NOT_FOUND)

    def test_summary_counts_segments(self):
        data = self.api(self.agent1).get("/api/work/summary/").data
        self.assertEqual(data["counts"]["offer"], {"total": 1, "due": 1})
        self.assertEqual(data["counts"]["callback"]["due"], 1)
        self.assertEqual(data["counts"]["retry"]["total"], 1)
        self.assertEqual(data["today"]["goal_minutes_per_hour"], 15)


class OfferTests(CrmTestBase):
    def setUp(self):
        super().setUp()
        self.plan = plan()
        self.lead = self.make_lead(owner=self.agent1, contact_name="", phone="555200001")
        self.agent1.first_name, self.agent1.last_name = "Jan", "Agent"
        self.agent1.save()

    def send(self, **extra):
        body = {"lead": self.lead.id, "product": self.plan.id, "quantity": 3, "term_months": 36,
                "promo": True, "client_email": "klient@example.com",
                "next_contact_at": (timezone.now() + timedelta(days=1)).isoformat(), **extra}
        return self.api(self.agent1).post("/api/offers/", body, format="json")

    def test_offer_computes_price_commission_and_sets_follow_up(self):
        r = self.send()
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        offer = Offer.objects.get()
        self.assertEqual(offer.monthly_total, Decimal("135.00"))   # 85 + 2 × 25
        self.assertEqual(offer.commission, Decimal("1215.00"))     # × 9 dla 36 mies.
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.OFFER)
        self.assertTrue(self.lead.knows_caller)
        self.assertEqual(self.lead.email, "klient@example.com")
        self.assertIsNotNone(self.lead.next_contact_at)
        self.assertEqual(Activity.objects.get().outcome, "offer")
        self.assertIn("mailto:", r.data["mailto"])

    def test_forward_mode_note_for_sender_and_no_email_without_server(self):
        OfferConfig.objects.create(forward_email="czarek@example.com")
        self.send()
        offer = Offer.objects.get()
        self.assertEqual(offer.recipient, "czarek@example.com")
        self.assertIn("Adres klienta: klient@example.com", offer.body)
        self.assertFalse(offer.sent)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(OFFER_EMAIL_ENABLED=True)
    def test_offer_is_emailed_when_mail_server_is_configured(self):
        OfferConfig.objects.create(forward_email="czarek@example.com")
        self.send()
        self.assertTrue(Offer.objects.get().sent)
        self.assertEqual(mail.outbox[0].to, ["czarek@example.com"])
        self.assertIn("135,00 zł", mail.outbox[0].body)

    def test_direct_mode_requires_client_email(self):
        OfferConfig.objects.create(mode="direct")
        self.assertEqual(self.send(client_email="").status_code, status.HTTP_400_BAD_REQUEST)

    def test_other_agent_cannot_send_offer(self):
        r = self.api(self.agent2).post("/api/offers/", {"lead": self.lead.id, "product": self.plan.id,
                                       "next_contact_at": (timezone.now() + timedelta(days=1)).isoformat()}, format="json")
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_preview(self):
        r = self.api(self.agent1).post("/api/offers/preview/", {"product": self.plan.id, "quantity": 2, "term_months": 24}, format="json")
        self.assertEqual(r.data, {"monthly_total": "95.00", "commission": "570.00"})

    def test_offer_shows_in_lead_history_and_undo_removes_it(self):
        self.send()
        detail = self.api(self.agent1).get(f"/api/leads/{self.lead.id}/").data
        self.assertEqual(detail["activities"][0]["offer"]["monthly_total"], "135.00")
        self.assertEqual(detail["segment"], "offer")
        act = Activity.objects.get()
        self.assertEqual(self.api(self.agent1).post(f"/api/activities/{act.id}/undo/").status_code, 200)
        self.assertFalse(Offer.objects.exists())

    def test_manager_changes_offer_settings_agent_cannot(self):
        self.assertEqual(self.api(self.agent1).patch("/api/offer-config/", {"forward_email": "x@example.com"}, format="json").status_code, 403)
        r = self.api(self.manager).patch("/api/offer-config/", {"forward_email": "x@example.com", "commission_36": "8"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(OfferConfig.get().commission_36, Decimal("8.00"))


class SaleCommissionTests(CrmTestBase):
    def test_sale_with_term_counts_commission(self):
        p = plan()
        lead = self.make_lead(owner=self.agent1)
        r = self.api(self.agent1).post("/api/activities/", {
            "lead": lead.id, "outcome": "sold",
            "sale_lines": [{"product": p.id, "quantity": 2, "term_months": 24, "promo": False}],
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        item = SaleItem.objects.get()
        self.assertEqual((item.total, item.commission, item.term_months), (Decimal("95.00"), Decimal("570.00"), 24))
        rows = self.api(self.agent1).get("/api/leaderboard/?days=1&sort=commission").data["results"]
        me = next(r for r in rows if r["username"] == "agent1")
        self.assertEqual(Decimal(me["commission"]), Decimal("570.00"))

    def test_callback_remembers_whether_client_knows_who_calls(self):
        lead = self.make_lead(owner=self.agent1)
        self.api(self.agent1).post("/api/activities/", {
            "lead": lead.id, "outcome": "callback", "introduced": False,
            "next_contact_at": (timezone.now() + timedelta(hours=2)).isoformat(),
        }, format="json")
        lead.refresh_from_db()
        self.assertFalse(lead.knows_caller)
        self.api(self.agent1).post("/api/activities/", {
            "lead": lead.id, "outcome": "callback", "introduced": True,
            "next_contact_at": (timezone.now() + timedelta(hours=3)).isoformat(),
        }, format="json")
        lead.refresh_from_db()
        self.assertTrue(lead.knows_caller)


class DialerTests(CrmTestBase):
    def pair(self):
        code = self.api(self.agent1).post("/api/dialer/pairing/").data["code"]
        r = self.client.post("/api/dialer/pair/", {"code": code, "name": "Xiaomi"}, content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()["token"]

    def test_full_call_flow_records_minutes(self):
        token = self.pair()
        lead = self.make_lead(owner=self.agent1, phone="555300001")
        self.assertTrue(self.api(self.agent1).get("/api/dialer/status/").data["online"])
        self.assertEqual(self.api(self.agent1).post("/api/dialer/call/", {"lead": lead.id}, format="json").status_code, 201)
        poll = self.client.get("/api/dialer/poll/", HTTP_X_DIALER_TOKEN=token)
        self.assertEqual(poll.json()["phone"], "555300001")
        self.assertEqual(self.client.get("/api/dialer/poll/", HTTP_X_DIALER_TOKEN=token).status_code, 204)
        self.client.post(f"/api/dialer/calls/{poll.json()['id']}/finish/", {"answered": True, "duration_seconds": 330},
                         content_type="application/json", HTTP_X_DIALER_TOKEN=token)
        self.api(self.agent1).post("/api/activities/", {"lead": lead.id, "outcome": "refused"}, format="json")
        self.assertEqual(Activity.objects.get().call_seconds, 330)
        today = self.api(self.agent1).get("/api/work/summary/").data["today"]
        self.assertEqual(today["minutes_today"], 5.5)
        self.assertTrue(today["dialer_used"])

    def test_wrong_code_and_wrong_token_are_rejected(self):
        self.assertEqual(self.client.post("/api/dialer/pair/", {"code": "000000"}, content_type="application/json").status_code, 400)
        self.assertEqual(self.client.get("/api/dialer/poll/", HTTP_X_DIALER_TOKEN="zly").status_code, 401)

    def test_agent_cannot_dial_other_agents_lead(self):
        lead = self.make_lead(owner=self.agent2)
        self.assertEqual(self.api(self.agent1).post("/api/dialer/call/", {"lead": lead.id}, format="json").status_code, 404)

    def test_new_call_cancels_pending(self):
        self.api(self.agent1).post("/api/dialer/call/", {"phone": "555300002"}, format="json")
        self.api(self.agent1).post("/api/dialer/call/", {"phone": "555300003"}, format="json")
        self.assertEqual(CallRequest.objects.filter(state="pending").count(), 1)


class AnnouncementTests(CrmTestBase):
    def test_manager_posts_agents_read_only_active(self):
        r = self.api(self.manager).post("/api/announcements/", {"title": "Promocja", "body": "6 mies. gratis", "pinned": True}, format="json")
        self.assertEqual(r.status_code, 201)
        Announcement.objects.create(title="Stara", is_active=False)
        self.assertEqual([a["title"] for a in self.api(self.agent1).get("/api/announcements/").data], ["Promocja"])
        self.assertEqual(self.api(self.agent1).post("/api/announcements/", {"title": "x"}, format="json").status_code, 403)
        self.assertEqual(self.api(self.manager).delete(f"/api/announcements/{r.data['id']}/").status_code, 204)


class OfferHtmlTests(OfferTests):
    """Oferta w układzie wzornika: mail HTML + załącznik oraz plik do pobrania przez agenta."""

    @override_settings(OFFER_EMAIL_ENABLED=True)
    def test_email_has_formatted_offer_and_attachment(self):
        OfferConfig.objects.create(forward_email="czarek@example.com", contact_phone="600 100 200")
        self.send()
        message = mail.outbox[0]
        html, mime = message.alternatives[0]
        self.assertEqual(mime, "text/html")
        self.assertIn("Adres klienta:", html)              # ramka z danymi dla osoby wysyłającej
        self.assertNotIn("Proszę o wysłanie", html)         # bez formułek – same dane
        self.assertIn("Oferta: M", html)
        self.assertIn("za 3 numery: 135,00 zł netto", html)
        self.assertIn(">Roaming UE 23 GB</li>", html)   # punkty taryfy jako lista
        name, content, _ = message.attachments[0]
        self.assertTrue(name.startswith("oferta-") and name.endswith(".html"))
        self.assertIn("600 100 200", content)
        self.assertNotIn("Adres klienta:", content)      # załącznik = sam list do klienta

    def test_agent_downloads_offer_file_other_agent_cannot(self):
        self.send()
        offer = Offer.objects.get()
        r = self.api(self.agent1).get(f"/api/offers/{offer.id}/html/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment;", r["Content-Disposition"])
        self.assertIn("Całkowite zobowiązanie miesięczne", r.content.decode())
        self.assertEqual(self.api(self.agent2).get(f"/api/offers/{offer.id}/html/").status_code, 404)

    def test_offer_list_does_not_send_html(self):
        self.send()
        r = self.api(self.agent1).get(f"/api/offers/?lead={self.lead.id}")
        self.assertNotIn("html", r.data[0])


class CompaniesLastTests(CrmTestBase):
    """Ustawienie zespołu: najpierw firmy jednoosobowe, spółki na końcu (domyślnie wszystko losowo)."""

    def setUp(self):
        super().setUp()
        from .models import Company
        self.spolka = self.make_lead(phone="555300001", company=Company.objects.create(name="Alfa Sp. z o.o."))
        self.jdg = self.make_lead(phone="555300002", company=Company.objects.create(name="Usługi Jan Nowak"))

    def test_company_names_are_recognised(self):
        from .services import is_company_entity
        for name in ("Alfa Sp. z o.o.", "BETA SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ", "Gamma S.A.", "Kowalski i wspólnicy sp.j.", "Delta sp. k."):
            self.assertTrue(is_company_entity(name), name)
        for name in ("Usługi Jan Nowak", "Sklep Spożywczy Emil Wzór", "Spa Anna"):
            self.assertFalse(is_company_entity(name), name)

    def test_setting_puts_companies_last_and_only_manager_changes_it(self):
        self.assertEqual(self.api(self.agent1).patch("/api/settings/", {"companies_last": True}, format="json").status_code, 403)
        r = self.api(self.manager).patch("/api/settings/", {"companies_last": True}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        first = self.api(self.agent1).post("/api/work/next/").data["lead"]["id"]
        self.assertEqual(first, self.jdg.id)
        Lead.objects.filter(pk=first).update(next_contact_at=timezone.now() + timedelta(days=1), attempts_count=1)
        self.assertEqual(self.api(self.agent1).post("/api/work/next/").data["lead"]["id"], self.spolka.id)


class CalendarFileTests(CrmTestBase):
    def test_ics_is_valid_for_google_calendar(self):
        from .models import Company
        long_note = "Omówić ofertę na pięć numerów i internet światłowodowy, decyduje wspólnik pan Marek"
        self.make_lead(owner=self.agent1, phone="555400001", status=Lead.Status.OFFER, next_contact_note=long_note,
                       company=Company.objects.create(name="Zakład Ślusarski Żółw"), contact_name="",
                       next_contact_at=timezone.now() + timedelta(hours=3))
        body = self.api(self.agent1).get("/api/my-leads/calendar/").content.decode("utf-8")
        self.assertIn("METHOD:PUBLISH", body)
        self.assertIn("SUMMARY:Follow-up oferty: Zakład Ślusarski Żółw", body)
        self.assertNotIn("\n", body.replace("\r\n", ""))   # tylko CRLF
        for line in body.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75, line)
        unfolded = body.replace("\r\n ", "")
        self.assertIn("Sprawa: " + long_note.replace(",", "\\,"), unfolded)


class ContactAndMinutesTests(CrmTestBase):
    """Agent dodaje klienta z polecenia; minuty rozmów z celem dziennym; ile zostało w puli."""

    def test_agent_adds_referred_contact_and_duplicate_is_refused(self):
        source = self.make_lead(owner=self.agent1, phone="555500001", contact_name="Polecający")
        r = self.api(self.agent1).post("/api/work/contact/", {"phone": "+48 600 700 800", "company_name": "Znajomy Sp. z o.o.",
                                                           "contact_name": "Pan Adam", "referred_by": source.id}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        lead = Lead.objects.get(pk=r.data["lead"]["id"])
        self.assertEqual((lead.owner, lead.status, lead.company.name), (self.agent1, Lead.Status.NEW, "Znajomy Sp. z o.o."))
        self.assertIn("Polecenie od: Polecający", lead.activities.get().note)
        again = self.api(self.agent1).post("/api/work/contact/", {"phone": "600700800"}, format="json")
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.data["lead"], lead.id)
        other = self.api(self.agent2).post("/api/work/contact/", {"phone": "600-700-800"}, format="json")
        self.assertEqual((other.status_code, other.data["lead"]), (409, None))   # cudzego klienta nie pokazujemy

    def test_minutes_report_has_daily_goal_from_working_hours(self):
        from datetime import datetime, time
        from unittest import mock
        noon = timezone.make_aware(datetime.combine(timezone.localdate(), time(12, 0)))
        with mock.patch("django.utils.timezone.now", return_value=noon):
            self._minutes_case(noon)

    def _minutes_case(self, now):
        Activity.objects.create(lead=self.make_lead(owner=self.agent1, phone="555500002"), agent=self.agent1, outcome="no_answer")
        Activity.objects.filter(agent=self.agent1).update(created_at=now - timedelta(hours=4))
        CallRequest.objects.create(user=self.agent1, phone="555500002", state=CallRequest.State.DONE, answered=True,
                                   duration_seconds=1800, ended_at=now)
        d = self.api(self.agent1).get("/api/work/minutes/?days=1").data
        self.assertEqual(d["worked_minutes"], 240)   # pracuje od 4 h (pierwszy zapis) do teraz
        self.assertEqual(d["goal"], 60)              # 4 h × 15 min
        self.assertEqual(d["minutes"], 30.0)
        self.assertEqual(len(d["by_hour"]), 24)
        week = self.api(self.agent1).get("/api/work/minutes/?days=7").data
        self.assertEqual(len(week["days"]), 7)
        self.assertIsNone(week["by_hour"])

    def test_summary_shows_shared_pool_left(self):
        self.make_lead(phone="555500003")
        mine = LeadBatch.objects.create(name="Cudza", assigned_to=self.agent2)
        self.make_lead(phone="555500004", batch=mine)
        pool_left = self.api(self.agent1).get("/api/work/summary/").data["base"]["pool_left"]
        self.assertEqual(pool_left, Lead.objects.filter(owner__isnull=True, status="new", batch__isnull=True).count())


def _at(hour, minute=0):
    from datetime import datetime, time
    return timezone.make_aware(datetime.combine(timezone.localdate(), time(hour, minute)))


class WorkDayTests(CrmTestBase):
    """Przyjście = zalogowanie, wyjście = wylogowanie; przerwy tylko pokazujemy. Manager widzi obecność."""

    def _row(self, user, now):
        from unittest import mock
        with mock.patch("django.utils.timezone.now", return_value=now):
            rows = self.api(self.manager).get("/api/work/attendance/").data["results"]
        return next(x for x in rows if x["username"] == user.username)

    def _post(self, user, path, now):
        from unittest import mock
        with mock.patch("django.utils.timezone.now", return_value=now):
            return self.api(user).post(path)

    def test_login_logout_and_attendance_for_manager(self):
        from .models import WorkDay
        self.assertEqual(self._post(self.agent1, "/api/work/login/", _at(9)).status_code, 204)
        self.assertEqual(self._post(self.agent1, "/api/work/logout/", _at(12, 20)).status_code, 204)
        self.assertEqual(self.api(self.agent1).get("/api/work/attendance/").status_code, 403)
        row = self._row(self.agent1, _at(15))
        self.assertFalse(row["working_now"])
        self.assertIsNotNone(row["ended_at"])
        self.assertEqual(row["worked_minutes"], 200)
        self.assertEqual(row["goal"], 50)                      # 3 h 20 min × 15 min/h
        self.assertEqual(WorkDay.objects.get(user=self.agent1).first_seen, _at(9))
        self.assertIsNone(self._row(self.agent2, _at(15))["started_at"])

    def test_login_after_logout_reopens_the_day_and_shows_break(self):
        self._post(self.agent1, "/api/work/login/", _at(9))
        self._post(self.agent1, "/api/work/logout/", _at(9, 10))
        self._post(self.agent1, "/api/work/login/", _at(9, 50))
        row = self._row(self.agent1, _at(9, 55))
        self.assertTrue(row["working_now"])
        self.assertIsNone(row["ended_at"])
        self.assertEqual(row["worked_minutes"], 55)             # przerwa NIE jest odejmowana
        self.assertEqual(row["break_minutes"], 40)
        self.assertEqual(len(row["breaks"]), 1)

    def test_short_pause_is_not_a_break(self):
        self._post(self.agent1, "/api/work/login/", _at(9))
        self._post(self.agent1, "/api/work/next/", _at(9, 10))
        self.assertEqual(self._row(self.agent1, _at(9, 12))["break_minutes"], 0)

    def test_ongoing_break_is_shown(self):
        self._post(self.agent1, "/api/work/login/", _at(9))
        row = self._row(self.agent1, _at(9, 20))
        self.assertFalse(row["working_now"])
        from datetime import datetime
        self.assertEqual(datetime.fromisoformat(row["on_break_since"]), _at(9))


class IdleWorkDayTests(CrmTestBase):
    def test_background_refresh_does_not_count_as_work(self):
        """Karta „Dzwonienie” zostawiona na noc odświeża liczniki, ale czas pracy nie rośnie."""
        from unittest import mock

        from .models import WorkDay
        with mock.patch("django.utils.timezone.now", return_value=_at(9)):
            self.api(self.agent1).get("/api/work/summary/?open=1")   # otwarcie „Dzwonienia” = przyjście
        for hour in (12, 18, 23):
            with mock.patch("django.utils.timezone.now", return_value=_at(hour)):
                self.api(self.agent1).get("/api/work/summary/")      # odświeżanie w tle
        self.assertEqual(WorkDay.objects.get(user=self.agent1).last_seen, _at(9))

    def test_agent_who_did_not_log_out_stops_counting_after_idle(self):
        from unittest import mock
        with mock.patch("django.utils.timezone.now", return_value=_at(9)):
            self.api(self.agent1).post("/api/work/login/")
        with mock.patch("django.utils.timezone.now", return_value=_at(11)):
            self.api(self.agent1).post("/api/work/next/")
        with mock.patch("django.utils.timezone.now", return_value=_at(15)):
            rows = self.api(self.manager).get("/api/work/attendance/").data["results"]
        row = next(x for x in rows if x["username"] == self.agent1.username)
        self.assertFalse(row["working_now"])
        self.assertEqual(row["worked_minutes"], 120)   # liczy się do ostatniego ruchu, nie do teraz
        self.assertEqual(row["goal"], 30)
        self.assertEqual(row["break_minutes"], 120)   # 9:00–11:00 bez żadnego ruchu = przerwa (pokazana, nie odjęta)



class OfferTextTests(CrmTestBase):
    """Treść oferty jak we wzorniku 11.0: średni koszt przy promocji, światłowód / TV bez „numerów”."""

    def setUp(self):
        super().setUp()
        self.lead = self.make_lead(owner=self.agent1, phone="555100009")
        self.config = OfferConfig.get()

    def test_promo_adds_average_monthly_cost(self):
        from .work import build_offer, offer_letter_html

        product = plan()                                    # M: 85 zł z promocją
        _, body = build_offer(self.config, self.lead, self.agent1, [{"product": product, "quantity": 1, "term": 24, "promo": True}], "")
        self.assertIn("Średni miesięczny koszt w okresie 24 miesięcy: 63,75 zł netto", body)   # 85 × 18 / 24
        html = offer_letter_html(self.config, [{"product": product, "quantity": 1, "term": 36, "promo": True}])
        self.assertIn("Średni miesięczny koszt w okresie 36 miesięcy: 70,83 zł netto", html)
        _, body = build_offer(self.config, self.lead, self.agent1, [{"product": product, "quantity": 1, "term": 24, "promo": False}], "")
        self.assertNotIn("Średni", body)

    def test_fiber_offer_speaks_of_monthly_fee_not_numbers(self):
        from .work import build_offer, offer_letter_html

        fiber = Product.objects.create(name="Światłowód L – blok", price=Decimal("65"), extra_price=0,
                                       features="Prędkość światłowodu: do 600 Mb/s")
        _, body = build_offer(self.config, self.lead, self.agent1, [{"product": fiber, "quantity": 2, "term": 24, "promo": True}], "")
        self.assertIn("Ilość: 2", body)
        self.assertIn("Opłata miesięczna: 130,00 zł netto", body)
        self.assertNotIn("numer", body.split("Z poważaniem")[0].lower().replace("liczba numerów", "x"))
        self.assertNotIn("Promocja", body)                  # światłowód nie ma „6 miesięcy gratis”
        self.assertNotIn("Średni", body)
        html = offer_letter_html(self.config, [{"product": fiber, "quantity": 2, "term": 24, "promo": False}])
        self.assertIn("Opłata miesięczna za 2 szt.: 130,00 zł netto", html)

    # Promocja taryfy z własnymi polami (światłowód z wzornika: 12 miesięcy po 50 zł, potem 85 zł)
    def test_fiber_promo_with_paid_months(self):
        from .work import build_offer, line_numbers

        fiber = Product.objects.create(name="Światłowód M – blok", price=Decimal("45"), promo_price=Decimal("85"),
                                       promo_label="12 miesięcy za 50 zł", promo_months=12,
                                       promo_month_price=Decimal("50"))
        n = line_numbers(self.config, {"product": fiber, "quantity": 1, "term": 24, "promo": True})
        self.assertEqual((n["monthly"], n["first"], n["average"]), (Decimal("85"), Decimal("50.00"), Decimal("67.50")))
        _, body = build_offer(self.config, self.lead, self.agent1, [{"product": fiber, "quantity": 1, "term": 24, "promo": True}], "")
        self.assertIn("Promocja: 12 miesięcy za 50 zł", body)
        self.assertIn("Przez pierwsze 12 miesięcy: 50,00 zł netto miesięcznie, od 13. miesiąca: 85,00 zł netto", body)
        self.assertIn("Ilość: 1", body)                     # światłowód: sztuki, nie numery
        n = line_numbers(self.config, {"product": fiber, "quantity": 1, "term": 24, "promo": False})
        self.assertEqual((n["monthly"], n["first"], n["average"]), (Decimal("45"), None, None))

    # Zestaw kilku usług: osobna sekcja dla każdej i suma na końcu
    def test_bundle_offer_lists_all_services_and_total(self):
        from .work import build_offer, offer_letter_html

        mobile = plan()                                     # M: 70 / 85 zł, +25 zł za numer
        tv = Product.objects.create(name="Magenta TV M", price=Decimal("46.30"), extra_price=0)
        lines = [{"product": mobile, "quantity": 3, "term": 24, "promo": False},
                 {"product": tv, "quantity": 1, "term": 24, "promo": True}]   # TV bez promocji – flaga ignorowana
        _, body = build_offer(self.config, self.lead, self.agent1, lines, "")
        self.assertIn("OFERTA: M", body)
        self.assertIn("OFERTA: Magenta TV M", body)
        self.assertIn("RAZEM miesięcznie za wszystkie usługi: 166,30 zł netto", body)   # 70 + 2 × 25 + 46,30
        self.assertIn("Razem miesięcznie za wszystkie usługi: 166,30 zł netto", offer_letter_html(self.config, lines))


class BundleOfferTests(OfferTests):
    """Zestaw kilku usług w jednej ofercie (np. numery + światłowód)."""

    def test_offer_with_several_lines(self):
        fiber = Product.objects.create(name="Światłowód L – blok", price=Decimal("65"), extra_price=0)
        r = self.send(lines=[{"product": self.plan.id, "quantity": 3, "term_months": 36, "promo": False},
                             {"product": fiber.id, "quantity": 1, "term_months": 24, "promo": False}])
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        offer = Offer.objects.get()
        self.assertEqual(offer.monthly_total, Decimal("185.00"))   # 70 + 2 × 25 + 65
        self.assertEqual(offer.commission, Decimal("1080.00") + Decimal("65") * OfferConfig.get().multiplier(24))
        self.assertEqual([x["product_name"] for x in offer.lines], ["M", "Światłowód L – blok"])
        self.assertIn("M ×3 + Światłowód L – blok", offer.product_name)
        self.assertIn("RAZEM miesięcznie za wszystkie usługi", offer.body)

    def test_offer_without_any_product_is_rejected(self):
        r = self.send(product=None, lines=[])
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)


class CalendarCallbackTests(CrmTestBase):
    """Kalendarz → „+ Dodaj callback”: termin bez zapisywania rozmowy."""

    def test_agent_adds_callback_to_own_lead(self):
        lead = self.make_lead(owner=self.agent1, phone="555300001")
        at = timezone.now() + timedelta(days=2)
        r = self.api(self.agent1).post(f"/api/my-leads/{lead.id}/add-callback/",
                                       {"next_contact_at": at.isoformat(), "next_contact_note": "omówić 3 numery"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        lead.refresh_from_db()
        self.assertEqual((lead.status, lead.next_contact_note), (Lead.Status.CALLBACK, "omówić 3 numery"))
        self.assertEqual(lead.next_contact_at.replace(microsecond=0), at.replace(microsecond=0))
        note = Activity.objects.get(lead=lead)
        self.assertEqual(note.outcome, Activity.Outcome.NOTE)       # notatka, nie telefon
        self.assertIn("Callback dodany w kalendarzu", note.note)

    def test_callback_rules(self):
        mine = self.make_lead(owner=self.agent1, phone="555300002")
        other = self.make_lead(owner=self.agent2, phone="555300003")
        future = (timezone.now() + timedelta(hours=3)).isoformat()
        self.assertEqual(self.api(self.agent1).post(f"/api/my-leads/{other.id}/add-callback/",
                                                    {"next_contact_at": future}, format="json").status_code, 404)
        past = (timezone.now() - timedelta(hours=1)).isoformat()
        self.assertEqual(self.api(self.agent1).post(f"/api/my-leads/{mine.id}/add-callback/",
                                                    {"next_contact_at": past}, format="json").status_code, 400)
        Lead.objects.filter(pk=mine.pk).update(status=Lead.Status.WON)
        self.assertEqual(self.api(self.agent1).post(f"/api/my-leads/{mine.id}/add-callback/",
                                                    {"next_contact_at": future}, format="json").status_code, 400)
        Lead.objects.filter(pk=mine.pk).update(status=Lead.Status.OFFER)
        self.api(self.agent1).post(f"/api/my-leads/{mine.id}/add-callback/", {"next_contact_at": future}, format="json")
        mine.refresh_from_db()
        self.assertEqual(mine.status, Lead.Status.OFFER)              # oferta zostaje ofertą


class MinutesGoalTests(CrmTestBase):
    """Manager ustawia agentowi cel minut rozmów na godzinę (domyślnie 15)."""

    def test_manager_changes_goal(self):
        from .work import goal_per_hour

        self.assertEqual(goal_per_hour(self.agent1), 15)
        r = self.api(self.manager).patch(f"/api/users/{self.agent1.id}/", {"goal_minutes_per_hour": 20}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assertEqual(r.data["goal_minutes_per_hour"], 20)
        self.assertEqual(goal_per_hour(self.agent1), 20)
        self.assertEqual(self.api(self.agent1).get("/api/work/summary/").data["today"]["goal_minutes_per_hour"], 20)
        bad = self.api(self.manager).patch(f"/api/users/{self.agent1.id}/", {"goal_minutes_per_hour": 0}, format="json")
        self.assertEqual(bad.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.api(self.agent1).patch(f"/api/users/{self.agent2.id}/", {"goal_minutes_per_hour": 5},
                                                     format="json").status_code, status.HTTP_403_FORBIDDEN)
