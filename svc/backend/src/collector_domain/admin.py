from django.contrib import admin

from .models import Agent, Camera, AttentionArea, AreaType, Location, AgentIndexEvent


class CameraAdmin(admin.ModelAdmin):
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset)


class AgentIndexEventAdmin(admin.ModelAdmin):
    list_display = ('id', 'type', 'creation_date', 'profile_id')
    ordering = ('-creation_date',)
    list_display_links = list_display


admin.site.register(Agent)
admin.site.register(Camera, CameraAdmin)
admin.site.register(AttentionArea)
admin.site.register(AreaType)
admin.site.register(Location)
admin.site.register(AgentIndexEvent, AgentIndexEventAdmin)
