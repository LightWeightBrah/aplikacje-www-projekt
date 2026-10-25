"""Cofnięcie ostatniego zapisu zaraz po pomyłce (POST /api/activities/{id}/undo/)."""

from datetime import timedelta

from django.utils import timezone

from .models import Activity, Lead, LeadAssignment
from .rules import get_rules
from .tests import CrmTestBase


class UndoActivityTests(CrmTestBase):
    def post(self, lead, outcome, user=None, **extra):
        body = {"lead": lead.pk, "outcome": outcome, **extra}
        return self.api(user or self.agent1).post("/api/activities/", body, format="json")

    def undo(self, activity_id, user=None):
        return self.api(user or self.agent1).post(f"/api/activities/{activity_id}/undo/")

    def test_undo_refusal_reopens_lead_and_removes_history_entry(self):
        when = timezone.now() + timedelta(days=1)
        lead = self.make_lead(owner=self.agent1, status=Lead.Status.CALLBACK, next_contact_at=when,
                              next_contact_note="oferta", attempts_count=2)
        act = self.post(lead, "refused").data
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.LOST)
        self.assertIsNone(lead.next_contact_at)

        response = self.undo(act["id"])
        self.assertEqual(response.status_code, 200)
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.CALLBACK)
        self.assertEqual(lead.next_contact_at, when)
        self.assertEqual(lead.next_contact_note, "oferta")
        self.assertEqual(lead.attempts_count, 2)
        self.assertEqual(lead.owner_id, self.agent1.pk)
        self.assertFalse(Activity.objects.filter(pk=act["id"]).exists())

    def test_undo_no_answer_restores_retry_state(self):
        lead = self.make_lead(owner=self.agent1)
        act = self.post(lead, "no_answer").data
        lead.refresh_from_db()
        self.assertEqual(lead.attempts_count, 1)
        self.assertIsNotNone(lead.next_contact_at)
        self.assertEqual(self.undo(act["id"]).status_code, 200)
        lead.refresh_from_db()
        self.assertEqual(lead.attempts_count, 0)
        self.assertIsNone(lead.next_contact_at)
        self.assertIsNone(lead.last_activity_at)

    def test_undo_returns_lead_taken_back_to_pool_after_last_try(self):
        rules = get_rules(); rules.max_attempts_per_agent = 2; rules.save()
        lead = self.make_lead(owner=self.agent1)
        self.post(lead, "no_answer")
        act = self.post(lead, "no_answer").data        # druga próba z dwóch: lead wraca do puli
        lead.refresh_from_db()
        self.assertIsNone(lead.owner_id)
        self.assertTrue(LeadAssignment.objects.filter(lead=lead, to_agent__isnull=True).exists())

        self.assertEqual(self.undo(act["id"]).status_code, 200)
        lead.refresh_from_db()
        self.assertEqual(lead.owner_id, self.agent1.pk)
        self.assertEqual(lead.attempts_count, 1)
        self.assertFalse(LeadAssignment.objects.filter(lead=lead, to_agent__isnull=True).exists())

    def test_cannot_undo_when_someone_else_took_the_lead(self):
        rules = get_rules(); rules.max_attempts_per_agent = 1; rules.save()
        lead = self.make_lead(owner=self.agent1)
        act = self.post(lead, "no_answer").data        # jedyna próba: lead w puli
        Lead.objects.filter(pk=lead.pk).update(owner=self.agent2)
        self.assertEqual(self.undo(act["id"]).status_code, 409)

    def test_cannot_undo_after_time_window(self):
        lead = self.make_lead(owner=self.agent1)
        act = self.post(lead, "callback", next_contact_at=(timezone.now() + timedelta(days=1)).isoformat()).data
        Activity.objects.filter(pk=act["id"]).update(created_at=timezone.now() - timedelta(minutes=2))
        response = self.undo(act["id"])
        self.assertEqual(response.status_code, 409)
        self.assertTrue(Activity.objects.filter(pk=act["id"]).exists())

    def test_cannot_undo_when_newer_entry_exists(self):
        lead = self.make_lead(owner=self.agent1)
        first = self.post(lead, "no_answer").data
        self.post(lead, "note", note="coś jeszcze")
        self.assertEqual(self.undo(first["id"]).status_code, 409)

    def test_other_agent_cannot_undo(self):
        lead = self.make_lead(owner=self.agent1)
        act = self.post(lead, "no_answer").data
        self.assertIn(self.undo(act["id"], user=self.agent2).status_code, (403, 404))
        self.assertTrue(Activity.objects.filter(pk=act["id"]).exists())

    def test_undo_state_is_not_exposed_in_api(self):
        lead = self.make_lead(owner=self.agent1)
        act = self.post(lead, "no_answer").data
        self.assertNotIn("undo_state", act)


