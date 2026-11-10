"""Praca na bazach z CEIDG: import w układzie „po pozycji”, bazy przypisane agentom,
pominięcie leada bez dzwonienia i nowe pola leada."""

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status

from .excel import XLSX_CONTENT_TYPE
from .models import Activity, Company, Lead, LeadBatch
from .sample_data import build_ceidg_sample_workbook
from .tests import CrmTestBase


def ceidg_upload():
    buffer = io.BytesIO()
    build_ceidg_sample_workbook().save(buffer)
    return SimpleUploadedFile("baza.xlsx", buffer.getvalue(), content_type=XLSX_CONTENT_TYPE)


class CeidgImportTests(CrmTestBase):
    def do_import(self, **extra):
        data = {"file": ceidg_upload(), **extra}
        return self.api(self.manager).post("/api/batches/import/", data, format="multipart")

    def test_ceidg_layout_is_recognised_by_position(self):
        response = self.do_import()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["layout"], "ceidg")
        self.assertEqual(response.data["created"], 5)
        self.assertEqual(response.data["errors"], [])

        lead = Lead.objects.get(phone="555020101")
        self.assertEqual(lead.contact_name, "")
        self.assertEqual(lead.display_name, "ZAKŁAD STOLARSKI ADAM PRZYKŁADOWY")
        company = lead.company
        self.assertEqual(company.nip, "5550001001")
        self.assertEqual(company.street, "ul. Leśna 4")
        self.assertEqual(company.postal_code, "10-001")
        self.assertEqual(company.city, "Olsztyn")

    def test_regon_keeps_leading_zero_and_apartment_is_joined(self):
        self.do_import()
        company = Lead.objects.get(phone="555020102").company
        self.assertEqual(company.regon, "055500102")
        self.assertEqual(company.street, "ul. Lipowa 12/3")

    def test_village_without_street_and_missing_postal_code(self):
        self.do_import()
        company = Lead.objects.get(phone="555020103").company
        self.assertEqual(company.street, "27")
        self.assertEqual(company.postal_code, "")
        self.assertEqual(company.city, "Kolonia Wzorcowa")

    def test_shifted_row_is_repaired_and_reported(self):
        response = self.do_import()
        company = Lead.objects.get(phone="555020104").company
        self.assertEqual(company.name, '"HURTOWNIA "ZEFIR" DOROTA PRZYKŁAD"')
        self.assertEqual(company.street, "ul. Polna 8")
        self.assertEqual(company.city, "Elbląg")
        rows = [w["row"] for w in response.data["warnings"]]
        self.assertIn(5, rows)

    def test_building_number_saved_as_date_is_restored(self):
        response = self.do_import()
        self.assertEqual(Lead.objects.get(phone="555020105").company.street, "ul. Długa 5/2")
        self.assertIn(6, [w["row"] for w in response.data["warnings"]])

    def test_import_can_assign_whole_batch_to_agent(self):
        response = self.do_import(assigned_to=self.agent1.pk)
        batch = LeadBatch.objects.get(pk=response.data["batch"]["id"])
        self.assertEqual(batch.assigned_to, self.agent1)
        # leady nie mają jeszcze opiekuna: dostaje je agent dopiero przy „Daj kolejnego”
        self.assertFalse(Lead.objects.filter(owner__isnull=False).exists())


class AssignedBatchClaimTests(CrmTestBase):
    def setUp(self):
        super().setUp()
        self.base1 = LeadBatch.objects.create(name="Baza agent1", assigned_to=self.agent1)
        self.base2 = LeadBatch.objects.create(name="Baza agent2", assigned_to=self.agent2)
        self.shared = LeadBatch.objects.create(name="Wspólna")
        self.own = self.make_lead(batch=self.base1, phone="555030001")
        self.other = self.make_lead(batch=self.base2, phone="555030002")
        self.common = self.make_lead(batch=self.shared, phone="555030003")

    def claim(self, user):
        return self.api(user).post("/api/leads/next/")

    def test_agent_gets_lead_from_own_base_first(self):
        response = self.claim(self.agent1)
        self.assertEqual(response.data["id"], self.own.id)

    def test_after_own_base_agent_gets_shared_pool_never_others_base(self):
        self.claim(self.agent1)
        self.assertEqual(self.claim(self.agent1).data["id"], self.common.id)
        self.assertEqual(self.claim(self.agent1).status_code, status.HTTP_404_NOT_FOUND)
        self.other.refresh_from_db()
        self.assertIsNone(self.other.owner)

    def test_base_progress(self):
        self.make_lead(batch=self.base1, phone="555030004")
        self.claim(self.agent1)
        data = self.api(self.agent1).get("/api/my-leads/base/").data
        self.assertEqual((data["total"], data["done"], data["left"]), (2, 1, 1))
        self.assertEqual(data["batches"][0]["name"], "Baza agent1")

    def test_manager_can_assign_existing_batch(self):
        response = self.api(self.manager).patch(
            f"/api/batches/{self.shared.pk}/", {"assigned_to": self.agent2.pk}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["assigned_to_name"], "agent2")


