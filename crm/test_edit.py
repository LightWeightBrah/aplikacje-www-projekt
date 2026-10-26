"""Poprawianie komentarza w zapisanym kontakcie i sprawy leada oraz elementy nowej karty kolejki."""

from datetime import timedelta

from django.utils import timezone

from .models import Activity, Lead
from .tests import CrmTestBase


class EditActivityNoteTests(CrmTestBase):
    def make_activity(self, agent=None, **kwargs):
        lead = self.make_lead(owner=agent or self.agent1)
        return Activity.objects.create(lead=lead, agent=agent or self.agent1, outcome=Activity.Outcome.NO_ANSWER,
                                       note=kwargs.get("note", "stara notatka"))

    def patch(self, activity, body, user=None):
        return self.api(user or self.agent1).patch(f"/api/activities/{activity.pk}/", body, format="json")

    def test_author_can_fix_note_and_nothing_else_changes(self):
        act = self.make_activity()
        lead = act.lead
        before = (lead.status, lead.attempts_count, lead.next_contact_at)
        response = self.patch(act, {"note": "poprawiona notatka", "outcome": "sold"})
        self.assertEqual(response.status_code, 200)
        act.refresh_from_db(); lead.refresh_from_db()
        self.assertEqual(act.note, "poprawiona notatka")
        self.assertEqual(act.outcome, Activity.Outcome.NO_ANSWER)          # wyniku nie da się zmienić
        self.assertEqual((lead.status, lead.attempts_count, lead.next_contact_at), before)

    def test_note_can_be_added_to_entry_without_comment(self):
        act = self.make_activity(note="")
        self.assertEqual(self.patch(act, {"note": "  dopisane później  "}).status_code, 200)
        act.refresh_from_db()
        self.assertEqual(act.note, "dopisane później")

    def test_other_agent_cannot_edit(self):
        act = self.make_activity()
        self.assertEqual(self.patch(act, {"note": "włamanie"}, user=self.agent2).status_code, 404)
        act.refresh_from_db()
        self.assertEqual(act.note, "stara notatka")

    def test_manager_can_edit_and_anonymous_cannot(self):
        act = self.make_activity()
        self.assertEqual(self.patch(act, {"note": "od managera"}, user=self.manager).status_code, 200)
        self.assertEqual(self.client.patch(f"/api/activities/{act.pk}/", {"note": "x"}, content_type="application/json").status_code, 401)

    def test_note_is_required_and_has_a_length_limit(self):
        act = self.make_activity()
        self.assertEqual(self.patch(act, {}).status_code, 400)
        self.assertEqual(self.patch(act, {"note": "x" * 2001}).status_code, 400)

    def test_put_and_delete_are_still_not_allowed(self):
        act = self.make_activity()
        api = self.api(self.agent1)
        self.assertEqual(api.put(f"/api/activities/{act.pk}/", {"note": "x"}, format="json").status_code, 405)
        self.assertEqual(api.delete(f"/api/activities/{act.pk}/").status_code, 405)

    def test_author_can_edit_after_lead_returned_to_pool(self):
        act = self.make_activity()
        Lead.objects.filter(pk=act.lead_id).update(owner=None)
        self.assertEqual(self.patch(act, {"note": "po zwrocie do puli"}).status_code, 200)


class EditCaseTests(CrmTestBase):
    def test_owner_can_change_case_without_touching_term_or_status(self):
        when = timezone.now() + timedelta(days=1)
        lead = self.make_lead(owner=self.agent1, status=Lead.Status.CALLBACK, next_contact_at=when, next_contact_note="stara sprawa")
        response = self.api(self.agent1).patch(f"/api/leads/{lead.pk}/", {"next_contact_note": "nowa sprawa"}, format="json")
        self.assertEqual(response.status_code, 200)
        lead.refresh_from_db()
        self.assertEqual(lead.next_contact_note, "nowa sprawa")
        self.assertEqual(lead.status, Lead.Status.CALLBACK)
        self.assertEqual(lead.next_contact_at, when)

    def test_case_can_be_cleared(self):
        lead = self.make_lead(owner=self.agent1, next_contact_note="do usunięcia")
        self.assertEqual(self.api(self.agent1).patch(f"/api/leads/{lead.pk}/", {"next_contact_note": ""}, format="json").status_code, 200)
        lead.refresh_from_db()
        self.assertEqual(lead.next_contact_note, "")

    def test_other_agent_cannot_change_case(self):
        lead = self.make_lead(owner=self.agent1, next_contact_note="moja")
        self.assertIn(self.api(self.agent2).patch(f"/api/leads/{lead.pk}/", {"next_contact_note": "cudza"}, format="json").status_code, (403, 404))
        lead.refresh_from_db()
        self.assertEqual(lead.next_contact_note, "moja")


class QueuePageLayoutTests(CrmTestBase):
    def test_queue_page_uses_current_static_version(self):
        html = self.client.get("/crm/queue/").content.decode()
        self.assertIn("crm/js/queue.js?v=59", html)
