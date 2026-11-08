"""Raporty managera (/api/reports/): lejek, godziny, odmowy, oferty bez odpowiedzi, końce umów."""

from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework import status

from .models import Activity, Lead, Offer
from .reports import MIN_CALLS_TO_COMPARE
from .tests import CrmTestBase


def months_ahead(n):
    today = timezone.localdate()
    idx = today.year * 12 + today.month - 1 + n
    return date(idx // 12, idx % 12 + 1, 1)


class ReportsTests(CrmTestBase):
    def get(self, user=None, **params):
        return self.api(user or self.manager).get("/api/reports/", params)

    def calls(self, agent, lead, outcome, n, note=""):
        Activity.objects.bulk_create([Activity(lead=lead, agent=agent, outcome=outcome, note=note) for _ in range(n)])

    def test_only_manager(self):
        self.assertEqual(self.get(self.agent1).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.get().status_code, status.HTTP_200_OK)
        self.assertEqual(self.get(days="0").status_code, status.HTTP_400_BAD_REQUEST)

    def test_funnel_marks_weakest_step_against_team(self):
        lead = self.make_lead(owner=self.agent1, phone="555300001")
        # agent1: dużo odbiera, ale prawie nikt nie chce oferty; agent2: odbiera i robi oferty
        self.calls(self.agent1, lead, Activity.Outcome.REFUSED, 28)
        self.calls(self.agent1, lead, Activity.Outcome.OFFER, 2)
        self.calls(self.agent2, lead, Activity.Outcome.OFFER, 12)
        self.calls(self.agent2, lead, Activity.Outcome.REFUSED, 13)
        self.calls(self.agent2, lead, Activity.Outcome.SOLD, 5)
        d = self.get().data["funnel"]
        rows = {r["agent"]: r for r in d["rows"]}
        self.assertEqual(rows[self.agent1.id]["weak_step"], "offer_rate")
        self.assertTrue(rows[self.agent1.id]["tip"])
        self.assertIsNone(rows[self.agent2.id]["weak_step"])
        self.assertEqual(d["team"]["calls"], 60)
        self.assertEqual(d["rows"][0]["agent"], self.agent2.id)   # najwięcej sprzedaży na górze

    def test_small_sample_is_not_judged(self):
        lead = self.make_lead(owner=self.agent1, phone="555300002")
        self.calls(self.agent1, lead, Activity.Outcome.NO_ANSWER, MIN_CALLS_TO_COMPARE - 1)
        self.calls(self.agent2, lead, Activity.Outcome.OFFER, 30)
        rows = {r["agent"]: r for r in self.get().data["funnel"]["rows"]}
        self.assertIsNone(rows[self.agent1.id]["weak_step"])

    def test_refusal_reasons_are_counted(self):
        lead = self.make_lead(owner=self.agent1, phone="555300003")
        self.calls(self.agent1, lead, Activity.Outcome.REFUSED, 3, "Za drogo")
        self.calls(self.agent1, lead, Activity.Outcome.REFUSED, 1, "Ma umowę w innej sieci; za drogo")
        self.calls(self.agent1, lead, Activity.Outcome.REFUSED, 2)
        r = self.get().data["refusals"]
        counts = {x["reason"]: x["count"] for x in r["rows"]}
        self.assertEqual(r["total"], 6)
        self.assertEqual(counts["Za drogo"], 4)
        self.assertEqual(counts["Ma umowę w innej sieci"], 1)
        self.assertEqual(counts["Bez podanego powodu"], 2)

    def test_hours_count_answered(self):
        lead = self.make_lead(owner=self.agent1, phone="555300004")
        self.calls(self.agent1, lead, Activity.Outcome.NO_ANSWER, 5)
        self.calls(self.agent1, lead, Activity.Outcome.CALLBACK, 5)
        h = self.get().data["hours"]
        self.assertEqual(sum(x["calls"] for x in h["weekdays"]), 10)
        self.assertEqual(sum(x["answered"] for x in h["weekdays"]), 5)

    def test_offers_waiting_lists_old_open_offers(self):
        old = self.make_lead(owner=self.agent1, phone="555300005", status=Lead.Status.OFFER,
                             next_contact_at=timezone.now() - timedelta(hours=3))
        fresh = self.make_lead(owner=self.agent1, phone="555300006", status=Lead.Status.OFFER)
        sold = self.make_lead(owner=self.agent1, phone="555300007", status=Lead.Status.WON)
        for lead, age in ((old, 5), (fresh, 0), (sold, 9)):
            o = Offer.objects.create(lead=lead, agent=self.agent1, product_name="M", monthly_total=Decimal("85"),
                                     subject="s", body="b")
            Offer.objects.filter(pk=o.pk).update(created_at=timezone.now() - timedelta(days=age))
        o = self.get().data["offers"]
        self.assertEqual(o["count"], 1)
        self.assertEqual(o["rows"][0]["lead"], old.id)
        self.assertTrue(o["rows"][0]["overdue"])
        self.assertEqual(o["monthly"], Decimal("85"))

    def test_contracts_by_month_and_ready_now(self):
        future = self.make_lead(owner=self.agent1, phone="555300008", contract_end=months_ahead(15))
        ready = self.make_lead(phone="555300009", status=Lead.Status.LOST, contract_end=months_ahead(3))
        self.make_lead(phone="555300010", contract_open_ended=True)
        c = self.get().data["contracts"]
        self.assertEqual(c["known"], 3)
        self.assertEqual(c["ready_now"], 1)
        self.assertEqual([x["lead"] for x in c["ready_idle"]], [ready.id])
        self.assertEqual(sum(m["count"] for m in c["months"]), 1)
        self.assertEqual(c["upcoming"][0]["lead"], future.id)

    def test_reports_page_renders(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get("/crm/reports/").status_code, status.HTTP_200_OK)
