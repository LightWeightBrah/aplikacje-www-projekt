"""Ustawienia zespołu (manager) i automatyczne ponowne próby po nieodebranym telefonie."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from .models import Lead
from .rules import attempt_times, get_rules, suggest_retry
from .services import release_stale_leads
from .tests import CrmTestBase

WARSAW = ZoneInfo("Europe/Warsaw")


def local(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=WARSAW)


class RulesApiTests(CrmTestBase):
    def test_agent_can_read_but_not_change(self):
        r = self.api(self.agent1).get("/api/settings/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["max_attempts_per_agent"], 3)
        self.assertEqual(r.data["retry_after_first_minutes"], 120)
        patch = self.api(self.agent1).patch("/api/settings/", {"max_attempts_per_agent": 5}, format="json")
        self.assertEqual(patch.status_code, 403)
        self.assertEqual(self.api(self.agent1).post("/api/settings/reset/").status_code, 403)

    def test_requires_login_and_role(self):
        self.assertEqual(self.api().get("/api/settings/").status_code, 401)
        self.assertEqual(self.api(self.no_role).get("/api/settings/").status_code, 403)

    def test_manager_changes_settings(self):
        r = self.api(self.manager).patch(
            "/api/settings/", {"max_attempts_per_agent": 5, "retry_after_first_minutes": 90}, format="json"
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["max_attempts_per_agent"], 5)
        self.assertEqual(r.data["updated_by_name"], "manager1")
        self.assertEqual(get_rules().retry_after_first_minutes, 90)

    def test_values_outside_range_are_rejected(self):
        for body in (
            {"max_attempts_per_agent": 0},
            {"max_attempts_per_agent": 11},
            {"retry_after_first_minutes": 1},
            {"work_start_hour": 20, "work_end_hour": 8},
        ):
            r = self.api(self.manager).patch("/api/settings/", body, format="json")
            self.assertEqual(r.status_code, 400, body)

    def test_reset_restores_defaults(self):
        self.api(self.manager).patch("/api/settings/", {"max_attempts_per_agent": 7}, format="json")
        r = self.api(self.manager).post("/api/settings/reset/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["max_attempts_per_agent"], 3)

    def test_attempt_limit_from_settings_returns_lead_to_pool(self):
        self.api(self.manager).patch("/api/settings/", {"max_attempts_per_agent": 2}, format="json")
        lead = self.make_lead(owner=self.agent1)
        for _ in range(2):
            self.api(self.agent1).post("/api/activities/", {"lead": lead.pk, "outcome": "no_answer"}, format="json")
        lead.refresh_from_db()
        self.assertIsNone(lead.owner)
        self.assertIsNone(lead.next_contact_at)

    def test_claim_timeout_from_settings(self):
        lead = self.make_lead(owner=self.agent1, claimed_at=timezone.now() - timedelta(hours=3))
        self.assertEqual(release_stale_leads(dry_run=True), 0)   # domyślnie 24 h
        self.api(self.manager).patch("/api/settings/", {"claim_timeout_hours": 2}, format="json")
        self.assertEqual(release_stale_leads(dry_run=True), 1)
        lead.refresh_from_db()
        self.assertEqual(lead.owner, self.agent1)


class SuggestRetryTests(CrmTestBase):
    def test_delays_by_attempt_number(self):
        rules = get_rules()
        now = local(26, 10)   # poniedziałek 10:00
        self.assertEqual(suggest_retry(rules, 0, now), local(26, 12))
        self.assertEqual(suggest_retry(rules, 1, now), local(27, 10))
        self.assertEqual(suggest_retry(rules, 2, now), local(28, 10))
        self.assertEqual(suggest_retry(rules, 5, now), local(28, 10))

    def test_evening_moves_to_next_morning(self):
        self.assertEqual(suggest_retry(get_rules(), 0, local(26, 16)), local(27, 9, 30))

    def test_night_moves_to_start_of_work_day(self):
        self.assertEqual(suggest_retry(get_rules(), 0, local(26, 5)), local(26, 9, 30))

    def test_work_hours_are_configurable(self):
        rules = get_rules()
        rules.work_start_hour, rules.work_end_hour = 8, 20
        self.assertEqual(suggest_retry(rules, 0, local(26, 17)), local(26, 19))
        self.assertEqual(suggest_retry(rules, 0, local(26, 19)), local(27, 8, 30))

    def test_default_work_day_is_nine_to_five(self):
        rules = get_rules()
        self.assertEqual((rules.work_start_hour, rules.work_end_hour), (9, 17))

    def test_retry_uses_a_different_time_of_day_than_earlier_attempts(self):
        rules = get_rules()
        # dzwoniono o 10:00 (rano), nieodebrane; +2 h = 12:00 to inna pora, więc zostaje
        self.assertEqual(suggest_retry(rules, 0, local(26, 10), [local(26, 10)]), local(26, 12))
        # następnego dnia o 12:00 pora „środek dnia” jest zajęta, więc dostajemy popołudnie
        used = [local(26, 10), local(26, 12)]
        self.assertEqual(suggest_retry(rules, 1, local(26, 12), used), local(27, 15))

    def test_same_slot_moves_later_the_same_day_when_possible(self):
        rules = get_rules()
        # 9:30 i +2 h = 11:30 to ta sama pora co 9:30? nie (11:30 zaczyna środek dnia) -> zostaje
        self.assertEqual(suggest_retry(rules, 0, local(26, 9, 30), [local(26, 9, 30)]), local(26, 11, 30))
        # próba o 12:00 +2 h = 14:00 (ta sama pora) -> popołudnie 15:00
        self.assertEqual(suggest_retry(rules, 0, local(26, 12), [local(26, 12)]), local(26, 15))

    def test_evening_attempts_rotate_the_time_of_day(self):
        # dzwonienie wieczorem (poza godzinami pracy) nie może dawać zawsze 9:00 następnego dnia
        rules = get_rules()
        evening = local(26, 21)
        got = [suggest_retry(rules, n, evening, [evening]) for n in range(3)]
        self.assertEqual(got, [local(27, 9, 30), local(28, 12), local(29, 15)])   # każda próba o innej porze

    def test_evening_skips_times_of_day_already_tried_in_work_hours(self):
        rules = get_rules()
        used = [local(26, 10), local(26, 21)]   # rano już dzwoniono
        self.assertEqual(suggest_retry(rules, 0, local(26, 21), used), local(27, 12))

    def test_all_times_of_day_used_fills_the_biggest_gap(self):
        """Po porannej, środkowej i popołudniowej próbie nie wracamy do 9:30, tylko bierzemy godzinę najdalszą od dotychczasowych."""
        rules = get_rules()
        used = [local(24, 9, 30), local(25, 12), local(26, 15)]
        self.assertEqual(suggest_retry(rules, 2, local(26, 15), used), local(28, 13, 30))

    def test_rotation_keeps_trying_new_hours(self):
        """Przy dużym limicie prób kolejne terminy nie powtarzają godzin: 9:30, 12:00, 15:00, potem luki."""
        rules = get_rules()
        now, tried, hours = local(26, 21), [local(26, 21)], []
        for n in range(6):
            now = suggest_retry(rules, n, now, tried)
            tried.append(now)
            hours.append((now.hour, now.minute))
        self.assertEqual(len(set(hours)), 6)
        self.assertEqual(hours[:3], [(9, 30), (12, 0), (15, 0)])
        self.assertTrue(all(9 <= h < 17 for h, _ in hours))


class AutoRetryTests(CrmTestBase):
    def post(self, lead, outcome, user=None, **extra):
        body = {"lead": lead.pk, "outcome": outcome}
        body.update(extra)
        return (self.api(user or self.agent1)).post("/api/activities/", body, format="json")

    def test_first_no_answer_sets_automatic_retry(self):
        lead = self.make_lead(owner=self.agent1)
        before = timezone.now()
        self.assertEqual(self.post(lead, "no_answer").status_code, 201)
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.NEW)
        self.assertGreaterEqual(lead.next_contact_at, before + timedelta(minutes=119))
        self.assertEqual(lead.activities.get().rescheduled_to, lead.next_contact_at)
        self.assertEqual(lead.missed_calls, 0)

    def test_second_no_answer_waits_longer(self):
        lead = self.make_lead(owner=self.agent1)
        self.post(lead, "no_answer")
        before = timezone.now()
        self.post(lead, "no_answer")
        lead.refresh_from_db()
        self.assertGreaterEqual(lead.next_contact_at, before + timedelta(minutes=1439))

    def test_last_attempt_returns_lead_to_pool_without_term(self):
        lead = self.make_lead(owner=self.agent1)
        for _ in range(3):
            self.post(lead, "no_answer")
        lead.refresh_from_db()
        self.assertIsNone(lead.owner)
        self.assertIsNone(lead.next_contact_at)
        self.assertIsNone(lead.activities.first().rescheduled_to)

    def test_term_given_by_agent_wins(self):
        lead = self.make_lead(owner=self.agent1)
        when = timezone.now() + timedelta(days=4)
        self.post(lead, "no_answer", next_contact_at=when.isoformat())
        lead.refresh_from_db()
        self.assertEqual(lead.next_contact_at, when)

    def test_future_manual_term_is_kept(self):
        when = timezone.now() + timedelta(days=3)
        lead = self.make_lead(owner=self.agent1, next_contact_at=when)
        self.post(lead, "no_answer")
        lead.refresh_from_db()
        self.assertEqual(lead.next_contact_at, when)

    def test_retry_attempt_at_its_term_is_not_a_missed_appointment(self):
        lead = self.make_lead(owner=self.agent1, next_contact_at=timezone.now() - timedelta(minutes=20))
        self.post(lead, "no_answer")
        lead.refresh_from_db()
        self.assertEqual(lead.missed_calls, 0)
        self.assertIsNone(lead.agreed_contact_at)
        self.assertGreater(lead.next_contact_at, timezone.now())

    def test_lead_in_pool_gets_no_automatic_term(self):
        lead = self.make_lead(owner=None)
        self.post(lead, "no_answer", user=self.manager)
        lead.refresh_from_db()
        self.assertIsNone(lead.next_contact_at)

    def test_callback_after_retry_replaces_automatic_term(self):
        lead = self.make_lead(owner=self.agent1)
        self.post(lead, "no_answer")
        when = timezone.now() + timedelta(days=2)
        self.post(lead, "callback", next_contact_at=when.isoformat())
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.CALLBACK)
        self.assertEqual(lead.next_contact_at, when)

    def test_repeated_evening_no_answers_never_repeat_an_hour(self):
        from unittest import mock
        rules = get_rules(); rules.max_attempts_per_agent = 10; rules.save()
        lead = self.make_lead(owner=self.agent1)
        base, got = local(26, 21), []
        for i in range(7):
            with mock.patch("django.utils.timezone.now", return_value=base + timedelta(minutes=i)):
                # jak w formularzu przy nietkniętej propozycji: termin liczy serwer, nic nie wysyłamy
                self.assertEqual(self.post(lead, "no_answer").status_code, 201)
            lead.refresh_from_db()
            t = timezone.localtime(lead.next_contact_at)
            got.append((t.hour, t.minute))
        self.assertEqual(got[:4], [(9, 30), (12, 0), (15, 0), (13, 30)])
        self.assertEqual(len(set(got)), 7, got)


class LeadRetryFieldsTests(CrmTestBase):
    def lead_row(self, lead):
        r = self.api(self.agent1).get("/api/my-leads/")
        return next(x for x in r.data["results"] if x["id"] == lead.pk)

    def test_counts_tries_and_suggests_next_term(self):
        lead = self.make_lead(owner=self.agent1)
        row = self.lead_row(lead)
        self.assertEqual(row["owner_no_answers"], 0)
        self.assertIsNotNone(row["suggested_retry_at"])
        self.api(self.agent1).post("/api/activities/", {"lead": lead.pk, "outcome": "no_answer"}, format="json")
        row = self.lead_row(lead)
        self.assertEqual(row["owner_no_answers"], 1)
        self.assertIsNotNone(row["suggested_retry_at"])

    def test_no_suggestion_for_last_attempt(self):
        self.api(self.manager).patch("/api/settings/", {"max_attempts_per_agent": 2}, format="json")
        lead = self.make_lead(owner=self.agent1)
        self.api(self.agent1).post("/api/activities/", {"lead": lead.pk, "outcome": "no_answer"}, format="json")
        row = self.lead_row(lead)
        self.assertEqual(row["owner_no_answers"], 1)
        self.assertIsNone(row["suggested_retry_at"])

    def test_detail_has_the_same_fields(self):
        lead = self.make_lead(owner=self.agent1)
        r = self.api(self.agent1).get(f"/api/leads/{lead.pk}/")
        self.assertEqual(r.data["owner_no_answers"], 0)
        self.assertIn("suggested_retry_at", r.data)

    def test_callback_lead_has_no_suggestion(self):
        lead = self.make_lead(
            owner=self.agent1, status=Lead.Status.CALLBACK,
            next_contact_at=timezone.now() + timedelta(days=1),
        )
        self.assertIsNone(self.lead_row(lead)["suggested_retry_at"])


class RetryDoesNotBlockTests(CrmTestBase):
    def setUp(self):
        super().setUp()
        self.make_lead(owner=None, phone="500100999")   # coś jest w puli

    def test_overdue_retry_of_untouched_lead_does_not_block_claiming(self):
        self.make_lead(owner=self.agent1, next_contact_at=timezone.now() - timedelta(hours=1))
        r = self.api(self.agent1).post("/api/leads/next/")
        self.assertEqual(r.status_code, 200)

    def test_overdue_promised_callback_still_blocks_claiming(self):
        self.make_lead(
            owner=self.agent1, status=Lead.Status.CALLBACK,
            next_contact_at=timezone.now() - timedelta(hours=1),
        )
        r = self.api(self.agent1).post("/api/leads/next/")
        self.assertEqual(r.status_code, 409)

    def test_agent_list_counts_only_promised_as_overdue(self):
        self.make_lead(owner=self.agent1, next_contact_at=timezone.now() - timedelta(hours=1))
        r = self.api(self.manager).get("/api/agents/")
        row = next(a for a in r.data if a["username"] == "agent1")
        self.assertEqual(row["overdue_leads"], 0)


class InterestedNeedsTermTests(CrmTestBase):
    def test_interested_without_term_is_rejected(self):
        lead = self.make_lead(owner=self.agent1)
        r = self.api(self.agent1).post("/api/activities/", {"lead": lead.pk, "outcome": "interested"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_interested_with_term_is_saved(self):
        lead = self.make_lead(owner=self.agent1)
        when = timezone.now() + timedelta(days=1)
        r = self.api(self.agent1).post(
            "/api/activities/",
            {"lead": lead.pk, "outcome": "interested", "next_contact_at": when.isoformat()},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        lead.refresh_from_db()
        self.assertEqual((lead.status, lead.next_contact_at), (Lead.Status.INTERESTED, when))

    def test_status_interested_via_lead_patch_needs_term(self):
        lead = self.make_lead(owner=self.agent1)
        r = self.api(self.manager).patch(f"/api/leads/{lead.pk}/", {"status": "interested"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_demo_data_has_terms_for_all_promised_leads(self):
        from django.core.management import call_command

        call_command("seed_demo", "--reset", verbosity=0)
        promised = Lead.objects.filter(status__in=(Lead.Status.INTERESTED, Lead.Status.CALLBACK))
        self.assertGreater(promised.count(), 3)
        self.assertFalse(promised.filter(next_contact_at__isnull=True).exists())


class OffHoursRotationTests(CrmTestBase):
    def test_evening_attempts_rotate_through_suggested_times(self):
        """Kolejne nieodebrania wieczorem nie wracają do 9:30 po trzech próbach, tylko wypełniają luki w ciągu dnia."""
        rules = get_rules()
        now, attempts, got = local(26, 21), [], []
        for n in range(7):
            prev = attempt_times(rules, attempts) + [now]
            nxt = suggest_retry(rules, n, now, prev)
            attempts.append((now, nxt))
            got.append((nxt.hour, nxt.minute))
            now = local(26, 21) + timedelta(minutes=n + 1)   # kolejne kliknięcia nadal wieczorem
        self.assertEqual(got[:4], [(9, 30), (12, 0), (15, 0), (13, 30)])
        self.assertEqual(len(set(got)), 7)

    def test_in_hours_attempt_counts_only_its_own_time(self):
        rules = get_rules()
        self.assertEqual(attempt_times(rules, [(local(26, 10), local(26, 12))]), [local(26, 10)])

