from django.urls import path
from rest_framework.routers import DefaultRouter

from . import rules_api, users_api, views

router = DefaultRouter()
router.register("products", views.ProductViewSet)
router.register("companies", views.CompanyViewSet)
router.register("batches", views.LeadBatchViewSet)
router.register("leads", views.LeadViewSet)
router.register("my-leads", views.MyLeadsViewSet, basename="my-lead")
router.register("activities", views.ActivityViewSet)
router.register("assignments", views.LeadAssignmentViewSet)
router.register("agents", views.AgentViewSet, basename="agent")
router.register("users", users_api.UserViewSet, basename="user")
router.register("stats", views.StatsViewSet, basename="stats")

urlpatterns = [
    path("settings/", rules_api.TeamRulesView.as_view(), name="team-rules"),
    path("settings/reset/", rules_api.TeamRulesResetView.as_view(), name="team-rules-reset"),
    path("leaderboard/", views.LeaderboardView.as_view(), name="leaderboard"),
    *router.urls,
]