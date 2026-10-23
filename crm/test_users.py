from django.contrib.auth import get_user_model
from rest_framework import status

from .models import Lead, LeadAssignment
from .tests import CrmTestBase

User = get_user_model()


class UsersBase(CrmTestBase):
    def token_login(self, username, password):
        return self.client.post(
            "/api/token/", {"username": username, "password": password}, format="json"
        )

    def create_agent(self, **extra):
        data = {"username": "nowy", "first_name": "Nowy", "last_name": "Agent"}
        data.update(extra)
        return self.api(self.manager).post("/api/users/", data, format="json")


class UserAccessTests(UsersBase):
    def test_requires_login(self):
        self.assertEqual(self.api().get("/api/users/").status_code, status.HTTP_401_UNAUTHORIZED)

    def test_agent_gets_403_everywhere(self):
        api = self.api(self.agent1)
        target = self.agent2.pk
        self.assertEqual(api.get("/api/users/").status_code, 403)
        self.assertEqual(api.post("/api/users/", {"username": "x"}, format="json").status_code, 403)
        self.assertEqual(api.patch(f"/api/users/{target}/", {"first_name": "X"}, format="json").status_code, 403)
        self.assertEqual(api.post(f"/api/users/{target}/reset-password/", {}, format="json").status_code, 403)
        self.assertEqual(api.post(f"/api/users/{target}/deactivate/", {}, format="json").status_code, 403)
        self.assertEqual(api.post(f"/api/users/{target}/activate/", {}, format="json").status_code, 403)

    def test_user_without_role_gets_403(self):
        self.assertEqual(self.api(self.no_role).get("/api/users/").status_code, 403)

    def test_list_shows_agents_and_managers_but_not_users_without_role(self):
        rows = self.api(self.manager).get("/api/users/").data
        names = {r["username"]: r for r in rows}
        self.assertEqual(set(names), {"agent1", "agent2", "manager1"})
        self.assertEqual(names["agent1"]["role"], "agent")
        self.assertEqual(names["manager1"]["role"], "manager")
        self.assertTrue(names["manager1"]["is_me"])
        self.assertFalse(names["agent1"]["is_me"])
        self.assertNotIn("password", names["agent1"])

    def test_list_counts_active_leads_and_shows_inactive_accounts(self):
        self.make_lead(owner=self.agent1)
        self.make_lead(owner=self.agent1, phone="500100201", status=Lead.Status.WON)
        self.agent2.is_active = False
        self.agent2.save()
        rows = {r["username"]: r for r in self.api(self.manager).get("/api/users/").data}
        self.assertEqual(rows["agent1"]["active_leads"], 1)
        self.assertFalse(rows["agent2"]["is_active"])


