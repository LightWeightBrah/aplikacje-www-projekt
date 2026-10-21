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


class ImportPageTests(CrmTestBase):
    def test_import_page_renders(self):
        r = self.client.get("/crm/import/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "import-form")
        self.assertContains(r, "CRM.initImport")

    def test_menu_links_to_import_page_for_managers_only(self):
        r = self.client.get("/crm/")
        self.assertContains(r, 'href="/crm/import/"')
        self.assertContains(r, "data-manager-only")

    def test_import_endpoint_used_by_page_is_manager_only(self):
        self.assertEqual(self.api().post("/api/batches/import/").status_code, 401)
        self.assertEqual(self.api(self.agent1).post("/api/batches/import/").status_code, 403)


class AgentListTests(CrmTestBase):
    def test_agents_endpoint_is_manager_only(self):
        self.assertEqual(self.api().get("/api/agents/").status_code, 401)
        self.assertEqual(self.api(self.agent1).get("/api/agents/").status_code, 403)
        self.assertEqual(self.api(self.manager).get("/api/agents/").status_code, 200)

    def test_agents_list_has_roles_and_active_lead_counts(self):
        self.make_lead(owner=self.agent1)
        self.make_lead(owner=self.agent1, phone="500100201", status="lost")
        rows = self.api(self.manager).get("/api/agents/").data
        by_name = {r["username"]: r for r in rows}
        self.assertEqual(by_name["agent1"]["active_leads"], 1)
        self.assertEqual(by_name["agent2"]["active_leads"], 0)
        self.assertEqual(by_name["manager1"]["role"], "manager")
        self.assertEqual(by_name["agent1"]["role"], "agent")
        self.assertNotIn("bezroli", by_name)


class LeadPanelApiTests(CrmTestBase):
    def test_lead_has_readable_names(self):
        from .models import Company, Product

        company = Company.objects.create(name="Alfa Sp. z o.o.", nip="5250000001")
        product = Product.objects.create(name="Abonament")
        self.make_lead(owner=self.agent1, company=company, product=product)
        row = self.api(self.manager).get("/api/leads/").data["results"][0]
        self.assertEqual(row["company_name"], "Alfa Sp. z o.o.")
        self.assertEqual(row["company_nip"], "5250000001")
        self.assertEqual(row["owner_name"], "agent1")
        self.assertEqual(row["product_name"], "Abonament")

    def test_names_are_null_for_pool_lead_without_company(self):
        self.make_lead()
        row = self.api(self.manager).get("/api/leads/").data["results"][0]
        self.assertIsNone(row["owner_name"])
        self.assertIsNone(row["company_name"])

    def test_pool_filter(self):
        self.make_lead()
        self.make_lead(owner=self.agent1, phone="500100201")
        client = self.api(self.manager)
        pool = client.get("/api/leads/?pool=true").data["results"]
        assigned = client.get("/api/leads/?pool=false").data["results"]
        self.assertEqual([l["owner"] for l in pool], [None])
        self.assertEqual([l["owner"] for l in assigned], [self.agent1.id])

    def test_ordering_by_name(self):
        self.make_lead(contact_name="Zofia", phone="500100201")
        self.make_lead(contact_name="Adam", phone="500100202")
        names = [
            l["contact_name"]
            for l in self.api(self.manager).get("/api/leads/?ordering=contact_name").data["results"]
        ]
        self.assertEqual(names, ["Adam", "Zofia"])


class ManagerPageTests(CrmTestBase):
    def test_manager_page_renders(self):
        r = self.client.get("/crm/manager/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "CRM.initManager")


class OverdueFilterTests(CrmTestBase):
    def test_overdue_filter_and_agent_counts(self):
        from datetime import timedelta

        from django.utils import timezone

        now = timezone.now()
        late = self.make_lead(owner=self.agent1, status="callback", phone="500100201")
        late.next_contact_at = now - timedelta(minutes=30)
        late.next_contact_note = "x"
        late.save()
        later = self.make_lead(owner=self.agent1, status="callback", phone="500100202")
        later.next_contact_at = now + timedelta(hours=2)
        later.next_contact_note = "x"
        later.save()
        closed = self.make_lead(owner=self.agent2, status="lost", phone="500100203")
        closed.next_contact_at = now - timedelta(days=1)
        closed.save()

        client = self.api(self.manager)
        ids = [l["id"] for l in client.get("/api/leads/?overdue=true").data["results"]]
        self.assertEqual(ids, [late.id])
        ids = [l["id"] for l in client.get(f"/api/leads/?overdue=true&owner={self.agent2.id}").data["results"]]
        self.assertEqual(ids, [])
        rows = {r["username"]: r for r in client.get("/api/agents/").data}
        self.assertEqual(rows["agent1"]["overdue_leads"], 1)
        self.assertEqual(rows["agent2"]["overdue_leads"], 0)


class TransferInfoTests(CrmTestBase):
    def test_agent_sees_who_the_lead_was_transferred_from(self):
        lead = self.make_lead(owner=self.agent1)
        self.api(self.manager).post(
            f"/api/leads/{lead.id}/assign/",
            {"agent": self.agent2.id, "reason": "urlop"},
            format="json",
        )
        client = self.api(self.agent2)
        row = client.get("/api/my-leads/").data["results"][0]
        self.assertEqual(row["transferred_from"], "agent1")
        detail = client.get(f"/api/leads/{lead.id}/").data
        self.assertEqual(detail["transferred_from"], "agent1")
        self.assertEqual(detail["assignments"][0]["from_agent_name"], "agent1")
        self.assertEqual(detail["assignments"][0]["reason"], "urlop")

    def test_lead_taken_from_pool_has_no_transfer(self):
        lead = self.make_lead()
        self.api(self.manager).post(
            f"/api/leads/{lead.id}/assign/", {"agent": self.agent1.id}, format="json"
        )
        row = self.api(self.agent1).get("/api/my-leads/").data["results"][0]
        self.assertIsNone(row["transferred_from"])
