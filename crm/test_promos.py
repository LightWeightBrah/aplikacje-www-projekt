"""Kilka promocji do wyboru w jednej taryfie (np. światłowód: 12×50 zł, 6×0 zł, 12×10 zł),
uzupełnianie starych taryf z wzornika i lista kanałów TV na ekranie „Dzwonienie”."""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .models import Offer, OfferConfig, Product, SaleItem
from .tariffs import clean_promos, load_standard_tariffs, promos_from_text, promos_to_text
from .tests import CrmTestBase


def fiber():
    return Product.objects.create(
        name="Światłowód M – blok", price=Decimal("45"), promo_price=Decimal("85"), extra_price=0,
        promo_label="12 miesięcy za 50 zł", promo_months=12, promo_month_price=Decimal("50"),
        promos=[{"label": "6 miesięcy za 0 zł", "months": 6, "month_price": "0.00", "price": "45.00"},
                {"label": "12 miesięcy za 10 zł", "months": 12, "month_price": "10.00", "price": "45.00"}])


class PromoChoiceTests(CrmTestBase):
    def setUp(self):
        super().setUp()
        self.fiber = fiber()
        self.config = OfferConfig.get()
        self.lead = self.make_lead(owner=self.agent1, phone="555300001")

    def test_every_promo_has_its_own_months_and_price(self):
        from .work import line_numbers

        line = {"product": self.fiber, "quantity": 1, "term": 24, "promo": True}
        main = line_numbers(self.config, line)
        self.assertEqual((main["label"], main["monthly"], main["first"]), ("12 miesięcy za 50 zł", Decimal("85"), Decimal("50.00")))
        six = line_numbers(self.config, {**line, "option": 1})
        # 6 × 0 zł, potem 45 zł: średnio 18 × 45 / 24 = 33,75 zł (jak w mailu ze szkolenia)
        self.assertEqual((six["label"], six["monthly"], six["first"], six["average"]),
                         ("6 miesięcy za 0 zł", Decimal("45.00"), Decimal("0.00"), Decimal("33.75")))
        ten = line_numbers(self.config, {**line, "option": 2})
        self.assertEqual((ten["monthly"], ten["average"]), (Decimal("45.00"), Decimal("27.50")))   # (12×10 + 12×45) / 24
        wrong = line_numbers(self.config, {**line, "option": 9})   # zły numer = pierwsza promocja
        self.assertEqual(wrong["label"], "12 miesięcy za 50 zł")

    def test_offer_api_uses_chosen_promo(self):
        r = self.api(self.agent1).post("/api/offers/", {
            "lead": self.lead.id, "client_email": "klient@example.com",
            "next_contact_at": (timezone.now() + timedelta(days=1)).isoformat(),
            "lines": [{"product": self.fiber.id, "quantity": 1, "term_months": 24, "promo": True, "option": 1}],
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        offer = Offer.objects.get()
        self.assertEqual(offer.monthly_total, Decimal("45.00"))
        self.assertIn("Promocja: 6 miesięcy za 0 zł", offer.body)
        self.assertEqual((offer.lines[0]["option"], offer.lines[0]["promo_label"]), (1, "6 miesięcy za 0 zł"))

    def test_sale_stores_chosen_promo(self):
        r = self.api(self.agent1).post("/api/activities/", {
            "lead": self.lead.id, "outcome": "sold",
            "sale_lines": [{"product": self.fiber.id, "quantity": 1, "term_months": 24, "promo": True, "option": 2}],
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        item = SaleItem.objects.get()
        self.assertEqual((item.unit_price, item.total, item.promo, item.promo_label),
                         (Decimal("45.00"), Decimal("45.00"), True, "12 miesięcy za 10 zł"))

    def test_preview_with_option(self):
        r = self.api(self.agent1).post("/api/offers/preview/", {
            "product": self.fiber.id, "quantity": 1, "term_months": 24, "promo": True, "option": 1}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(Decimal(r.data["monthly_total"]), Decimal("45.00"))


class PromoListTests(CrmTestBase):
    def test_clean_and_text_round_trip(self):
        rows = clean_promos([{"label": "6 miesięcy za 0 zł", "months": "6", "month_price": "0", "price": "45,5"}])
        self.assertEqual(rows, [{"label": "6 miesięcy za 0 zł", "months": 6, "month_price": "0.00", "price": "45.50"}])
        self.assertEqual(promos_from_text(promos_to_text(rows)), rows)
        for bad in ([{"label": "", "price": "1"}], [{"label": "X", "price": "abc"}], [{"label": "X", "months": 99, "price": "1"}], "x"):
            with self.assertRaises(ValueError):
                clean_promos(bad)
        with self.assertRaises(ValueError):
            promos_from_text("tylko nazwa")

    def test_manager_saves_promos_and_bad_ones_are_refused(self):
        p = fiber()
        r = self.api(self.manager).patch(f"/api/products/{p.id}/", {"promos": [{"label": "3 miesiące za 1 zł", "months": 3, "month_price": "1", "price": "45"}]}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(p.promos[0]["price"], "45.00")
        r = self.api(self.manager).patch(f"/api/products/{p.id}/", {"promos": [{"label": "", "price": "1"}]}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_load_standard_fills_empty_info_and_promos_only(self):
        old = Product.objects.create(name="Światłowód M – blok", price=Decimal("44"))   # bez promocji (stara wersja)
        tv = Product.objects.create(name="Magenta TV S", price=Decimal("18.52"))
        created, updated = load_standard_tariffs()
        self.assertIn("Światłowód M – blok", updated)
        self.assertIn("Magenta TV S", updated)
        self.assertIn("Kontrakt Podstawowy", created)
        self.assertIn("JUMP proFirma XS", created)
        old.refresh_from_db(); tv.refresh_from_db()
        self.assertEqual(old.price, Decimal("44"))            # cena managera zostaje
        self.assertEqual(old.promos, [])                       # światłowód ma jedną promocję
        self.assertEqual((old.promo_price, old.promo_label), (Decimal("45"), "12 miesięcy za 10 zł"))
        self.assertIn("Polsat", tv.info)
        self.assertEqual(load_standard_tariffs()[1], [])       # drugi raz nic się nie zmienia


class QueueChannelsTests(CrmTestBase):
    def test_queue_page_has_official_channel_list(self):
        self.client.force_login(self.agent1)
        r = self.client.get("/crm/queue/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'id="tv-channels"')
        tv = r.context["tv_channels"]
        self.assertIn("TVP 1", tv["packages"]["S"][0]["channels"])
        self.assertTrue(any("HBO HD" in g["channels"] for g in tv["packages"]["L"]))