class SkipLeadTests(CrmTestBase):
    def setUp(self):
        super().setUp()
        self.lead = self.make_lead(owner=self.agent1)

    def skip(self, reason="closed", **extra):
        return self.api(self.agent1).post(
            "/api/activities/",
            {"lead": self.lead.id, "outcome": "skipped", "skip_reason": reason, **extra},
            format="json",
        )

    def test_skip_closes_lead_without_counting_attempt(self):
        response = self.skip("closed", note="wykreślona z CEIDG")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.DO_NOT_CALL)
        self.assertEqual(self.lead.attempts_count, 0)
        self.assertEqual(Activity.objects.get().skip_reason, "closed")

    def test_skip_reason_decides_status(self):
        self.skip("has_tmobile")
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.LOST)

    def test_skip_reason_optional_by_default(self):
        response = self.api(self.agent1).post(
            "/api/activities/", {"lead": self.lead.id, "outcome": "skipped"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.LOST)

    def test_skip_requires_reason_when_manager_wants(self):
        from .rules import get_rules
        rules = get_rules()
        rules.require_skip_reason = True
        rules.save()
        response = self.api(self.agent1).post(
            "/api/activities/", {"lead": self.lead.id, "outcome": "skipped"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reason_only_with_skip(self):
        response = self.api(self.agent1).post(
            "/api/activities/",
            {"lead": self.lead.id, "outcome": "no_answer", "skip_reason": "closed"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_skip_is_not_counted_as_call_in_stats(self):
        self.skip()
        data = self.api(self.agent1).get("/api/stats/?days=1").data
        self.assertEqual(data["agents"][0]["calls"], 0)


class LeadDetailsTests(CrmTestBase):
    def setUp(self):
        super().setUp()
        company = Company.objects.create(name="Firma Testowa", nip="5550002001", street="ul. Krótka 1", city="Olsztyn")
        self.lead = self.make_lead(owner=self.agent1, contact_name="", company=company)

    def patch(self, data):
        return self.api(self.agent1).patch(f"/api/leads/{self.lead.id}/", data, format="json")

    def test_display_name_is_company(self):
        data = self.api(self.agent1).get(f"/api/leads/{self.lead.id}/").data
        self.assertEqual(data["display_name"], "Firma Testowa")
        self.assertEqual(data["company_street"], "ul. Krótka 1")

    def test_agent_saves_operators_in_fixed_order(self):
        response = self.patch({"operators": "orange, PLAY"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["operators"], "play,orange")

    def test_unknown_operator_is_rejected(self):
        self.assertEqual(self.patch({"operators": "heyah"}).status_code, status.HTTP_400_BAD_REQUEST)

    def test_agent_fills_contact_person_phone_and_comment(self):
        response = self.patch({
            "contact_name": "Pani Anna", "phone_alt": "555 040 001",
            "client_comment": "Ma 3 numery w Play, umowa do marca",
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.phone_alt, "555 040 001")
        self.assertEqual(self.lead.client_comment, "Ma 3 numery w Play, umowa do marca")

    def test_client_info_remembers_who_changed_it(self):
        response = self.patch({"client_comment": "Decyduje właściciel"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["client_comment_by"], self.agent1.id)
        self.assertEqual(response.data["client_comment_by_name"], self.agent1.get_full_name() or self.agent1.username)
        self.assertIsNotNone(response.data["client_comment_at"])
        # inny agent widzi, kto wpisał informacje
        Lead.objects.filter(pk=self.lead.pk).update(owner=self.agent2)
        data = self.api(self.agent2).get(f"/api/leads/{self.lead.id}/").data
        self.assertEqual(data["client_comment"], "Decyduje właściciel")
        self.assertEqual(data["client_comment_by"], self.agent1.id)

    def test_other_changes_keep_client_info_author(self):
        self.patch({"client_comment": "Ma 2 numery"})
        self.patch({"contact_name": "Pan Marek", "client_comment": "Ma 2 numery"})  # ten sam tekst
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.client_comment_by, self.agent1)
        stamp = self.lead.client_comment_at
        self.patch({"operators": "play"})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.client_comment_at, stamp)

    def test_author_cannot_be_set_by_hand(self):
        self.patch({"client_comment": "Coś", "client_comment_by": self.agent2.id})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.client_comment_by, self.agent1)

    def test_bad_alt_phone_is_rejected(self):
        self.assertEqual(self.patch({"phone_alt": "123"}).status_code, status.HTTP_400_BAD_REQUEST)


class DemoBaseTests(CrmTestBase):
    """Większe fikcyjne bazy do pokazu: każda wchodzi bez błędów i nie nakłada się na inne."""

    def test_each_demo_file_imports_cleanly_and_files_do_not_overlap(self):
        from .sample_data import DEMO_PARTS, DEMO_ROWS_PER_PART, build_demo_ceidg_workbook
        for part in range(1, DEMO_PARTS + 1):
            buffer = io.BytesIO()
            build_demo_ceidg_workbook(part).save(buffer)
            upload = SimpleUploadedFile(f"pokaz_{part}.xlsx", buffer.getvalue(), content_type=XLSX_CONTENT_TYPE)
            response = self.api(self.manager).post("/api/batches/import/", {"file": upload}, format="multipart")
            self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
            self.assertEqual(response.data["layout"], "ceidg")
            self.assertEqual(response.data["created"], DEMO_ROWS_PER_PART, response.data)
            self.assertEqual(response.data["errors"], [])
        self.assertEqual(Lead.objects.filter(phone__startswith="555").count(), DEMO_PARTS * DEMO_ROWS_PER_PART)
