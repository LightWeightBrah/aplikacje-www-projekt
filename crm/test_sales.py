"""Katalog produktów z cenami, pozycje sprzedaży i przychód w statystykach."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.utils import timezone
from rest_framework import status

from .models import Activity, Lead, Product, SaleItem
from .tests import CrmTestBase


class SalesBase(CrmTestBase):
    def setUp(self):
        super().setUp()
        self.internet = Product.objects.create(name="Internet", price=Decimal("1200.00"), unit="umowa")
        self.sim = Product.objects.create(name="Karta SIM", price=Decimal("49.50"), unit="szt.")

    def sell(self, user, lead, lines, **extra):
        payload = {
            "lead": lead.id,
            "outcome": "sold",
            "sale_lines": [{"product": p.id, "quantity": q} for p, q in lines],
        }
        payload.update(extra)
        return self.api(user).post("/api/activities/", payload, format="json")


class ProductCatalogTests(SalesBase):
    def test_product_has_price_and_unit(self):
        data = self.api(self.agent1).get(f"/api/products/{self.internet.id}/").data
        self.assertEqual(Decimal(data["price"]), Decimal("1200.00"))
        self.assertEqual(data["unit"], "umowa")

    def test_unit_defaults_to_pieces_and_price_to_zero(self):
        r = self.api(self.manager).post("/api/products/", {"name": "Nowy"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r.data["unit"], "szt.")
        self.assertEqual(Decimal(r.data["price"]), Decimal("0"))

    def test_manager_sets_price(self):
        r = self.api(self.manager).patch(
            f"/api/products/{self.sim.id}/", {"price": "55.00"}, format="json"
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.sim.refresh_from_db()
        self.assertEqual(self.sim.price, Decimal("55.00"))

    def test_agent_cannot_change_price_or_add_product(self):
        api = self.api(self.agent1)
        r = api.patch(f"/api/products/{self.sim.id}/", {"price": "1.00"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        r = api.post("/api/products/", {"name": "Cudzy", "price": "1.00"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_negative_price_is_rejected(self):
        r = self.api(self.manager).post(
            "/api/products/", {"name": "Zły", "price": "-5.00"}, format="json"
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_can_be_filtered_to_active_products(self):
        self.sim.is_active = False
        self.sim.save()
        r = self.api(self.agent1).get("/api/products/?is_active=true")
        self.assertEqual([p["name"] for p in r.data["results"]], ["Internet"])


class SaleRecordingTests(SalesBase):
    def test_sale_computes_value_from_catalog_price(self):
        lead = self.make_lead(owner=self.agent1)
        r = self.sell(self.agent1, lead, [(self.internet, 3)])
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Decimal(r.data["sale_total"]), Decimal("3600.00"))
        item = SaleItem.objects.get()
        self.assertEqual(item.product_name, "Internet")
        self.assertEqual(item.unit_price, Decimal("1200.00"))
        self.assertEqual(item.quantity, 3)
        self.assertEqual(item.total, Decimal("3600.00"))
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.WON)

    def test_sale_with_several_products_sums_lines(self):
        lead = self.make_lead(owner=self.agent1)
        r = self.sell(self.agent1, lead, [(self.internet, 1), (self.sim, 4)])
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(r.data["sale_items"]), 2)
        self.assertEqual(Decimal(r.data["sale_total"]), Decimal("1398.00"))   # 1200 + 4 × 49,50

    def test_same_product_twice_becomes_one_line(self):
        lead = self.make_lead(owner=self.agent1)
        r = self.sell(self.agent1, lead, [(self.sim, 1), (self.sim, 2)])
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        item = SaleItem.objects.get()
        self.assertEqual(item.quantity, 3)
        self.assertEqual(item.total, Decimal("148.50"))

    def test_old_sale_keeps_its_price_after_price_change(self):
        lead = self.make_lead(owner=self.agent1)
        self.sell(self.agent1, lead, [(self.internet, 1)])
        self.api(self.manager).patch(
            f"/api/products/{self.internet.id}/", {"price": "2000.00", "name": "Internet Pro"}, format="json"
        )
        item = SaleItem.objects.get()
        self.assertEqual(item.unit_price, Decimal("1200.00"))
        self.assertEqual(item.total, Decimal("1200.00"))
        self.assertEqual(item.product_name, "Internet")
        lead2 = self.make_lead(owner=self.agent1, phone="500100201")
        self.sell(self.agent1, lead2, [(self.internet, 1)])
        self.assertEqual(SaleItem.objects.get(activity__lead=lead2).total, Decimal("2000.00"))

    def test_sale_requires_products_when_catalog_has_active_ones(self):
        lead = self.make_lead(owner=self.agent1)
        r = self.api(self.agent1).post(
            "/api/activities/", {"lead": lead.id, "outcome": "sold"}, format="json"
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.NEW)
        self.assertFalse(Activity.objects.exists())

    def test_sale_without_products_is_allowed_when_catalog_is_empty(self):
        Product.objects.all().delete()
        lead = self.make_lead(owner=self.agent1)
        r = self.api(self.agent1).post(
            "/api/activities/", {"lead": lead.id, "outcome": "sold"}, format="json"
        )
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(r.data["sale_total"])
        self.assertFalse(SaleItem.objects.exists())

    def test_sale_without_products_is_allowed_when_all_products_are_off(self):
        Product.objects.update(is_active=False)
        lead = self.make_lead(owner=self.agent1)
        r = self.api(self.agent1).post(
            "/api/activities/", {"lead": lead.id, "outcome": "sold"}, format="json"
        )
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)

    def test_inactive_product_cannot_be_sold(self):
        self.sim.is_active = False
        self.sim.save()
        lead = self.make_lead(owner=self.agent1)
        r = self.sell(self.agent1, lead, [(self.sim, 1)])
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(SaleItem.objects.exists())

    def test_products_only_allowed_with_sold_outcome(self):
        lead = self.make_lead(owner=self.agent1)
        r = self.sell(
            self.agent1,
            lead,
            [(self.sim, 1)],
            outcome="callback",
            next_contact_at=(timezone.now() + timedelta(days=1)).isoformat(),
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(SaleItem.objects.exists())

    def test_quantity_must_be_between_1_and_9999(self):
        lead = self.make_lead(owner=self.agent1)
        for quantity in (0, -1, 10000):
            r = self.sell(self.agent1, lead, [(self.sim, quantity)])
            self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST, quantity)
        self.assertFalse(SaleItem.objects.exists())

    def test_agent_cannot_sell_someone_elses_lead(self):
        lead = self.make_lead(owner=self.agent2)
        r = self.sell(self.agent1, lead, [(self.sim, 1)])
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(SaleItem.objects.exists())

    def test_undo_removes_sale_items(self):
        lead = self.make_lead(owner=self.agent1)
        r = self.sell(self.agent1, lead, [(self.internet, 2)])
        undo = self.api(self.agent1).post(f"/api/activities/{r.data['id']}/undo/")
        self.assertEqual(undo.status_code, status.HTTP_200_OK)
        self.assertFalse(SaleItem.objects.exists())
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.NEW)

    def test_lead_detail_history_shows_sale_items(self):
        lead = self.make_lead(owner=self.agent1)
        self.sell(self.agent1, lead, [(self.internet, 2), (self.sim, 1)])
        data = self.api(self.agent1).get(f"/api/leads/{lead.id}/").data
        sold = data["activities"][0]
        self.assertEqual({i["product_name"] for i in sold["sale_items"]}, {"Internet", "Karta SIM"})
        self.assertEqual(Decimal(sold["sale_total"]), Decimal("2449.50"))

    def test_other_outcomes_have_no_sale_data(self):
        lead = self.make_lead(owner=self.agent1)
        r = self.api(self.agent1).post(
            "/api/activities/", {"lead": lead.id, "outcome": "no_answer"}, format="json"
        )
        self.assertEqual(r.data["sale_items"], [])
        self.assertIsNone(r.data["sale_total"])


class RevenueStatsTests(SalesBase):
    def setUp(self):
        super().setUp()
        self.sell(self.agent1, self.make_lead(owner=self.agent1, phone="500100301"), [(self.internet, 3)])
        self.sell(self.agent2, self.make_lead(owner=self.agent2, phone="500100302"), [(self.internet, 1), (self.sim, 2)])

    def test_manager_sees_total_revenue_and_breakdown_by_product(self):
        data = self.api(self.manager).get("/api/stats/?days=1").data
        self.assertEqual(Decimal(data["revenue"]), Decimal("4899.00"))   # 3600 + 1200 + 99
        by_product = {row["product_name"]: row for row in data["sales_by_product"]}
        self.assertEqual(by_product["Internet"]["quantity"], 4)
        self.assertEqual(Decimal(by_product["Internet"]["revenue"]), Decimal("4800.00"))
        self.assertEqual(by_product["Karta SIM"]["quantity"], 2)
        self.assertEqual(data["sales_by_product"][0]["product_name"], "Internet")   # najwyższy przychód na górze

    def test_agent_sees_only_own_revenue(self):
        data = self.api(self.agent1).get("/api/stats/?days=1").data
        self.assertEqual(Decimal(data["revenue"]), Decimal("3600.00"))
        self.assertEqual([r["product_name"] for r in data["sales_by_product"]], ["Internet"])

    def test_revenue_per_agent_in_stats_rows(self):
        rows = {r["username"]: r for r in self.api(self.manager).get("/api/stats/?days=1").data["agents"]}
        self.assertEqual(Decimal(rows["agent1"]["revenue"]), Decimal("3600.00"))
        self.assertEqual(Decimal(rows["agent2"]["revenue"]), Decimal("1299.00"))

    def test_old_sales_are_outside_the_period(self):
        Activity.objects.filter(agent=self.agent1).update(created_at=timezone.now() - timedelta(days=10))
        today = self.api(self.manager).get("/api/stats/?days=1").data
        self.assertEqual(Decimal(today["revenue"]), Decimal("1299.00"))
        month = self.api(self.manager).get("/api/stats/?days=30").data
        self.assertEqual(Decimal(month["revenue"]), Decimal("4899.00"))

    def test_no_sales_means_zero_revenue(self):
        SaleItem.objects.all().delete()
        data = self.api(self.manager).get("/api/stats/?days=1").data
        self.assertEqual(Decimal(data["revenue"]), Decimal("0"))
        self.assertEqual(data["sales_by_product"], [])

    def test_leaderboard_can_be_sorted_by_revenue(self):
        r = self.api(self.manager).get("/api/leaderboard/?days=1&sort=revenue")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        rows = r.data["results"]
        self.assertEqual([x["username"] for x in rows[:2]], ["agent1", "agent2"])
        self.assertEqual(Decimal(rows[0]["revenue"]), Decimal("3600.00"))
        self.assertEqual(rows[0]["rank"], 1)

    def test_leaderboard_revenue_order_follows_money_not_count(self):
        # agent2 ma tyle samo sprzedaży co agent1 (1 rozmowa), ale mniejszą kwotę
        rows = self.api(self.manager).get("/api/leaderboard/?days=1&sort=sold").data["results"]
        self.assertEqual(rows[0]["sold"], rows[1]["sold"])
        self.assertIn("revenue", rows[0])

    def test_unknown_sort_is_still_rejected(self):
        r = self.api(self.manager).get("/api/leaderboard/?sort=pieniadze")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)


class DemoSalesTests(CrmTestBase):
    def test_demo_data_has_prices_and_sales_with_products(self):
        call_command("seed_demo", "--reset", stdout=StringIO())
        self.assertFalse(Product.objects.filter(price=0).exists())
        self.assertTrue(SaleItem.objects.exists())
        data = self.api(self.manager).get("/api/stats/?days=1").data
        self.assertGreater(Decimal(data["revenue"]), Decimal("0"))
