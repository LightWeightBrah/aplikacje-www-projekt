from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("products", views.ProductViewSet)
router.register("companies", views.CompanyViewSet)
router.register("batches", views.LeadBatchViewSet)
router.register("leads", views.LeadViewSet)
router.register("my-leads", views.MyLeadsViewSet, basename="my-lead")
router.register("activities", views.ActivityViewSet)
router.register("assignments", views.LeadAssignmentViewSet)
router.register("stats", views.StatsViewSet, basename="stats")

urlpatterns = router.urls