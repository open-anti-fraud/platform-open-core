from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.db import transaction
from django.utils.safestring import mark_safe


from user_domain.managers import WorkspaceManager
from .models import Workspace, Access, EmailTemplate


class CustomUserAdmin(UserAdmin):
    list_display = UserAdmin.list_display + ('workspace_count', 'workspace_list')
    actions = ['create_workspace', 'delete_user_workspaces', 'reset_users_owner_access']

    @admin.action(description='Create Workspace')
    def create_workspace(self, request, queryset):
        with transaction.atomic():
            for user in queryset:
                workspace = WorkspaceManager.create_workspace(title=str(user.username) + "'s Workspace",
                                                              username=user.username)
                Access.objects.create(user=user, workspace=workspace, permissions=Access.OWNER)

    @admin.action(description='Delete user workspaces')
    def delete_user_workspaces(self, request, queryset):
        with transaction.atomic():
            for user in queryset:
                for access in user.accesses.all():
                    workspace = access.workspace

                    # For proper workspace blobs deletion
                    workspace.blobmeta.all().delete()

                    workspace.delete()

    @admin.action(description='Reset user\'s access token')
    def reset_users_owner_access(self, request, queryset):
        with transaction.atomic():
            for user in queryset:
                a = user.accesses.get(permissions=Access.OWNER)
                a.delete()
                Access.objects.create(user=user, workspace=a.workspace, permissions=a.permissions)

    def workspace_count(self, obj):
        accesses = obj.accesses.all()
        count = 0
        if accesses:
            for access in accesses:
                count = count + 1 if access.workspace else count + 0
        return count

    def workspace_list(self, obj):
        accesses = obj.accesses.all()
        workspace_links = []
        if accesses:
            for access in accesses:
                if access.workspace:
                    workspace_links.append('<a href="/storage/admin/user_domain/workspace/{0}/change/">{1}</a>'
                                           .format(access.workspace.id, str(access.workspace)))
        return mark_safe('<br>'.join(workspace_links))


class EmailTemplateAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        if obj:
            return self.readonly_fields + ('template_key',)
        return self.readonly_fields


admin.site.unregister(User)

admin.site.register(Workspace)
admin.site.register(User, CustomUserAdmin)
admin.site.register(Access)
admin.site.register(EmailTemplate, EmailTemplateAdmin)
