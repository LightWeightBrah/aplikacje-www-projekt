from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("products", views.ProductViewSet)
router.register("companies", views.CompanyViewSet)
router.register("batches", views.LeadBatchViewSet)
router.register("leads", views.LeadViewSet)
router.register("activities", views.ActivityViewSet)
router.register("assignments", views.LeadAssignmentViewSet)

urlpatterns = router.urls