class UserCreateTests(UsersBase):
    def test_manager_creates_agent_with_generated_password(self):
        r = self.create_agent()
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["role"], "agent")
        self.assertEqual(r.data["full_name"], "Nowy Agent")
        password = r.data["password"]
        self.assertGreaterEqual(len(password), 10)
        self.assertEqual(self.token_login("nowy", password).status_code, 200)

    def test_new_agent_can_use_api_as_agent(self):
        password = self.create_agent().data["password"]
        token = self.token_login("nowy", password).data["access"]
        me = self.client.get("/api/me/", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(me.data["role"], "agent")
        self.assertEqual(self.client.get("/api/users/", HTTP_AUTHORIZATION=f"Bearer {token}").status_code, 403)

    def test_manager_sets_own_password(self):
        r = self.create_agent(password="Mocne-haslo-77")
        self.assertEqual(r.status_code, 201)
        self.assertIsNone(r.data["password"])
        self.assertEqual(self.token_login("nowy", "Mocne-haslo-77").status_code, 200)

    def test_simple_password_is_accepted(self):
        r = self.create_agent(password="123")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.token_login("nowy", "123").status_code, 200)

    def test_username_must_be_unique_ignoring_case(self):
        r = self.create_agent(username="AGENT1")
        self.assertEqual(r.status_code, 400)
        self.assertIn("username", r.data)

    def test_name_is_required(self):
        r = self.api(self.manager).post("/api/users/", {"username": "bezimienia"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("first_name", r.data)
        self.assertIn("last_name", r.data)

    def test_bad_username_characters(self):
        self.assertEqual(self.create_agent(username="ala ma kota").status_code, 400)

    def test_can_create_manager(self):
        r = self.create_agent(role="manager")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["role"], "manager")

    def test_invalid_role(self):
        self.assertEqual(self.create_agent(role="admin").status_code, 400)

    def test_manager_can_edit_names(self):
        r = self.api(self.manager).patch(
            f"/api/users/{self.agent1.pk}/", {"first_name": "Anna", "last_name": "Nowak"}, format="json"
        )
        self.assertEqual(r.status_code, 200)
        self.agent1.refresh_from_db()
        self.assertEqual(self.agent1.get_full_name(), "Anna Nowak")

    def test_edit_cannot_grant_superuser(self):
        self.api(self.manager).patch(
            f"/api/users/{self.agent1.pk}/", {"is_superuser": True, "is_staff": True}, format="json"
        )
        self.agent1.refresh_from_db()
        self.assertFalse(self.agent1.is_superuser)
        self.assertFalse(self.agent1.is_staff)

    def test_manager_changes_login_and_user_logs_in_with_new_one(self):
        r = self.api(self.manager).patch(f"/api/users/{self.agent1.pk}/", {"username": "ania.n"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["username"], "ania.n")
        self.assertEqual(self.token_login("agent1", "haslo12345").status_code, 401)
        self.assertEqual(self.token_login("ania.n", "haslo12345").status_code, 200)

    def test_login_must_stay_unique_ignoring_case(self):
        r = self.api(self.manager).patch(f"/api/users/{self.agent1.pk}/", {"username": "AGENT2"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("username", r.data)

    def test_keeping_own_login_in_edit_is_fine(self):
        r = self.api(self.manager).patch(
            f"/api/users/{self.agent1.pk}/", {"username": "agent1", "first_name": "Ala"}, format="json"
        )
        self.assertEqual(r.status_code, 200)

    def test_bad_login_characters_in_edit(self):
        r = self.api(self.manager).patch(f"/api/users/{self.agent1.pk}/", {"username": "a b"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_agent_promoted_to_manager_and_back(self):
        api = self.api(self.manager)
        r = api.patch(f"/api/users/{self.agent1.pk}/", {"role": "manager"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["role"], "manager")
        self.assertEqual(self.api(self.agent1).get("/api/users/").status_code, 200)
        self.assertEqual(self.api(self.agent1).get("/api/me/").data["role"], "manager")
        r = api.patch(f"/api/users/{self.agent1.pk}/", {"role": "agent"}, format="json")
        self.assertEqual(r.data["role"], "agent")
        self.assertEqual(self.api(self.agent1).get("/api/users/").status_code, 403)
        self.assertEqual(self.agent1.groups.count(), 1)

    def test_cannot_change_own_role(self):
        r = self.api(self.manager).patch(f"/api/users/{self.manager.pk}/", {"role": "agent"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.api(self.manager).get("/api/me/").data["role"], "manager")

    def test_cannot_demote_last_active_manager(self):
        other = User.objects.get(pk=self.create_agent(username="szef", role="manager").data["id"])
        self.api(other).post(f"/api/users/{self.manager.pk}/deactivate/", {}, format="json")
        r = self.api(self.manager).patch(f"/api/users/{other.pk}/", {"role": "agent"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_invalid_role_in_edit(self):
        r = self.api(self.manager).patch(f"/api/users/{self.agent1.pk}/", {"role": "admin"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_agent_keeps_leads_after_role_change(self):
        lead = self.make_lead(owner=self.agent1)
        self.api(self.manager).patch(f"/api/users/{self.agent1.pk}/", {"role": "manager"}, format="json")
        lead.refresh_from_db()
        self.assertEqual(lead.owner, self.agent1)


class ResetPasswordTests(UsersBase):
    def test_generated_password_replaces_old_one(self):
        r = self.api(self.manager).post(f"/api/users/{self.agent1.pk}/reset-password/", {}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["generated"])
        self.assertEqual(self.token_login("agent1", "haslo12345").status_code, 401)
        self.assertEqual(self.token_login("agent1", r.data["password"]).status_code, 200)

    def test_manager_chooses_password(self):
        r = self.api(self.manager).post(
            f"/api/users/{self.agent1.pk}/reset-password/", {"password": "Zupelnie-nowe-9"}, format="json"
        )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["generated"])
        self.assertEqual(self.token_login("agent1", "Zupelnie-nowe-9").status_code, 200)

    def test_simple_password_is_accepted_on_reset(self):
        r = self.api(self.manager).post(
            f"/api/users/{self.agent1.pk}/reset-password/", {"password": "123"}, format="json"
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.token_login("agent1", "123").status_code, 200)

    def test_reset_logs_out_existing_sessions(self):
        tokens = self.token_login("agent1", "haslo12345").data
        auth = {"HTTP_AUTHORIZATION": f"Bearer {tokens['access']}"}
        self.assertEqual(self.client.get("/api/me/", **auth).status_code, 200)
        self.api(self.manager).post(f"/api/users/{self.agent1.pk}/reset-password/", {}, format="json")
        self.assertEqual(self.client.get("/api/me/", **auth).status_code, 401)
        refresh = self.client.post("/api/token/refresh/", {"refresh": tokens["refresh"]}, format="json")
        new_access = refresh.data.get("access") if refresh.status_code == 200 else None
        if new_access:  # nawet jeśli odświeżenie przejdzie, nowy token nie może dawać dostępu
            self.assertEqual(
                self.client.get("/api/me/", HTTP_AUTHORIZATION=f"Bearer {new_access}").status_code, 401
            )

    def test_unknown_user_is_404(self):
        r = self.api(self.manager).post("/api/users/99999/reset-password/", {}, format="json")
        self.assertEqual(r.status_code, 404)

    def test_user_without_role_cannot_be_reset(self):
        r = self.api(self.manager).post(f"/api/users/{self.no_role.pk}/reset-password/", {}, format="json")
        self.assertEqual(r.status_code, 404)


class DeactivateTests(UsersBase):
    def deactivate(self, user, **body):
        return self.api(self.manager).post(f"/api/users/{user.pk}/deactivate/", body, format="json")

    def test_deactivated_agent_cannot_log_in(self):
        self.assertEqual(self.deactivate(self.agent1).status_code, 200)
        self.agent1.refresh_from_db()
        self.assertFalse(self.agent1.is_active)
        self.assertEqual(self.token_login("agent1", "haslo12345").status_code, 401)

    def test_existing_token_stops_working(self):
        tokens = self.token_login("agent1", "haslo12345").data
        auth = {"HTTP_AUTHORIZATION": f"Bearer {tokens['access']}"}
        self.deactivate(self.agent1)
        self.assertEqual(self.client.get("/api/me/", **auth).status_code, 401)
        refresh = self.client.post("/api/token/refresh/", {"refresh": tokens["refresh"]}, format="json")
        if refresh.status_code == 200:
            self.assertEqual(
                self.client.get("/api/me/", HTTP_AUTHORIZATION=f"Bearer {refresh.data['access']}").status_code, 401
            )

    def test_agent_with_active_leads_needs_a_decision(self):
        self.make_lead(owner=self.agent1)
        r = self.deactivate(self.agent1)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data["active_leads"], 1)
        self.agent1.refresh_from_db()
        self.assertTrue(self.agent1.is_active)

    def test_leads_returned_to_pool(self):
        from datetime import timedelta
        from django.utils import timezone

        new = self.make_lead(owner=self.agent1, phone="500100201")
        cb = self.make_lead(
            owner=self.agent1, phone="500100202", status=Lead.Status.CALLBACK,
            next_contact_at=timezone.now() + timedelta(days=1), next_contact_note="oferta",
        )
        won = self.make_lead(owner=self.agent1, phone="500100203", status=Lead.Status.WON)
        r = self.deactivate(self.agent1, handover="pool")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["leads_moved"], 2)
        for lead in (new, cb):
            lead.refresh_from_db()
            self.assertIsNone(lead.owner)
            self.assertEqual(lead.status, Lead.Status.NEW)
            self.assertIsNone(lead.next_contact_at)
        won.refresh_from_db()
        self.assertEqual(won.owner, self.agent1)  # zamknięty lead zostaje
        self.assertEqual(LeadAssignment.objects.filter(from_agent=self.agent1, to_agent=None).count(), 2)

    def test_leads_handed_to_other_agent(self):
        lead = self.make_lead(owner=self.agent1)
        r = self.deactivate(self.agent1, handover="agent", to_agent=self.agent2.pk)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["leads_moved"], 1)
        lead.refresh_from_db()
        self.assertEqual(lead.owner, self.agent2)

    def test_handover_needs_target(self):
        self.make_lead(owner=self.agent1)
        r = self.deactivate(self.agent1, handover="agent")
        self.assertEqual(r.status_code, 400)

    def test_cannot_hand_over_to_inactive_or_same_user(self):
        self.make_lead(owner=self.agent1)
        self.agent2.is_active = False
        self.agent2.save()
        self.assertEqual(self.deactivate(self.agent1, handover="agent", to_agent=self.agent2.pk).status_code, 400)
        self.assertEqual(self.deactivate(self.agent1, handover="agent", to_agent=self.agent1.pk).status_code, 400)
        self.agent1.refresh_from_db()
        self.assertTrue(self.agent1.is_active)

    def test_failed_handover_changes_nothing(self):
        lead = self.make_lead(owner=self.agent1)
        self.deactivate(self.agent1, handover="agent", to_agent=99999)
        lead.refresh_from_db()
        self.assertEqual(lead.owner, self.agent1)

    def test_cannot_deactivate_yourself(self):
        r = self.deactivate(self.manager)
        self.assertEqual(r.status_code, 400)
        self.manager.refresh_from_db()
        self.assertTrue(self.manager.is_active)

    def test_cannot_deactivate_last_active_manager(self):
        boss = User.objects.get(pk=self.create_agent(username="szef", role="manager").data["id"])
        self.assertEqual(self.api(boss).post(
            f"/api/users/{self.manager.pk}/deactivate/", {}, format="json").status_code, 200)
        # boss jest teraz jedynym aktywnym managerem; ktoś inny z uprawnieniami nie może go wyłączyć
        r = self.api(self.manager).post(f"/api/users/{boss.pk}/deactivate/", {}, format="json")
        self.assertEqual(r.status_code, 400)
        boss.refresh_from_db()
        self.assertTrue(boss.is_active)

    def test_last_active_manager_is_protected(self):
        other = User.objects.create_user("manager2", password="haslo12345")
        other.groups.add(self.manager.groups.first())
        other.is_active = False
        other.save()
        # jedyny aktywny manager = ten, który wysyła żądanie, więc to samo, co wyłączenie siebie
        self.assertEqual(self.deactivate(self.manager).status_code, 400)

    def test_already_inactive(self):
        self.deactivate(self.agent1)
        self.assertEqual(self.deactivate(self.agent1).status_code, 400)

    def test_deactivated_agent_disappears_from_assignable_list(self):
        self.deactivate(self.agent1)
        names = [a["username"] for a in self.api(self.manager).get("/api/agents/").data]
        self.assertNotIn("agent1", names)

    def test_history_is_kept(self):
        lead = self.make_lead(owner=self.agent1)
        self.deactivate(self.agent1, handover="pool")
        self.assertTrue(User.objects.filter(pk=self.agent1.pk).exists())
        self.assertTrue(LeadAssignment.objects.filter(lead=lead).exists())

    def test_activate_restores_access(self):
        self.deactivate(self.agent1)
        r = self.api(self.manager).post(f"/api/users/{self.agent1.pk}/activate/", format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.token_login("agent1", "haslo12345").status_code, 200)


class SuperuserProtectionTests(UsersBase):
    def setUp(self):
        super().setUp()
        self.root = User.objects.create_superuser("root", password="haslo12345")

    def test_superuser_is_listed_as_manager(self):
        rows = {r["username"]: r for r in self.api(self.manager).get("/api/users/").data}
        self.assertEqual(rows["root"]["role"], "manager")
        self.assertTrue(rows["root"]["is_superuser"])

    def test_plain_manager_cannot_touch_superuser(self):
        api = self.api(self.manager)
        self.assertEqual(api.post(f"/api/users/{self.root.pk}/reset-password/", {}, format="json").status_code, 403)
        self.assertEqual(api.post(f"/api/users/{self.root.pk}/deactivate/", {}, format="json").status_code, 403)
        self.assertEqual(api.patch(f"/api/users/{self.root.pk}/", {"first_name": "X"}, format="json").status_code, 403)
        self.root.refresh_from_db()
        self.assertTrue(self.root.is_active)
        self.assertTrue(self.root.check_password("haslo12345"))

    def test_cannot_change_role_of_superuser(self):
        r = self.api(self.root).patch(f"/api/users/{self.root.pk}/", {"role": "agent"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_superuser_can_manage_managers(self):
        r = self.api(self.root).post(f"/api/users/{self.manager.pk}/deactivate/", {}, format="json")
        self.assertEqual(r.status_code, 200)


class LastLoginTests(UsersBase):
    def test_login_updates_last_login(self):
        self.token_login("agent1", "haslo12345")
        rows = {r["username"]: r for r in self.api(self.manager).get("/api/users/").data}
        self.assertIsNotNone(rows["agent1"]["last_login"])
        self.assertIsNone(rows["agent2"]["last_login"])


class FirstLoginPasswordTests(UsersBase):
    def login_as(self, username, password):
        tokens = self.token_login(username, password).data
        return {"HTTP_AUTHORIZATION": f"Bearer {tokens['access']}"}

    def test_new_account_must_change_password(self):
        password = self.create_agent().data["password"]
        auth = self.login_as("nowy", password)
        self.assertTrue(self.client.get("/api/me/", **auth).data["must_change_password"])

    def test_old_accounts_do_not_have_to_change(self):
        self.assertFalse(self.api(self.agent1).get("/api/me/").data["must_change_password"])

    def test_user_sets_own_password_and_flag_clears(self):
        password = self.create_agent().data["password"]
        auth = self.login_as("nowy", password)
        r = self.client.post(
            "/api/me/password/",
            {"current_password": password, "new_password": "Moje-wlasne-haslo-5"},
            format="json", **auth,
        )
        self.assertEqual(r.status_code, 200)
        # odpowiedź zawiera nowe tokeny, które od razu działają
        me = self.client.get("/api/me/", HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
        self.assertEqual(me.status_code, 200)
        self.assertFalse(me.data["must_change_password"])
        self.assertEqual(self.token_login("nowy", password).status_code, 401)
        self.assertEqual(self.token_login("nowy", "Moje-wlasne-haslo-5").status_code, 200)
        # stary token przestaje działać
        self.assertEqual(self.client.get("/api/me/", **auth).status_code, 401)

    def test_wrong_current_password(self):
        r = self.api(self.agent1).post(
            "/api/me/password/", {"current_password": "zle", "new_password": "Nowe-haslo-123"}, format="json"
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("current_password", r.data)

    def test_user_may_choose_any_password(self):
        r = self.api(self.agent1).post(
            "/api/me/password/", {"current_password": "haslo12345", "new_password": "123"}, format="json"
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.token_login("agent1", "123").status_code, 200)

    def test_empty_or_same_new_password_rejected(self):
        api = self.api(self.agent1)
        for new in ["", "haslo12345"]:
            r = api.post("/api/me/password/", {"current_password": "haslo12345", "new_password": new}, format="json")
            self.assertEqual(r.status_code, 400, new)
        self.agent1.refresh_from_db()
        self.assertTrue(self.agent1.check_password("haslo12345"))

    def test_first_login_does_not_ask_for_temporary_password_again(self):
        password = self.create_agent().data["password"]
        auth = self.login_as("nowy", password)
        r = self.client.post("/api/me/password/", {"new_password": "moje"}, format="json", **auth)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.token_login("nowy", "moje").status_code, 200)

    def test_first_login_cannot_keep_temporary_password(self):
        password = self.create_agent().data["password"]
        auth = self.login_as("nowy", password)
        r = self.client.post("/api/me/password/", {"new_password": password}, format="json", **auth)
        self.assertEqual(r.status_code, 400)

    def test_normal_change_still_requires_current_password(self):
        r = self.api(self.agent1).post("/api/me/password/", {"new_password": "inne"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("current_password", r.data)

    def test_requires_login(self):
        r = self.api().post("/api/me/password/", {"current_password": "a", "new_password": "b"}, format="json")
        self.assertEqual(r.status_code, 401)

    def test_reset_forces_change_again(self):
        self.api(self.manager).post(f"/api/users/{self.agent1.pk}/reset-password/", {}, format="json")
        self.assertTrue(self.api(self.agent1).get("/api/me/").data["must_change_password"])

    def test_manager_resetting_own_password_is_not_forced(self):
        self.api(self.manager).post(
            f"/api/users/{self.manager.pk}/reset-password/", {"password": "Wlasne-haslo-managera-1"}, format="json"
        )
        self.assertFalse(self.api(self.manager).get("/api/me/").data["must_change_password"])


class DeleteUserTests(UsersBase):
    def delete(self, user):
        return self.api(self.manager).delete(f"/api/users/{user.pk}/")

    def test_account_without_history_can_be_deleted(self):
        pk = self.create_agent().data["id"]
        rows = {r["id"]: r for r in self.api(self.manager).get("/api/users/").data}
        self.assertTrue(rows[pk]["can_delete"])
        self.assertEqual(self.api(self.manager).delete(f"/api/users/{pk}/").status_code, 204)
        self.assertFalse(User.objects.filter(pk=pk).exists())

    def test_agent_cannot_delete(self):
        self.assertEqual(self.api(self.agent1).delete(f"/api/users/{self.agent2.pk}/").status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.agent2.pk).exists())

    def test_account_with_leads_cannot_be_deleted(self):
        lead = self.make_lead(owner=self.agent1)
        r = self.delete(self.agent1)
        self.assertEqual(r.status_code, 409)
        lead.refresh_from_db()
        self.assertEqual(lead.owner, self.agent1)

    def test_account_with_call_history_cannot_be_deleted(self):
        from .models import Activity

        lead = self.make_lead()
        Activity.objects.create(lead=lead, agent=self.agent1, outcome=Activity.Outcome.NOTE)
        self.assertEqual(self.delete(self.agent1).status_code, 409)
        rows = {r["username"]: r for r in self.api(self.manager).get("/api/users/").data}
        self.assertFalse(rows["agent1"]["can_delete"])

    def test_account_with_assignment_history_cannot_be_deleted(self):
        lead = self.make_lead()
        LeadAssignment.objects.create(lead=lead, from_agent=None, to_agent=self.agent1, reason="x")
        self.assertEqual(self.delete(self.agent1).status_code, 409)

    def test_cannot_delete_yourself_or_superuser(self):
        root = User.objects.create_superuser("root", password="haslo12345")
        self.assertEqual(self.delete(self.manager).status_code, 400)
        self.assertEqual(self.delete(root).status_code, 403)
        self.assertEqual(self.api(root).delete(f"/api/users/{root.pk}/").status_code, 400)

    def test_cannot_delete_last_active_manager(self):
        other = User.objects.get(pk=self.create_agent(username="szef", role="manager").data["id"])
        self.api(other).post(f"/api/users/{self.manager.pk}/deactivate/", {}, format="json")
        r = self.api(self.manager).delete(f"/api/users/{other.pk}/")
        self.assertEqual(r.status_code, 400)

    def test_deactivated_test_account_can_be_deleted(self):
        pk = self.create_agent().data["id"]
        self.api(self.manager).post(f"/api/users/{pk}/deactivate/", {}, format="json")
        self.assertEqual(self.api(self.manager).delete(f"/api/users/{pk}/").status_code, 204)


class EditAccountWithoutNamesTests(UsersBase):
    def test_account_without_names_can_change_login_and_role_only(self):
        self.assertEqual(self.agent1.get_full_name(), "")
        r = self.api(self.manager).patch(
            f"/api/users/{self.agent1.pk}/", {"username": "nowy.login", "role": "manager"}, format="json"
        )
        self.assertEqual(r.status_code, 200)
        self.agent1.refresh_from_db()
        self.assertEqual((self.agent1.first_name, self.agent1.last_name), ("", ""))

    def test_names_can_be_cleared(self):
        self.agent1.first_name = "Jan"
        self.agent1.save()
        r = self.api(self.manager).patch(f"/api/users/{self.agent1.pk}/", {"first_name": ""}, format="json")
        self.assertEqual(r.status_code, 200)


class AvatarInListTests(UsersBase):
    PNG = (
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )

    def test_list_contains_profile_pictures(self):
        self.api(self.agent1).post("/api/me/avatar/", {"image": self.PNG}, format="json")
        rows = {r["username"]: r for r in self.api(self.manager).get("/api/users/").data}
        self.assertEqual(rows["agent1"]["avatar"], self.PNG)
        self.assertEqual(rows["agent2"]["avatar"], "")


class UserAvatarTests(UsersBase):
    PNG = (
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )

    def row(self, username):
        return {r["username"]: r for r in self.api(self.manager).get("/api/users/").data}[username]

    def test_list_shows_avatar(self):
        self.api(self.agent1).post("/api/me/avatar/", {"image": self.PNG}, format="json")
        self.assertEqual(self.row("agent1")["avatar"], self.PNG)
        self.assertEqual(self.row("agent2")["avatar"], "")

    def test_manager_sets_and_removes_avatar(self):
        api = self.api(self.manager)
        r = api.post(f"/api/users/{self.agent1.pk}/avatar/", {"image": self.PNG}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.api(self.agent1).get("/api/me/").data["avatar"], self.PNG)
        self.assertEqual(api.delete(f"/api/users/{self.agent1.pk}/avatar/").status_code, 204)
        self.assertEqual(self.row("agent1")["avatar"], "")

    def test_bad_image_rejected(self):
        r = self.api(self.manager).post(
            f"/api/users/{self.agent1.pk}/avatar/", {"image": "data:text/html;base64,AAAA"}, format="json"
        )
        self.assertEqual(r.status_code, 400)

    def test_agent_cannot_change_others_avatar(self):
        api = self.api(self.agent1)
        self.assertEqual(api.post(f"/api/users/{self.agent2.pk}/avatar/", {"image": self.PNG}, format="json").status_code, 403)
        self.assertEqual(api.delete(f"/api/users/{self.agent2.pk}/avatar/").status_code, 403)

    def test_plain_manager_cannot_touch_superuser_avatar(self):
        root = User.objects.create_superuser("root", password="haslo12345")
        r = self.api(self.manager).post(f"/api/users/{root.pk}/avatar/", {"image": self.PNG}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_avatar_change_keeps_team_access_flag_and_password_flag(self):
        self.api(self.manager).post(f"/api/users/{self.agent1.pk}/avatar/", {"image": self.PNG}, format="json")
        me = self.api(self.agent1).get("/api/me/").data
        self.assertTrue(me["can_view_team"])
        self.assertFalse(me["must_change_password"])