class LastContactAndSummaryTests(CrmTestBase):
    def test_lead_list_shows_last_contact_without_notes(self):
        lead = self.make_lead(owner=self.agent1)
        api = self.api(self.agent1)
        api.post("/api/activities/", {"lead": lead.pk, "outcome": "no_answer"}, format="json")
        api.post("/api/activities/", {"lead": lead.pk, "outcome": "note", "note": "tylko notatka"}, format="json")
        row = api.get("/api/my-leads/").json()["results"][0]
        self.assertEqual(row["last_contact_outcome"], "no_answer")
        self.assertEqual(row["last_contact_agent"], self.agent1.pk)
        self.assertIsNotNone(row["last_contact_at"])

    def test_lead_without_contact_has_empty_last_contact(self):
        self.make_lead(owner=self.agent1)
        row = self.api(self.agent1).get("/api/my-leads/").json()["results"][0]
        self.assertIsNone(row["last_contact_outcome"])

    def test_day_summary_counts_calls_overdue_and_remaining(self):
        api = self.api(self.agent1)
        now = timezone.now()
        a = self.make_lead(owner=self.agent1, contact_name="A", status=Lead.Status.CALLBACK,
                           next_contact_at=now - timedelta(hours=2))
        self.make_lead(owner=self.agent1, contact_name="B", status=Lead.Status.CALLBACK,
                       next_contact_at=now + timedelta(minutes=30))
        api.post("/api/activities/", {"lead": a.pk, "outcome": "interested",
                                      "next_contact_at": (now + timedelta(days=3)).isoformat()}, format="json")
        api.post("/api/activities/", {"lead": a.pk, "outcome": "note", "note": "x"}, format="json")
        data = api.get("/api/my-leads/summary/").json()
        self.assertEqual(data["done_today"], 1)          # notatka się nie liczy
        self.assertEqual(data["talked_today"], 1)
        self.assertEqual(data["overdue"], 0)             # A dostał nowy termin
        self.assertEqual(data["active"], 2)


class AgentUiPiecesTests(CrmTestBase):
    def test_queue_and_plan_pages_load_shared_contact_helpers(self):
        for url in ("/crm/queue/", "/crm/plan/"):
            html = self.client.get(url).content.decode()
            self.assertIn("crm/js/contact.js", html, url)
        self.assertIn('id="day-bar"', self.client.get("/crm/queue/").content.decode())


class DaySummaryRetriesTests(CrmTestBase):
    def test_overdue_retry_is_counted_separately_from_overdue_callback(self):
        now = timezone.now()
        self.make_lead(owner=self.agent1, contact_name="Obiecany", status=Lead.Status.CALLBACK,
                       next_contact_at=now - timedelta(hours=1))
        self.make_lead(owner=self.agent1, contact_name="Ponowna próba", status=Lead.Status.NEW,
                       next_contact_at=now - timedelta(hours=3))
        data = self.api(self.agent1).get("/api/my-leads/summary/").json()
        self.assertEqual(data["overdue"], 1)
        self.assertEqual(data["retries_due"], 1)
