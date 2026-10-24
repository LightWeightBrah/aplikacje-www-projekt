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


class AvatarTests(CrmTestBase):
    # 1x1 piksel PNG
    PNG = (
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )

    def test_avatar_requires_login(self):
        r = self.api().post("/api/me/avatar/", {"image": self.PNG}, format="json")
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_has_empty_avatar_by_default(self):
        self.assertEqual(self.api(self.agent1).get("/api/me/").data["avatar"], "")

    def test_agent_can_set_and_remove_avatar(self):
        api = self.api(self.agent1)
        r = api.post("/api/me/avatar/", {"image": self.PNG}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(api.get("/api/me/").data["avatar"], self.PNG)
        self.assertEqual(api.delete("/api/me/avatar/").status_code, 204)
        self.assertEqual(api.get("/api/me/").data["avatar"], "")

    def test_manager_can_set_avatar(self):
        r = self.api(self.manager).post("/api/me/avatar/", {"image": self.PNG}, format="json")
        self.assertEqual(r.status_code, 200)

    def test_avatar_does_not_change_team_access_flag(self):
        self.api(self.agent1).post("/api/me/avatar/", {"image": self.PNG}, format="json")
        self.assertTrue(self.api(self.agent1).get("/api/me/").data["can_view_team"])

    def test_avatar_is_per_user(self):
        self.api(self.agent1).post("/api/me/avatar/", {"image": self.PNG}, format="json")
        self.assertEqual(self.api(self.agent2).get("/api/me/").data["avatar"], "")

    def test_avatar_rejects_wrong_type(self):
        for bad in ["data:text/html;base64,PHNjcmlwdD4=", "http://x/y.png", "", None, 5]:
            r = self.api(self.agent1).post("/api/me/avatar/", {"image": bad}, format="json")
            self.assertEqual(r.status_code, 400, bad)

    def test_avatar_rejects_fake_image_bytes(self):
        import base64

        fake = "data:image/png;base64," + base64.b64encode(b"to nie jest obrazek").decode()
        r = self.api(self.agent1).post("/api/me/avatar/", {"image": fake}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_avatar_rejects_too_big_file(self):
        import base64

        big = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 200_000).decode()
        r = self.api(self.agent1).post("/api/me/avatar/", {"image": big}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_user_menu_has_avatar_controls(self):
        r = self.client.get("/crm/")
        self.assertContains(r, 'id="avatar-change"')
        self.assertContains(r, 'id="avatar-file"')


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


class DashboardPeriodTests(CrmTestBase):
    def test_dashboard_has_today_and_custom_range(self):
        r = self.client.get("/crm/")
        self.assertContains(r, 'value="1"')
        self.assertContains(r, 'id="date-from"')
        self.assertContains(r, 'id="date-to"')
        self.assertContains(r, 'id="outcomes"')
        self.assertContains(r, 'id="scheduled"')


    def test_dashboard_defaults_to_today(self):
        r = self.client.get("/crm/")
        self.assertContains(r, '<option value="1" selected>Dziś</option>')
        self.assertNotContains(r, '<option value="7" selected>')

    def test_dashboard_uses_plain_language_titles(self):
        r = self.client.get("/crm/")
        self.assertContains(r, "Wyniki rozmów")
        self.assertContains(r, "Moi klienci")
        self.assertContains(r, "Wyniki agentów")
        self.assertNotContains(r, "Lejek leadów")
        self.assertNotContains(r, "Jak poszły rozmowy")


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


class RankingPageTests(CrmTestBase):
    def test_ranking_page_renders(self):
        r = self.client.get("/crm/ranking/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'id="rank-table"')
        self.assertContains(r, 'id="access-card"')
        self.assertContains(r, "CRM.initRanking")
        self.assertContains(r, '<option value="1" selected>Dziś</option>')

    def test_menu_has_ranking_link_hidden_for_blocked_agents(self):
        r = self.client.get("/crm/")
        self.assertContains(r, 'href="/crm/ranking/"')
        self.assertContains(r, "data-team-only")

    def test_blocked_agent_gets_403_from_api_used_by_page(self):
        from .models import AgentProfile

        AgentProfile.objects.create(user=self.agent1, can_view_team_stats=False)
        r = self.api(self.agent1).get("/api/leaderboard/?days=1")
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.api(self.agent2).get("/api/leaderboard/?days=1").status_code, 200)


class UsersPageTests(CrmTestBase):
    def test_users_page_renders(self):
        r = self.client.get("/crm/users/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "users-table")
        self.assertContains(r, "CRM.initUsers")

    def test_menu_links_to_users_page_for_managers_only(self):
        r = self.client.get("/crm/")
        self.assertContains(r, 'href="/crm/users/" data-manager-only')

    def test_users_api_used_by_page_is_manager_only(self):
        self.assertEqual(self.api().get("/api/users/").status_code, 401)
        self.assertEqual(self.api(self.agent1).get("/api/users/").status_code, 403)


class ManagerScopeTests(CrmTestBase):
    """Manager może na dashboardzie przełączyć się z całego zespołu na własną pracę."""

    def setUp(self):
        super().setUp()
        from .models import Activity, Lead

        self.mine = Lead.objects.create(contact_name="Moj", phone="500100900", owner=self.manager)
        theirs = Lead.objects.create(contact_name="Cudzy", phone="500100901", owner=self.agent1)
        Lead.objects.create(contact_name="Pula", phone="500100902")
        Activity.objects.create(lead=self.mine, agent=self.manager, outcome="interested")
        Activity.objects.create(lead=theirs, agent=self.agent1, outcome="sold")
        Activity.objects.create(lead=theirs, agent=self.agent1, outcome="sold")

    def test_dashboard_has_scope_switch(self):
        self.assertContains(self.client.get("/crm/"), 'id="scope-tabs"')

    def test_default_scope_is_whole_team(self):
        d = self.api(self.manager).get("/api/stats/?days=1").data
        self.assertEqual(d["outcomes"]["sold"], 2)
        self.assertEqual(d["pool_leads"], 1)
        self.assertEqual(d["total_leads"], 3)

    def test_me_scope_shows_only_managers_own_work(self):
        d = self.api(self.manager).get("/api/stats/?days=1&scope=me").data
        self.assertEqual(d["outcomes"]["sold"], 0)
        self.assertEqual(d["outcomes"]["interested"], 1)
        self.assertEqual(d["total_leads"], 1)
        self.assertIsNone(d["pool_leads"])
        self.assertEqual([a["username"] for a in d["agents"]], ["manager1"])

    def test_team_scope_explicit_equals_default(self):
        api = self.api(self.manager)
        self.assertEqual(
            api.get("/api/stats/?days=1&scope=team").data["outcomes"],
            api.get("/api/stats/?days=1").data["outcomes"],
        )

    def test_agent_cannot_widen_scope(self):
        d = self.api(self.agent1).get("/api/stats/?days=1&scope=team").data
        self.assertEqual(d["outcomes"]["sold"], 2)
        self.assertIsNone(d["pool_leads"])
        self.assertEqual(d["total_leads"], 1)

    def test_invalid_scope(self):
        self.assertEqual(self.api(self.manager).get("/api/stats/?days=1&scope=x").status_code, 400)


class PlanPageTests(CrmTestBase):
    def test_plan_page_renders(self):
        r = self.client.get("/crm/plan/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "plan-list")
        self.assertContains(r, "CRM.initPlan")

    def test_menu_links_to_plan_for_everyone(self):
        r = self.client.get("/crm/")
        self.assertContains(r, 'href="/crm/plan/"')
        self.assertNotContains(r, 'href="/crm/plan/" data-manager-only')

    def test_queue_page_supports_lead_link(self):
        self.assertContains(self.client.get("/crm/queue/?lead=1"), "queue.js")

    def test_schedule_and_calendar_need_login(self):
        self.assertEqual(self.api().get("/api/my-leads/schedule/").status_code, 401)
        self.assertEqual(self.api().get("/api/my-leads/calendar/").status_code, 401)


class ReminderShellTests(CrmTestBase):
    def test_shell_has_reminder_elements(self):
        for url in ("/crm/", "/crm/queue/", "/crm/plan/"):
            r = self.client.get(url)
            self.assertContains(r, 'id="reminders"', msg_prefix=url)
            self.assertContains(r, 'id="plan-badge"', msg_prefix=url)
            self.assertContains(r, 'id="notif-btn"', msg_prefix=url)
            self.assertContains(r, "CRM.initReminders()", msg_prefix=url)

    def test_reminders_script_is_served(self):
        self.assertContains(self.client.get("/crm/"), "crm/js/reminders.js")

    def test_due_window_of_five_minutes_used_by_reminders(self):
        from datetime import timedelta

        from django.utils import timezone

        now = timezone.now()
        soon = self.make_lead(owner=self.agent1, next_contact_at=now + timedelta(minutes=3))
        late = self.make_lead(owner=self.agent1, next_contact_at=now - timedelta(minutes=20))
        self.make_lead(owner=self.agent1, next_contact_at=now + timedelta(minutes=30))
        r = self.api(self.agent1).get("/api/my-leads/due/", {"minutes": 5})
        self.assertEqual(r.status_code, 200)
        self.assertEqual([l["id"] for l in r.data["upcoming"]], [soon.id])
        self.assertEqual([l["id"] for l in r.data["overdue"]], [late.id])

    def test_due_needs_login(self):
        self.assertEqual(self.api().get("/api/my-leads/due/").status_code, 401)


class ReminderSettingsAndWorkerTests(CrmTestBase):
    def test_root_redirects_to_app(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/crm/")

    def test_service_worker_is_served_with_app_scope(self):
        r = self.client.get("/crm/sw.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("javascript", r["Content-Type"])
        self.assertEqual(r["Service-Worker-Allowed"], "/crm/")
        self.assertIn("notificationclick", r.content.decode())

    def test_settings_dialog_is_in_the_shell(self):
        r = self.client.get("/crm/")
        for element in ("notif-open", "notif-dialog", "notif-enabled", "notif-min", "notif-sec", "notif-sound"):
            self.assertContains(r, f'id="{element}"')

    def test_plan_page_has_clickable_rows_script(self):
        self.assertContains(self.client.get("/crm/plan/"), "crm/js/plan.js")


class SettingsPageTests(CrmTestBase):
    def test_settings_page_renders(self):
        r = self.client.get("/crm/settings/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "rules-form")
        self.assertContains(r, "CRM.initSettings")

    def test_menu_links_to_settings_for_managers_only(self):
        r = self.client.get("/crm/")
        self.assertContains(r, 'href="/crm/settings/"')
        self.assertContains(r, "data-manager-only")

    def test_settings_api_used_by_page_is_manager_only_for_writes(self):
        self.assertEqual(self.api(self.agent1).get("/api/settings/").status_code, 200)
        self.assertEqual(self.api(self.agent1).patch("/api/settings/", {}, format="json").status_code, 403)
        self.assertEqual(self.api(self.manager).patch("/api/settings/", {}, format="json").status_code, 200)
