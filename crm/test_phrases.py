"""Wynik rozmowy: gotowe przyciski (wzory komentarzy, powody odmowy) i wymagane powody – ustawia manager."""

from rest_framework import status

from .models import Activity
from .phrases import DEFAULT_PHRASES
from .rules import get_rules
from .tests import CrmTestBase


class QuickPhrasesTests(CrmTestBase):
    def patch(self, body, user=None):
        return self.api(user or self.manager).patch("/api/settings/", body, format="json")

    def test_defaults_for_everyone(self):
        data = self.api(self.agent1).get("/api/settings/").data
        self.assertEqual(data["quick_phrases"], DEFAULT_PHRASES)
        self.assertFalse(data["require_skip_reason"])
        self.assertFalse(data["require_refuse_reason"])

    def test_manager_adds_reason_and_list_is_cleaned(self):
        r = self.patch({"quick_phrases": {"refused": ["Za drogo", "  Ma  leasing  ", "za drogo", ""]}})
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assertEqual(r.data["quick_phrases"]["refused"], ["Za drogo", "Ma leasing"])
        # inne wyniki zostają domyślne, kolejny zapis jednej listy nie kasuje poprzedniej
        self.assertEqual(r.data["quick_phrases"]["callback"], DEFAULT_PHRASES["callback"])
        r = self.patch({"quick_phrases": {"callback": ["Na urlopie"]}})
        self.assertEqual(r.data["quick_phrases"]["refused"], ["Za drogo", "Ma leasing"])
        self.assertEqual(r.data["quick_phrases"]["callback"], ["Na urlopie"])

    def test_bad_lists_rejected(self):
        self.assertEqual(self.patch({"quick_phrases": {"xyz": ["a"]}}).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.patch({"quick_phrases": {"refused": "Za drogo"}}).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.patch({"quick_phrases": {"refused": ["x" * 61]}}).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.patch({"quick_phrases": {"refused": []}}, user=self.agent1).status_code, status.HTTP_403_FORBIDDEN)

    def test_reset_restores_defaults(self):
        self.patch({"quick_phrases": {"refused": ["Tylko to"]}, "require_skip_reason": True})
        r = self.api(self.manager).post("/api/settings/reset/")
        self.assertEqual(r.data["quick_phrases"], DEFAULT_PHRASES)
        self.assertFalse(r.data["require_skip_reason"])

    def test_refuse_reason_optional_unless_required(self):
        lead = self.make_lead(owner=self.agent1, phone="555400001")
        body = {"lead": lead.id, "outcome": "refused", "note": ""}
        r = self.api(self.agent1).post("/api/activities/", body, format="json")
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        rules = get_rules()
        rules.require_refuse_reason = True
        rules.save()
        lead2 = self.make_lead(owner=self.agent1, phone="555400002")
        r = self.api(self.agent1).post("/api/activities/", {**body, "lead": lead2.id}, format="json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        r = self.api(self.agent1).post("/api/activities/", {**body, "lead": lead2.id, "note": "Ma leasing"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)

    def test_manager_skip_reasons_decide_status(self):
        from .models import Lead
        self.patch({"quick_phrases": {"skip_never": ["Firma w likwidacji"], "skip_lost": ["Ma umowę do 2030"]}})
        cases = (("Firma w likwidacji", Lead.Status.DO_NOT_CALL), ("Ma umowę do 2030", Lead.Status.LOST),
                 ("closed", Lead.Status.DO_NOT_CALL))   # stary kod z wcześniejszych wersji dalej działa
        for i, (reason, expected) in enumerate(cases):
            lead = self.make_lead(owner=self.agent1, phone=f"55541000{i}")
            r = self.api(self.agent1).post("/api/activities/", {"lead": lead.id, "outcome": "skipped", "skip_reason": reason}, format="json")
            self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
            lead.refresh_from_db()
            self.assertEqual(lead.status, expected, reason)

    def test_report_counts_manager_reasons(self):
        self.patch({"quick_phrases": {"refused": ["Ma leasing"]}})
        lead = self.make_lead(owner=self.agent1, phone="555400003")
        Activity.objects.create(lead=lead, agent=self.agent1, outcome=Activity.Outcome.REFUSED, note="Ma leasing")
        rows = self.api(self.manager).get("/api/reports/").data["refusals"]["rows"]
        self.assertIn({"reason": "Ma leasing", "count": 1}, rows)
