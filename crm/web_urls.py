from django.urls import path

from . import web_views

app_name = "crm"

urlpatterns = [
    path("", web_views.dashboard, name="dashboard"),
    path("queue/", web_views.queue_page, name="queue"),
    path("login/", web_views.login_page, name="login"),
    path("leads/", web_views.lead_list, name="lead-list"),
]
