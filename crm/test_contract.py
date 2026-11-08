"""Koniec umowy u obecnego operatora (telefon w dniu, od którego wolno przenieść numery)
i cel minut rozmów dla całego zespołu."""

from datetime import date, timedelta

from django.utils import timezone
from rest_framework import status

from .models import Lead, porting_from
from .tests import CrmTestBase
from .work import goal_per_hour


def months_ahead(n):
    today = timezone.localdate()
    idx = today.year * 12 + today.month - 1 + n
    return date(idx // 12, idx % 12 + 1, 1)


class PortingDateTests(CrmTestBase):
    def test_porting_starts_twelve_months_before_end(self):
        # umowa do marca 2027 (do 31.03.2027): od 1.04.2026 zostaje mniej niż 12 miesięcy
        self.assertEqual(porting_from(date(2027, 3, 1)), date(2026, 4, 1))
        self.assertEqual(porting_from(date(2027, 1, 1)), date(2026, 2, 1))
        self.assertEqual(porting_from(date(2027, 12, 1)), date(2027, 1, 1))
        self.assertIsNone(porting_from(None))


class ContractEndTests(CrmTestBase):
    def setUp(self):
        super().setUp()
        self.lead = self.make_lead(owner=self.agent1, phone="555200001")

    def patch(self, body):
        return self.api(self.agent1).patch(f"/api/leads/{self.lead.id}/", body, format="json")

    def refuse(self, note=""):
        return self.api(self.agent1).post("/api/activities/", {"lead": self.lead.id, "outcome": "refused", "note": note}, format="json")

    def test_agent_saves_month_and_open_ended_clears_it(self):
        r = self.patch({"contract_end": "2027-03-17"})
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assertEqual(r.data["contract_end"], "2027-03-01")   # zapisujemy sam miesiąc
        self.assertEqual(r.data["porting_from"], "2026-04-01")
        r = self.patch({"contract_open_ended": True})
        self.assertIsNone(r.data["contract_end"])
        self.assertIsNone(r.data["porting_from"])
        r = self.patch({"contract_end": "2027-05-01"})
        self.assertFalse(r.data["contract_open_ended"])

    def test_refusal_with_future_porting_date_becomes_callback(self):
        end = months_ahead(20)
        self.patch({"contract_end": end.isoformat()})
        self.assertEqual(self.refuse("Ma umowę w innej sieci").status_code, status.HTTP_201_CREATED)
        lead = Lead.objects.get(pk=self.lead.pk)
        self.assertEqual(lead.status, Lead.Status.CALLBACK)
        self.assertEqual(lead.owner, self.agent1)
        when = timezone.localtime(lead.next_contact_at)
        self.assertGreaterEqual(when.date(), porting_from(end))
        self.assertLess(when.date(), porting_from(end) + timedelta(days=7))   # najbliższy dzień pracy
        self.assertEqual(when.hour, 10)
        self.assertIn(f"Koniec umowy {end.month:02d}.{end.year}", lead.next_contact_note)

    def test_refusal_closes_when_porting_possible_now_or_unknown(self):
        self.patch({"contract_end": months_ahead(5).isoformat()})   # mniej niż 12 miesięcy: można już teraz
        self.refuse()
        self.assertEqual(Lead.objects.get(pk=self.lead.pk).status, Lead.Status.LOST)
        other = self.make_lead(owner=self.agent1, phone="555200002")
        self.api(self.agent1).post("/api/activities/", {"lead": other.id, "outcome": "refused"}, format="json")
        self.assertEqual(Lead.objects.get(pk=other.pk).status, Lead.Status.LOST)

    def test_refusal_asking_for_no_contact_still_closes(self):
        self.patch({"contract_end": months_ahead(24).isoformat()})
        self.refuse("Prosi o brak kontaktu")
        self.assertEqual(Lead.objects.get(pk=self.lead.pk).status, Lead.Status.LOST)

    def test_other_outcomes_keep_agents_own_date(self):
        self.patch({"contract_end": months_ahead(24).isoformat()})
        at = timezone.now() + timedelta(days=2)
        self.api(self.agent1).post("/api/activities/", {"lead": self.lead.id, "outcome": "callback",
                                                        "next_contact_at": at.isoformat()}, format="json")
        lead = Lead.objects.get(pk=self.lead.pk)
        self.assertEqual(lead.status, Lead.Status.CALLBACK)
        self.assertLess(abs((lead.next_contact_at - at).total_seconds()), 2)


class TeamGoalTests(CrmTestBase):
    def test_team_goal_applies_unless_agent_has_own(self):
        self.assertEqual(goal_per_hour(self.agent1), 15)
        r = self.api(self.manager).patch("/api/settings/", {"goal_minutes_per_hour": 20}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assertEqual(goal_per_hour(self.agent1), 20)
        self.assertEqual(self.api(self.agent1).get("/api/work/summary/").data["today"]["goal_minutes_per_hour"], 20)
        own = self.api(self.manager).patch(f"/api/users/{self.agent1.id}/", {"goal_minutes_per_hour": 25}, format="json")
        self.assertEqual(own.data["own_goal_minutes_per_hour"], 25)
        self.assertEqual(goal_per_hour(self.agent1), 25)
        self.assertEqual(goal_per_hour(self.agent2), 20)
        back = self.api(self.manager).patch(f"/api/users/{self.agent1.id}/", {"goal_minutes_per_hour": None}, format="json")
        self.assertEqual(back.status_code, status.HTTP_200_OK, back.data)
        self.assertIsNone(back.data["own_goal_minutes_per_hour"])
        self.assertEqual(goal_per_hour(self.agent1), 20)

    def test_team_goal_limits_and_only_manager(self):
        self.assertEqual(self.api(self.manager).patch("/api/settings/", {"goal_minutes_per_hour": 0}, format="json").status_code,
                         status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.api(self.manager).patch("/api/settings/", {"goal_minutes_per_hour": 61}, format="json").status_code,
                         status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.api(self.agent1).patch("/api/settings/", {"goal_minutes_per_hour": 5}, format="json").status_code,
                         status.HTTP_403_FORBIDDEN)
