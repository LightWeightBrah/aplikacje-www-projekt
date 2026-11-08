from django.urls import path
from rest_framework.routers import DefaultRouter

from . import device_api, recordings_api, rules_api, users_api, views, work_api

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
    path("settings/test-sms/", rules_api.TestSmsView.as_view(), name="test-sms"),
    path("settings/test-sms/<int:pk>/", rules_api.TestSmsStateView.as_view(), name="test-sms-state"),
    path("leaderboard/", views.LeaderboardView.as_view(), name="leaderboard"),
    # praca agenta: „Następny klient”, liczniki follow-upów i liczby dnia
    path("work/next/", work_api.NextWorkView.as_view(), name="work-next"),
    path("work/summary/", work_api.WorkSummaryView.as_view(), name="work-summary"),
    # oferty
    path("work/contact/", work_api.WorkContactView.as_view(), name="work-contact"),
    path("device/activate/", device_api.DeviceActivateView.as_view(), name="device-activate"),
    path("devices/", device_api.DeviceListView.as_view(), name="devices"),
    path("devices/code/", device_api.DeviceCodeView.as_view(), name="device-code"),
    path("devices/<int:pk>/revoke/", device_api.DeviceRevokeView.as_view(), name="device-revoke"),
    path("work/login/", work_api.WorkLoginView.as_view(), name="work-login"),
    path("work/logout/", work_api.WorkLogoutView.as_view(), name="work-logout"),
    path("work/attendance/", work_api.AttendanceView.as_view(), name="work-attendance"),
    path("work/minutes/", work_api.WorkMinutesView.as_view(), name="work-minutes"),
    path("offers/", work_api.OffersView.as_view(), name="offers"),
    path("offers/<int:pk>/html/", work_api.OfferHtmlView.as_view(), name="offer-html"),
    path("offers/preview/", work_api.OfferPreviewView.as_view(), name="offer-preview"),
    path("offer-config/", work_api.OfferConfigView.as_view(), name="offer-config"),
    # informacje dla zespołu (panel „Solid info”)
    path("announcements/", work_api.AnnouncementsView.as_view(), name="announcements"),
    path("announcements/<int:pk>/", work_api.AnnouncementDetailView.as_view(), name="announcement"),
    # dialer: CRM ↔ aplikacja na telefonie
    path("dialer/status/", work_api.DialerStatusView.as_view(), name="dialer-status"),
    path("dialer/call/", work_api.DialerCallView.as_view(), name="dialer-call"),
    path("dialer/pairing/", work_api.DialerPairingView.as_view(), name="dialer-pairing"),
    path("dialer/pair/", work_api.DialerPairView.as_view(), name="dialer-pair"),
    path("dialer/poll/", work_api.DialerPollView.as_view(), name="dialer-poll"),
    path("dialer/calls/<int:pk>/finish/", work_api.DialerFinishView.as_view(), name="dialer-finish"),
    path("dialer/sms/<int:pk>/done/", work_api.DialerSmsDoneView.as_view(), name="dialer-sms-done"),
    path("dialer/calls/<int:pk>/recording/", recordings_api.DialerRecordingView.as_view(), name="dialer-recording"),
    # nagrywanie rozmów (opcjonalne, manager)
    path("recordings/", recordings_api.RecordingListView.as_view(), name="recordings"),
    path("recordings/<int:pk>/audio/", recordings_api.RecordingAudioView.as_view(), name="recording-audio"),
    path("recordings/settings/", recordings_api.RecordingSettingsView.as_view(), name="recording-settings"),
    path("recordings/google/connect/", recordings_api.GoogleConnectView.as_view(), name="recording-google-connect"),
    path("recordings/google/disconnect/", recordings_api.GoogleDisconnectView.as_view(), name="recording-google-disconnect"),
    *router.urls,
]
