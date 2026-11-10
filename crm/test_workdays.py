"""Dni pracy w tygodniu i kilka ponownych prób po nieodebranym umówionym terminie."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from .models import Lead
from .rules import callback_retry_at, fit_work_time, get_rules, suggest_retry
from .tests import CrmTestBase

WARSAW = ZoneInfo("Europe/Warsaw")


def local(day, hour, minute=0):
    # październik 2026: 28 = środa, 29 = czwartek, 30 = piątek, 31 = sobota; 2 listopada = poniedziałek
    month = 11 if day < 20 else 10
    return datetime(2026, month, day, hour, minute, tzinfo=WARSAW)


class WorkTimeTests(CrmTestBase):
    def rules(self, days="12345"):
        rules = get_rules()
        rules.work_days = days
        rules.save()
        return rules

    def test_friday_evening_moves_to_monday_when_week_is_mon_fri(self):
        self.assertEqual(fit_work_time(self.rules(), local(30, 18)), local(2, 9))

    def test_friday_evening_moves_to_saturday_when_saturday_is_work_day(self):
        self.assertEqual(fit_work_time(self.rules("123456"), local(30, 18)), local(31, 9))

    def test_day_off_keeps_the_hour(self):
        self.assertEqual(fit_work_time(self.rules(), local(31, 14)), local(2, 14))

    def test_inside_work_time_stays(self):
        self.assertEqual(fit_work_time(self.rules(), local(28, 11, 20)), local(28, 11, 20))

    def test_automatic_retry_skips_weekend(self):
        # piątek 16:30, ponowna próba za 2 h wypada po pracy -> poniedziałek, nie sobota
        moment = suggest_retry(self.rules(), 0, local(30, 16, 30), [local(30, 16, 30)])
        self.assertEqual(timezone.localtime(moment).date(), local(2, 9).date())

    def test_automatic_retry_uses_saturday_when_it_is_work_day(self):
        moment = suggest_retry(self.rules("123456"), 0, local(30, 16, 30), [local(30, 16, 30)])
        self.assertEqual(timezone.localtime(moment).date(), local(31, 9).date())

    def test_later_retries_never_land_on_day_off(self):
        rules = self.rules()
        previous = [local(28, 9, 30), local(28, 12), local(28, 15)]
        for tries in range(3, 8):
            moment = suggest_retry(rules, tries, local(30, 16), previous + [local(30, 16)])
            self.assertLessEqual(timezone.localtime(moment).isoweekday(), 5, tries)


class CallbackStepsTests(CrmTestBase):
    def test_steps_follow_settings_and_repeat_the_last_one(self):
        rules = get_rules()
        self.assertEqual(rules.callback_retry_steps, [15, 60, 120, 1440])
        now = local(28, 10)
        self.assertEqual(callback_retry_at(rules, 0, now), local(28, 10, 15))
        self.assertEqual(callback_retry_at(rules, 1, now), local(28, 11))
        self.assertEqual(callback_retry_at(rules, 2, now), local(28, 12))
        self.assertEqual(callback_retry_at(rules, 3, now), local(29, 10))
        self.assertEqual(callback_retry_at(rules, 9, now), local(29, 10))

    def test_step_after_work_lands_on_next_work_day(self):
        rules = get_rules()
        self.assertEqual(callback_retry_at(rules, 0, local(30, 16, 50)), local(2, 9))

    def test_missed_appointments_walk_through_steps(self):
        lead = self.make_lead(owner=self.agent1, status=Lead.Status.CALLBACK,
                              next_contact_at=timezone.now() - timedelta(minutes=10))
        rules = get_rules()
        for missed_before in range(3):
            before = timezone.now()
            r = self.api(self.agent1).post("/api/activities/", {"lead": lead.pk, "outcome": "no_answer"}, format="json")
            self.assertEqual(r.status_code, 201, r.data)
            lead.refresh_from_db()
            self.assertEqual(lead.missed_calls, missed_before + 1)
            expected = callback_retry_at(rules, missed_before, before)
            self.assertLess(abs((lead.next_contact_at - expected).total_seconds()), 60)
            Lead.objects.filter(pk=lead.pk).update(next_contact_at=timezone.now() - timedelta(minutes=1))


class WorkSettingsApiTests(CrmTestBase):
    def patch(self, body):
        return self.api(self.manager).patch("/api/settings/", body, format="json")

    def test_defaults(self):
        data = self.api(self.agent1).get("/api/settings/").data
        self.assertEqual(data["work_days"], "12345")
        self.assertEqual(data["callback_retry_steps"], [15, 60, 120, 1440])
        self.assertEqual(data["max_attempts_per_agent"], 5)

    def test_manager_sets_work_days_and_steps(self):
        r = self.patch({"work_days": "6123", "callback_retry_steps": [30, 90]})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["work_days"], "1236")
        self.assertEqual(get_rules().callback_retry_steps, [30, 90])

    def test_bad_values_are_rejected(self):
        for body in (
            {"work_days": ""},
            {"work_days": "18"},
            {"callback_retry_steps": []},
            {"callback_retry_steps": [1]},
            {"callback_retry_steps": [15] * 9},
            {"callback_retry_steps": ["15"]},
            {"callback_retry_steps": 15},
        ):
            self.assertEqual(self.patch(body).status_code, 400, body)

    def test_reset_restores_work_days_and_steps(self):
        self.patch({"work_days": "1234567", "callback_retry_steps": [30]})
        r = self.api(self.manager).post("/api/settings/reset/")
        self.assertEqual(r.data["work_days"], "12345")
        self.assertEqual(r.data["callback_retry_steps"], [15, 60, 120, 1440])
