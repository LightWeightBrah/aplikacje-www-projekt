from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

from .models import (
    Activity,
    AgentProfile,
    Company,
    Lead,
    LeadAssignment,
    LeadBatch,
    Product,
    SaleItem,
    TeamRules,
)


class ActivityInline(admin.TabularInline):
    model = Activity
    extra = 0
    readonly_fields = ("created_at",)


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0
    readonly_fields = ("product_name", "unit", "unit_price", "quantity", "total")
    can_delete = False


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "price", "unit", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "nip", "krs", "regon", "city", "created_at")
    search_fields = ("name", "nip", "krs", "regon", "city")


@admin.register(LeadBatch)
class LeadBatchAdmin(admin.ModelAdmin):
    list_display = ("name", "source_file_name", "created_by", "created_at")
    search_fields = ("name",)


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = (
        "contact_name",
        "phone",
        "status",
        "owner",
        "next_contact_at",
        "attempts_count",
        "batch",
    )
    list_filter = ("status", "owner", "product", "batch")
    search_fields = ("contact_name", "phone", "email", "company__name")
    date_hierarchy = "created_at"
    inlines = [ActivityInline]


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ("lead", "agent", "outcome", "created_at")
    list_filter = ("outcome", "agent")
    search_fields = ("lead__contact_name", "lead__phone", "note")
    inlines = [SaleItemInline]


@admin.register(LeadAssignment)
class LeadAssignmentAdmin(admin.ModelAdmin):
    list_display = ("lead", "from_agent", "to_agent", "reason", "assigned_at")
    list_filter = ("to_agent",)


class AgentProfileInline(admin.StackedInline):
    model = AgentProfile
    can_delete = False
    exclude = ("avatar",)  # długi tekst ze zdjęciem; użytkownik zmienia je sam w menu profilu


class UserWithProfileAdmin(UserAdmin):
    """Zwykły panel użytkownika z dodatkowym polem „może widzieć wyniki innych agentów”."""

    inlines = [AgentProfileInline]


admin.site.unregister(get_user_model())
admin.site.register(get_user_model(), UserWithProfileAdmin)


@admin.register(TeamRules)
class TeamRulesAdmin(admin.ModelAdmin):
    """Ustawienia zespołu (jeden wiersz); na co dzień zmienia je manager na stronie /crm/settings/."""

    list_display = ("__str__", "max_attempts_per_agent", "updated_at", "updated_by")

    def has_add_permission(self, request):
        return not TeamRules.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
