"""Nieodebrany umówiony kontakt: lead jest śledzony, a nie zostaje zaległy w nieskończoność."""

from datetime import timedelta

from django.utils import timezone

from .models import Lead
from .tests import CrmTestBase


class MissedCallTests(CrmTestBase):
    def post(self, lead, outcome, **extra):
        body = {"lead": lead.pk, "outcome": outcome}
        body.update(extra)
        return self.api(self.agent1).post("/api/activities/", body, format="json")

    def appointment(self, minutes, status=Lead.Status.INTERESTED):
        when = timezone.now() + timedelta(minutes=minutes)
        return self.make_lead(
            owner=self.agent1,
            status=status,
            next_contact_at=when,
            agreed_contact_at=when,
            next_contact_note="Wycena",
        )

    def test_no_answer_after_agreed_time_counts_as_missed_and_schedules_retry(self):
        lead = self.appointment(-30)
        agreed = lead.agreed_contact_at
        r = self.post(lead, "no_answer")
        self.assertEqual(r.status_code, 201)
        lead.refresh_from_db()
        self.assertEqual(lead.missed_calls, 1)
        self.assertEqual(lead.agreed_contact_at, agreed)
        self.assertEqual(lead.status, Lead.Status.INTERESTED)
        self.assertEqual(lead.next_contact_note, "Wycena")
        self.assertGreater(lead.next_contact_at, timezone.now() + timedelta(minutes=55))

    def test_lead_is_not_overdue_after_missed_call_so_next_lead_can_be_taken(self):
        lead = self.appointment(-30)
        self.post(lead, "no_answer")
        self.make_lead(owner=None)
        r = self.api(self.agent1).post("/api/leads/next/")
        self.assertEqual(r.status_code, 200)

    def test_agent_can_choose_retry_time(self):
        lead = self.appointment(-10)
        when = timezone.now() + timedelta(days=1)
        self.post(lead, "no_answer", next_contact_at=when.isoformat())
        lead.refresh_from_db()
        self.assertEqual(lead.next_contact_at, when)
        self.assertEqual(lead.missed_calls, 1)

    def test_missed_calls_accumulate(self):
        lead = self.appointment(-30)
        self.post(lead, "no_answer")
        Lead.objects.filter(pk=lead.pk).update(
            next_contact_at=timezone.now() - timedelta(minutes=1)
        )
        self.post(lead, "no_answer")
        lead.refresh_from_db()
        self.assertEqual(lead.missed_calls, 2)

    def test_early_call_does_not_count_and_keeps_appointment(self):
        lead = self.appointment(180)
        original = lead.next_contact_at
        self.post(lead, "no_answer")
        lead.refresh_from_db()
        self.assertEqual(lead.missed_calls, 0)
        self.assertEqual(lead.next_contact_at, original)

    def test_no_answer_without_appointment_changes_nothing(self):
        lead = self.make_lead(owner=self.agent1)
        self.post(lead, "no_answer")
        lead.refresh_from_db()
        self.assertEqual(lead.missed_calls, 0)
        self.assertIsNone(lead.next_contact_at)
        self.assertIsNone(lead.agreed_contact_at)

    def test_conversation_resets_counter_and_stores_new_agreement(self):
        lead = self.appointment(-30)
        self.post(lead, "no_answer")
        when = timezone.now() + timedelta(days=2)
        self.post(lead, "callback", next_contact_at=when.isoformat(), next_contact_note="Decyzja")
        lead.refresh_from_db()
        self.assertEqual(lead.missed_calls, 0)
        self.assertEqual(lead.agreed_contact_at, when)

    def test_closing_the_lead_clears_tracking(self):
        lead = self.appointment(-30)
        self.post(lead, "no_answer")
        self.post(lead, "refused")
        lead.refresh_from_db()
        self.assertEqual(lead.missed_calls, 0)
        self.assertIsNone(lead.agreed_contact_at)
        self.assertIsNone(lead.next_contact_at)

    def test_api_exposes_fields_read_only(self):
        lead = self.appointment(-30)
        self.post(lead, "no_answer")
        r = self.api(self.agent1).get(f"/api/leads/{lead.pk}/")
        self.assertEqual(r.data["missed_calls"], 1)
        self.assertIn("agreed_contact_at", r.data)
        r = self.api(self.manager).patch(
            f"/api/leads/{lead.pk}/", {"missed_calls": 9}, format="json"
        )
        lead.refresh_from_db()
        self.assertEqual(lead.missed_calls, 1)


