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


class FrontendPagesTests(CrmTestBase):
    def test_login_page_renders(self):
        r = self.client.get("/crm/login/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "login-form")

    def test_dashboard_shell_renders(self):
        r = self.client.get("/crm/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "CRM.initDashboard")
