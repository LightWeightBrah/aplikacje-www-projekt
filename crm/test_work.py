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
        self.assertIn("Proszę o wysłanie oferty do klienta", html)
        self.assertIn("Oferta: M", html)
        self.assertIn("za 3 numery: 135,00 zł netto", html)
        self.assertIn(">Roaming UE 23 GB</li>", html)   # punkty taryfy jako lista
        name, content, _ = message.attachments[0]
        self.assertTrue(name.startswith("oferta-") and name.endswith(".html"))
        self.assertIn("600 100 200", content)
        self.assertNotIn("Proszę o wysłanie", content)   # załącznik = sam list do klienta

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
        Activity.objects.filter(agent=self.agent1).update(created_at=now - timedelta(hours=2, minutes=10))
        CallRequest.objects.create(user=self.agent1, phone="555500002", state=CallRequest.State.DONE, answered=True,
                                   duration_seconds=1800, ended_at=now)
        d = self.api(self.agent1).get("/api/work/minutes/?days=1").data
        self.assertEqual(d["hours"], 3)            # od pierwszego zapisu 2 h 10 min temu → 3 pełne godziny
        self.assertEqual(d["goal"], 45)
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
