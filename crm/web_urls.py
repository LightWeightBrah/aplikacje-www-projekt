from django.urls import path

from . import web_views

app_name = "crm"

urlpatterns = [
    path("leads/", web_views.lead_list, name="lead-list"),
]