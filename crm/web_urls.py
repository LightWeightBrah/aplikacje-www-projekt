from django.urls import path

from . import web_views

app_name = "crm"

urlpatterns = [
    path("", web_views.dashboard, name="dashboard"),
    path("queue/", web_views.queue_page, name="queue"),
    path("plan/", web_views.plan_page, name="plan"),
    path("login/", web_views.login_page, name="login"),
    path("manager/", web_views.manager_page, name="manager"),
    path("ranking/", web_views.ranking_page, name="ranking"),
    path("import/", web_views.import_page, name="import"),
    path("users/", web_views.users_page, name="users"),
    path("products/", web_views.products_page, name="products"),
    path("settings/", web_views.settings_page, name="settings"),
    path("leads/", web_views.lead_list, name="lead-list"),
    path("sw.js", web_views.service_worker, name="sw"),
]
