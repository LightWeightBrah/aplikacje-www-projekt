"""Raport „Wszystko po agencie”, „Usuń tę bazę”, pory ponownych prób wg odbierania i pełna lista kanałów TV."""

from datetime import datetime, timedelta
from decimal import Decimal
from io import BytesIO
from zoneinfo import ZoneInfo

from django.utils import timezone
from openpyxl import load_workbook

from .models import Activity, CallRequest, Company, Lead, LeadBatch, Offer, SaleItem
from .rules import ANSWER_STATS_MIN_CALLS, _slot_starts, answer_rates_by_slot, get_rules, suggest_retry
from .tests import CrmTestBase
from .tv_channels import all_groups


class AgentReportTests(CrmTestBase):
    url = "/api/reports/agent/"

    def test_only_manager_and_people_list(self):
        self.assertEqual(self.api(self.agent1).get(self.url).status_code, 403)
        self.agent2.is_active = False
        self.agent2.save()
        people = self.api(self.manager).get(self.url).data["people"]
        names = {p["username"]: p for p in people}
        self.assertIn("agent1", names)
        self.assertFalse(names["agent2"]["active"])          # wyłączone konto też jest na liście
        self.assertEqual(people[-1]["username"], "agent2")   # wyłączeni na końcu

    def test_report_counts_everything(self):
        lead = self.make_lead(owner=self.agent1, phone="555400001")
        Activity.objects.create(lead=lead, agent=self.agent1, outcome=Activity.Outcome.NO_ANSWER)
        Activity.objects.create(lead=lead, agent=self.agent1, outcome=Activity.Outcome.OFFER, call_seconds=125, note="Wysłana")
        sold = Activity.objects.create(lead=lead, agent=self.agent1, outcome=Activity.Outcome.SOLD, call_seconds=60)
        SaleItem.objects.create(activity=sold, product_name="M", unit_price=Decimal("70"), quantity=2,
                                total=Decimal("140"), commission=Decimal("840"))
        Offer.objects.create(lead=lead, agent=self.agent1, product_name="M", monthly_total=Decimal("140"),
                             subject="Oferta", body="…")
        Activity.objects.create(lead=lead, agent=self.agent2, outcome=Activity.Outcome.SOLD)   # nie jego
        self.make_lead(owner=self.agent1, phone="555400002", status=Lead.Status.CALLBACK,
                       next_contact_at=timezone.now() + timedelta(days=1))

        d = self.api(self.manager).get(self.url, {"user": self.agent1.pk, "days": 7}).data
        t = d["totals"]
        self.assertEqual((t["calls"], t["talks"], t["offers"], t["sold"]), (3, 2, 1, 1))
        self.assertEqual(t["commission"], Decimal("840"))
        self.assertEqual(t["clients_now"], 2)
        self.assertEqual(len(d["sales"]), 1)
        self.assertEqual(len(d["offers"]), 1)
        self.assertEqual(d["log_total"], 3)
        segs = {g["segment"]: g["count"] for g in d["clients"]}
        self.assertEqual(segs["callback"], 1)
        self.assertEqual(d["days"][0]["calls"], 3)

    def test_bad_params_and_excel(self):
        self.assertEqual(self.api(self.manager).get(self.url, {"user": "x"}).status_code, 400)
        self.assertEqual(self.api(self.manager).get(self.url, {"user": 99999}).status_code, 404)
        lead = self.make_lead(owner=self.agent1, phone="555400003")
        Activity.objects.create(lead=lead, agent=self.agent1, outcome=Activity.Outcome.REFUSED, note="Za drogo")
        r = self.api(self.manager).get(self.url, {"user": self.agent1.pk, "file": "xlsx"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("praca-agent1-", r["Content-Disposition"])
        wb = load_workbook(BytesIO(r.content))
        self.assertEqual(wb.sheetnames, ["Podsumowanie", "Dni", "Sprzedaż", "Oferty", "Klienci teraz", "Rozmowy"])
        self.assertEqual(wb["Rozmowy"].cell(2, 6).value, "Za drogo")


class RemoveBatchTests(CrmTestBase):
    def test_removes_only_untouched_leads(self):
        batch = LeadBatch.objects.create(name="Baza testowa")
        fresh = self.make_lead(phone="555410001", batch=batch, company=Company.objects.create(name="Nowa firma"))
        worked = self.make_lead(owner=self.agent1, phone="555410002", batch=batch)
        Activity.objects.create(lead=worked, agent=self.agent1, outcome=Activity.Outcome.REFUSED)
        dialed = self.make_lead(owner=self.agent1, phone="555410003", batch=batch)
        CallRequest.objects.create(user=self.agent1, lead=dialed, phone=dialed.phone)

        ov = {b["id"]: b for b in self.api(self.manager).get("/api/batches/overview/").data}
        self.assertEqual((ov[batch.pk]["total"], ov[batch.pk]["untouched"]), (3, 1))
        self.assertEqual(self.api(self.agent1).get("/api/batches/overview/").status_code, 403)
        self.assertEqual(self.api(self.agent1).delete(f"/api/batches/{batch.pk}/").status_code, 403)

        r = self.api(self.manager).delete(f"/api/batches/{batch.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.data["removed"], r.data["kept"]), (1, 2))
        self.assertFalse(Lead.objects.filter(pk=fresh.pk).exists())
        self.assertFalse(Company.objects.filter(name="Nowa firma").exists())   # pusta firma posprzątana
        self.assertTrue(Lead.objects.filter(pk=worked.pk, batch=None).exists())
        self.assertTrue(Activity.objects.filter(lead=worked).exists())         # historia zostaje
        self.assertFalse(LeadBatch.objects.filter(pk=batch.pk).exists())


class RetryByAnswerHoursTests(CrmTestBase):
    def test_no_rates_without_enough_calls(self):
        self.assertIsNone(answer_rates_by_slot(get_rules()))

    def test_rates_by_slot(self):
        rules = get_rules()
        lead = self.make_lead(owner=self.agent1)
        starts = _slot_starts(rules)
        today = timezone.localtime().replace(second=0, microsecond=0)
        rows = []
        for slot, answered in ((0, 6), (1, 12), (2, 24)):
            moment = (today - timedelta(days=1)).replace(hour=(starts[slot] + 30) // 60, minute=(starts[slot] + 30) % 60)
            for i in range(ANSWER_STATS_MIN_CALLS):
                rows.append(Activity(lead=lead, agent=self.agent1,
                                     outcome=Activity.Outcome.REFUSED if i < answered else Activity.Outcome.NO_ANSWER))
        Activity.objects.bulk_create(rows)
        # created_at to auto_now_add – ustawiamy godziny po zapisie
        ids = list(Activity.objects.order_by("id").values_list("id", flat=True))
        for n, slot in enumerate((0, 1, 2)):
            moment = (today - timedelta(days=1)).replace(hour=(starts[slot] + 30) // 60, minute=(starts[slot] + 30) % 60)
            Activity.objects.filter(id__in=ids[n * ANSWER_STATS_MIN_CALLS:(n + 1) * ANSWER_STATS_MIN_CALLS]).update(created_at=moment)
        rates = answer_rates_by_slot(rules)
        self.assertEqual([round(r, 2) for r in rates], [0.2, 0.4, 0.8])

    def test_next_day_retry_prefers_best_slot(self):
        rules = get_rules()
        evening = datetime(2026, 10, 26, 21, 0, tzinfo=ZoneInfo("Europe/Warsaw"))   # poniedziałek wieczorem
        starts = _slot_starts(rules)
        plain = suggest_retry(rules, 0, evening)
        best = suggest_retry(rules, 0, evening, slot_rates=[0.2, 0.3, 0.7])
        self.assertEqual(plain.hour * 60 + plain.minute, starts[0] + 30)   # bez danych: rano (rotacja)
        self.assertEqual(best.hour * 60 + best.minute, starts[2] + 30)     # z danymi: pora, gdy odbierają najczęściej
        # pora już próbowana odpada – wtedy druga najlepsza
        tried = timezone.localtime(best)
        again = suggest_retry(rules, 1, evening + timedelta(days=1), [tried], slot_rates=[0.2, 0.3, 0.7])
        self.assertEqual(again.hour * 60 + again.minute, starts[1] + 30)


class TvChannelsTests(CrmTestBase):
    def test_l_has_all_channels_of_s_and_m(self):
        names = lambda plan: {c for _g, cs in all_groups(plan) for c in cs}  # noqa: E731
        self.assertIn("Nickelodeon", names("L"))       # z pakietu S
        self.assertIn("Disney Channel", names("L"))    # z pakietu M
        self.assertIn("Disney XD", names("L"))         # z pakietu L
        self.assertNotIn("Disney XD", names("M"))
        self.assertTrue(names("S") < names("M") < names("L"))
        groups = dict(all_groups("L"))
        self.assertEqual(groups["Dzieci"][:2], ["Nick Jr.", "Nickelodeon"])   # kolejność: S, potem M, potem L


class LastOfferOnListsTests(CrmTestBase):
    def test_callback_after_offer_shows_offer(self):
        lead = self.make_lead(owner=self.agent1, phone="555420001", status=Lead.Status.CALLBACK,
                              next_contact_at=timezone.now() + timedelta(minutes=5))
        Offer.objects.create(lead=lead, agent=self.agent1, product_name="M", monthly_total=Decimal("70"),
                             subject="Oferta", body="…")
        due = self.api(self.agent1).get("/api/my-leads/due/", {"minutes": 15}).data
        row = due["upcoming"][0]
        self.assertEqual((row["last_offer_name"], row["last_offer_monthly"]), ("M", "70.00"))
        self.assertTrue(row["last_offer_at"])
        mine = self.api(self.agent1).get("/api/my-leads/").data
        rows = mine["results"] if isinstance(mine, dict) else mine
        self.assertEqual(rows[0]["last_offer_name"], "M")