class TopicTests(CrmTestBase):
    """Sprawa (next_contact_note) jest opcjonalna i zapamiętywana między rozmowami."""

    def post(self, lead, outcome, **extra):
        body = {"lead": lead.pk, "outcome": outcome}
        body.update(extra)
        return self.api(self.agent1).post("/api/activities/", body, format="json")

    def test_callback_without_topic_is_accepted(self):
        lead = self.make_lead(owner=self.agent1)
        when = timezone.now() + timedelta(days=1)
        r = self.post(lead, "callback", next_contact_at=when.isoformat())
        self.assertEqual(r.status_code, 201)
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.CALLBACK)
        self.assertEqual(lead.next_contact_note, "")

    def test_topic_is_kept_when_new_term_has_no_topic_field(self):
        lead = self.make_lead(owner=self.agent1, next_contact_note="Wycena")
        when = timezone.now() + timedelta(days=2)
        self.post(lead, "callback", next_contact_at=when.isoformat())
        lead.refresh_from_db()
        self.assertEqual(lead.next_contact_note, "Wycena")
        self.assertEqual(lead.next_contact_at, when)

    def test_empty_topic_clears_it_and_new_topic_replaces_it(self):
        lead = self.make_lead(owner=self.agent1, next_contact_note="Wycena")
        when = timezone.now() + timedelta(days=2)
        self.post(lead, "callback", next_contact_at=when.isoformat(), next_contact_note="")
        lead.refresh_from_db()
        self.assertEqual(lead.next_contact_note, "")
        self.post(lead, "interested", next_contact_at=when.isoformat(), next_contact_note="Oferta")
        lead.refresh_from_db()
        self.assertEqual(lead.next_contact_note, "Oferta")


class ActivityTimelineTests(CrmTestBase):
    """Historia pokazuje, na kiedy był umówiony kontakt i na kiedy ustawiono następny."""

    def post(self, lead, outcome, **extra):
        body = {"lead": lead.pk, "outcome": outcome}
        body.update(extra)
        return self.api(self.agent1).post("/api/activities/", body, format="json")

    def test_missed_call_records_planned_time_and_automatic_retry(self):
        planned = timezone.now() - timedelta(minutes=10)
        lead = self.make_lead(owner=self.agent1, status=Lead.Status.INTERESTED, next_contact_at=planned)
        r = self.post(lead, "no_answer")
        self.assertEqual(r.status_code, 201)
        activity = lead.activities.get()
        self.assertEqual(activity.planned_at, planned)
        lead.refresh_from_db()
        self.assertEqual(activity.rescheduled_to, lead.next_contact_at)
        self.assertGreater(activity.rescheduled_to, timezone.now() + timedelta(minutes=55))

    def test_explicit_new_term_is_recorded(self):
        planned = timezone.now() - timedelta(minutes=10)
        lead = self.make_lead(owner=self.agent1, next_contact_at=planned)
        when = timezone.now() + timedelta(days=1)
        self.post(lead, "no_answer", next_contact_at=when.isoformat())
        activity = lead.activities.get()
        self.assertEqual(activity.planned_at, planned)
        self.assertEqual(activity.rescheduled_to, when)

    def test_notes_and_leads_without_term_have_no_timeline(self):
        lead = self.make_lead(owner=self.agent1, next_contact_at=timezone.now() + timedelta(days=1))
        self.post(lead, "note", note="Tylko notatka")
        self.assertIsNone(lead.activities.get().planned_at)
        other = self.make_lead(owner=self.agent1)
        self.post(other, "no_answer")
        act = other.activities.get()
        self.assertIsNone(act.planned_at)
        self.assertIsNone(act.rescheduled_to)

    def test_api_exposes_timeline_read_only(self):
        planned = timezone.now() - timedelta(minutes=10)
        lead = self.make_lead(owner=self.agent1, next_contact_at=planned)
        self.post(lead, "no_answer")
        r = self.api(self.agent1).get(f"/api/leads/{lead.pk}/")
        item = r.data["activities"][0]
        self.assertIsNotNone(item["planned_at"])
        self.assertIsNotNone(item["rescheduled_to"])
        r = self.post(lead, "no_answer", planned_at="2020-01-01T00:00:00Z")
        self.assertNotEqual(lead.activities.first().planned_at.year, 2020)
