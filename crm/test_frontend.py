from rest_framework import status

from .tests import CrmTestBase


class MeEndpointTests(CrmTestBase):
    def test_me_requires_login(self):
        self.assertEqual(self.api().get("/api/me/").status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_returns_role_for_agent_and_manager(self):
        agent = self.api(self.agent1).get("/api/me/").data
        manager = self.api(self.manager).get("/api/me/").data
        self.assertEqual(agent["role"], "agent")
        self.assertEqual(agent["username"], "agent1")
        self.assertEqual(manager["role"], "manager")

    def test_me_for_user_without_role(self):
        self.assertEqual(self.api(self.no_role).get("/api/me/").data["role"], "none")


class ActivityHistoryTests(CrmTestBase):
    def test_lead_detail_history_shows_who_talked(self):
        lead = self.make_lead(owner=self.agent1)
        self.api(self.agent1).post(
            "/api/activities/", {"lead": lead.id, "outcome": "no_answer"}, format="json"
        )
        history = self.api(self.agent1).get(f"/api/leads/{lead.id}/").data["activities"]
        self.assertEqual(history[0]["agent_name"], "agent1")
        self.assertEqual(history[0]["agent"], self.agent1.id)

    def test_interested_with_date_keeps_next_contact(self):
        from datetime import timedelta

        from django.utils import timezone

        lead = self.make_lead(owner=self.agent1)
        when = (timezone.now() + timedelta(days=2)).isoformat()
        response = self.api(self.agent1).post(
            "/api/activities/",
            {"lead": lead.id, "outcome": "interested", "next_contact_at": when,
             "next_contact_note": "ustalić warunki"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        lead.refresh_from_db()
        self.assertEqual(lead.status, "interested")
        self.assertIsNotNone(lead.next_contact_at)
        self.assertEqual(lead.next_contact_note, "ustalić warunki")


class FrontendPagesTests(CrmTestBase):
    def test_login_page_renders(self):
        r = self.client.get("/crm/login/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "login-form")

    def test_login_has_theme_button(self):
        self.assertContains(self.client.get("/crm/login/"), 'id="theme-btn"')

    def test_dashboard_has_user_menu_with_theme_choice(self):
        r = self.client.get("/crm/")
        self.assertContains(r, 'id="user-popover"')
        self.assertContains(r, 'data-theme-choice="auto"')

    def test_queue_page_renders(self):
        r = self.client.get("/crm/queue/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "claim-btn")

    def test_dashboard_shell_renders(self):
        r = self.client.get("/crm/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "CRM.initDashboard")
