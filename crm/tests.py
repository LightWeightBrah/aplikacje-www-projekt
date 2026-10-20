from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from .models import Activity, Company, Lead, LeadAssignment
from .roles import AGENT, MANAGER


# Szybszy hasher haseł, używany tylko w testach
@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class CrmTestBase(APITestCase):
    """Wspólne dane testowe i funkcje pomocnicze."""

    def setUp(self):
        agent_group, _ = Group.objects.get_or_create(name=AGENT)
        manager_group, _ = Group.objects.get_or_create(name=MANAGER)

        self.agent1 = User.objects.create_user("agent1", password="haslo12345")
        self.agent2 = User.objects.create_user("agent2", password="haslo12345")
        self.manager = User.objects.create_user("manager1", password="haslo12345")
        self.agent1.groups.add(agent_group)
        self.agent2.groups.add(agent_group)
        self.manager.groups.add(manager_group)

        self.no_role = User.objects.create_user("bezroli", password="haslo12345")

    def api(self, user=None):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        return client

    def make_lead(self, owner=None, **kwargs):
        data = {"contact_name": "Jan Kowalski", "phone": "500100200"}
        data.update(kwargs)
        return Lead.objects.create(owner=owner, **data)


class AuthAndPermissionTests(CrmTestBase):
    """Uwierzytelnianie JWT, role i uprawnienia."""

    # Brak tokena daje 401
    def test_unauthenticated_request_returns_401(self):
        response = self.api().get("/api/leads/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # Token JWT z /api/token/ daje dostęp do API
    def test_jwt_token_allows_access_to_api(self):
        response = self.client.post(
            "/api/token/",
            {"username": "agent1", "password": "haslo12345"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        response = self.client.get("/api/my-leads/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # Użytkownik bez roli Agent/Manager dostaje 403
    def test_user_without_role_gets_403(self):
        response = self.api(self.no_role).get("/api/leads/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # Produkty może dodawać tylko manager
    def test_agent_cannot_create_product_but_manager_can(self):
        payload = {"name": "Światłowód 1 Gb"}

        response = self.api(self.agent1).post("/api/products/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        response = self.api(self.manager).post("/api/products/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    # Leada może usunąć tylko manager
    def test_only_manager_can_delete_lead(self):
        lead = self.make_lead(owner=self.agent1)

        response = self.api(self.agent1).delete(f"/api/leads/{lead.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Lead.objects.filter(id=lead.id).exists())

        response = self.api(self.manager).delete(f"/api/leads/{lead.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Lead.objects.filter(id=lead.id).exists())


class LeadVisibilityTests(CrmTestBase):
    """Widoczność leadów zależnie od roli."""

    def setUp(self):
        super().setUp()
        self.lead_a1 = self.make_lead(owner=self.agent1, contact_name="Lead agenta 1")
        self.lead_a2 = self.make_lead(owner=self.agent2, contact_name="Lead agenta 2")
        self.lead_pool = self.make_lead(owner=None, contact_name="Lead z puli")

    # Agent widzi tylko swoje leady
    def test_agent_sees_only_own_leads(self):
        response = self.api(self.agent1).get("/api/leads/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [item["id"] for item in response.data["results"]]
        self.assertEqual(ids, [self.lead_a1.id])

    # Manager widzi wszystkie leady
    def test_manager_sees_all_leads(self):
        response = self.api(self.manager).get("/api/leads/")
        ids = {item["id"] for item in response.data["results"]}
        self.assertEqual(
            ids, {self.lead_a1.id, self.lead_a2.id, self.lead_pool.id}
        )

    # Agent nie otworzy cudzego leada (404)
    def test_agent_cannot_open_someone_elses_lead(self):
        response = self.api(self.agent1).get(f"/api/leads/{self.lead_a2.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # Lead agenta należy do niego, lead managera trafia do puli
    def test_agent_creates_lead_as_owner_manager_creates_into_pool(self):
        payload = {"contact_name": "Nowy Klient", "phone": "600700800"}

        response = self.api(self.agent1).post("/api/leads/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["owner"], self.agent1.id)

        response = self.api(self.manager).post("/api/leads/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data["owner"])


class LeadValidationAndSearchTests(CrmTestBase):
    """Walidacja danych i wyszukiwanie leadów."""

    # Za krótki numer telefonu jest odrzucany
    def test_phone_too_short_is_rejected(self):
        response = self.api(self.agent1).post(
            "/api/leads/",
            {"contact_name": "Ktoś", "phone": "123"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("phone", response.data)

    # Status callback wymaga terminu i notatki
    def test_callback_status_requires_date_and_note(self):
        response = self.api(self.agent1).post(
            "/api/leads/",
            {"contact_name": "Ktoś", "phone": "500100200", "status": "callback"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # Wyszukiwanie po NIP i KRS firmy
    def test_search_finds_lead_by_company_nip_and_krs(self):
        company = Company.objects.create(
            name="Test Sp. z o.o.", nip="1234567890", krs="0000123456"
        )
        wanted = self.make_lead(owner=self.agent1, company=company)
        self.make_lead(owner=self.agent1, contact_name="Inny Lead")
        client = self.api(self.agent1)

        for query in ("1234567890", "0000123456"):
            response = client.get("/api/leads/", {"search": query})
            ids = [item["id"] for item in response.data["results"]]
            self.assertEqual(ids, [wanted.id], msg=f"search={query}")


class NextLeadTests(CrmTestBase):
    """Endpoint leads/next."""

    # Lead z puli jest przypisywany agentowi
    def test_next_assigns_lead_from_pool_to_agent(self):
        lead = self.make_lead(owner=None)

        response = self.api(self.agent1).post("/api/leads/next/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], lead.id)
        lead.refresh_from_db()
        self.assertEqual(lead.owner, self.agent1)
        self.assertIsNotNone(lead.claimed_at)
        self.assertTrue(
            LeadAssignment.objects.filter(lead=lead, to_agent=self.agent1).exists()
        )

    # Pusta pula daje 404
    def test_next_returns_404_when_pool_is_empty(self):
        self.make_lead(owner=self.agent2)

        response = self.api(self.agent1).post("/api/leads/next/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # Zaległe oddzwonienie blokuje losowanie (409)
    def test_next_blocked_by_overdue_callback_returns_409(self):
        self.make_lead(
            owner=self.agent1,
            status=Lead.Status.CALLBACK,
            next_contact_at=timezone.now() - timedelta(hours=1),
            next_contact_note="Oddzwonić w sprawie oferty",
        )
        pool_lead = self.make_lead(owner=None, contact_name="Lead z puli")

        response = self.api(self.agent1).post("/api/leads/next/")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["overdue_count"], 1)
        pool_lead.refresh_from_db()
        self.assertIsNone(pool_lead.owner)

    # Agent nie dostaje leada, który już prowadził
    def test_next_does_not_return_lead_agent_already_handled(self):
        lead = self.make_lead(owner=None)
        LeadAssignment.objects.create(
            lead=lead, from_agent=self.agent1, reason="Zwrot do puli"
        )

        response = self.api(self.agent1).post("/api/leads/next/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        response = self.api(self.agent2).post("/api/leads/next/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], lead.id)


class ActivityEffectTests(CrmTestBase):
    """Wpływ aktywności na lead."""

    def setUp(self):
        super().setUp()
        self.lead = self.make_lead(owner=self.agent1)

    def add_activity(self, user=None, **payload):
        payload.setdefault("lead", self.lead.id)
        return self.api(user or self.agent1).post(
            "/api/activities/", payload, format="json"
        )

    # Brak odpowiedzi zwiększa próby i ustawia ostatnią aktywność
    def test_no_answer_increases_attempts_and_sets_last_activity(self):
        response = self.add_activity(outcome="no_answer")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.attempts_count, 1)
        self.assertIsNotNone(self.lead.last_activity_at)
        self.assertEqual(self.lead.status, Lead.Status.NEW)
        self.assertEqual(Activity.objects.filter(lead=self.lead).count(), 1)

    # Notatka nie liczy się jako próba
    def test_note_does_not_count_as_attempt(self):
        self.add_activity(outcome="note", note="Klient prosił o maila")

        self.lead.refresh_from_db()
        self.assertEqual(self.lead.attempts_count, 0)
        self.assertIsNotNone(self.lead.last_activity_at)

    # Prośba o oddzwonienie ustawia status i termin
    def test_callback_sets_status_and_next_contact(self):
        when = timezone.now() + timedelta(days=1)

        response = self.add_activity(
            outcome="callback",
            next_contact_at=when.isoformat(),
            next_contact_note="Oddzwonić jutro",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.CALLBACK)
        self.assertIsNotNone(self.lead.next_contact_at)
        self.assertEqual(self.lead.next_contact_note, "Oddzwonić jutro")

    # Oddzwonienie bez terminu jest odrzucane
    def test_callback_without_date_is_rejected(self):
        response = self.add_activity(outcome="callback")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.attempts_count, 0)
        self.assertEqual(Activity.objects.count(), 0)

    # Termin z przeszłości jest odrzucany
    def test_callback_date_in_the_past_is_rejected(self):
        past = timezone.now() - timedelta(hours=1)

        response = self.add_activity(
            outcome="callback",
            next_contact_at=past.isoformat(),
            next_contact_note="Za późno",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # Odmowa zamyka leada i czyści termin
    def test_refused_closes_lead_and_clears_next_contact(self):
        self.lead.next_contact_at = timezone.now() + timedelta(hours=2)
        self.lead.next_contact_note = "Stary termin"
        self.lead.save()

        self.add_activity(outcome="refused")

        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.LOST)
        self.assertIsNone(self.lead.next_contact_at)
        self.assertEqual(self.lead.next_contact_note, "")

    # Zły numer ustawia status nie dzwonić
    def test_wrong_number_sets_do_not_call(self):
        self.add_activity(outcome="wrong_number")

        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.DO_NOT_CALL)

    # Agent nie doda aktywności do cudzego leada
    def test_agent_cannot_add_activity_to_someone_elses_lead(self):
        response = self.add_activity(user=self.agent2, outcome="no_answer")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.attempts_count, 0)

    # Autorem aktywności jest zalogowany agent
    def test_activity_is_recorded_with_logged_in_agent(self):
        self.add_activity(outcome="no_answer", agent=self.agent2.id)

        activity = Activity.objects.get(lead=self.lead)
        self.assertEqual(activity.agent, self.agent1)


class MyLeadsDueTests(CrmTestBase):
    """Endpoint my-leads/due."""

    # Podział na zaległe i nadchodzące leady
    def test_due_splits_overdue_and_upcoming_leads(self):
        now = timezone.now()
        overdue = self.make_lead(
            owner=self.agent1, next_contact_at=now - timedelta(hours=1)
        )
        upcoming = self.make_lead(
            owner=self.agent1, next_contact_at=now + timedelta(minutes=5)
        )
        self.make_lead(owner=self.agent1, next_contact_at=now + timedelta(hours=3))
        self.make_lead(owner=self.agent2, next_contact_at=now - timedelta(hours=1))

        response = self.api(self.agent1).get("/api/my-leads/due/", {"minutes": 15})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["window_minutes"], 15)
        self.assertEqual([lead["id"] for lead in response.data["overdue"]], [overdue.id])
        self.assertEqual(
            [lead["id"] for lead in response.data["upcoming"]], [upcoming.id]
        )

    # Błędny parametr minutes daje 400
    def test_due_rejects_invalid_minutes(self):
        client = self.api(self.agent1)
        for value in ("abc", "-5", "5000"):
            response = client.get("/api/my-leads/due/", {"minutes": value})
            self.assertEqual(
                response.status_code, status.HTTP_400_BAD_REQUEST, msg=value
            )