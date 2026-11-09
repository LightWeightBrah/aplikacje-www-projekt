from django.urls import path

from . import phone_api, recordings_api, web_views

app_name = "crm"

urlpatterns = [
    path("", web_views.dashboard, name="dashboard"),
    path("queue/", web_views.queue_page, name="queue"),
    path("plan/", web_views.plan_page, name="plan"),
    path("calendar/", web_views.calendar_page, name="calendar"),
    path("login/", web_views.login_page, name="login"),
    path("manager/", web_views.manager_page, name="manager"),
    path("reports/", web_views.reports_page, name="reports"),
    path("ranking/", web_views.ranking_page, name="ranking"),
    path("import/", web_views.import_page, name="import"),
    path("users/", web_views.users_page, name="users"),
    path("products/", web_views.products_page, name="products"),
    path("settings/", web_views.settings_page, name="settings"),
    path("leads/", web_views.lead_list, name="lead-list"),
    path("sw.js", web_views.service_worker, name="sw"),
    path("dialer.apk", web_views.dialer_apk, name="dialer-apk"),
    path("activate/", web_views.activate_page, name="activate"),
    path("privacy/", web_views.privacy_page, name="privacy"),
    path("f/<str:token>/", phone_api.shared_file, name="shared-file"),
    path("recordings/google/callback/", recordings_api.google_callback, name="recording-google-callback"),
]
