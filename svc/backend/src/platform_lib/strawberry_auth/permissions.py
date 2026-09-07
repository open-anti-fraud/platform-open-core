import uuid
from typing import List, Union, Optional, Any

from cachetools import cached, TTLCache
from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User
from strawberry.permission import BasePermission
from strawberry.types import Info

from api_gateway.api.token import Token
from platform_lib.exceptions import InvalidToken
from platform_lib.utils import get_token, get_workspace_id, get_user
from user_domain.models import Workspace, Access
from plib.tracing.utils import get_tracer, ContextStub
from platform_lib.caching import get_ttl


@cached(cache=TTLCache(maxsize=settings.CACHE_MAXSIZE, ttl=get_ttl(settings.CACHE_TTL, settings.CACHE_JITTER)))
def get_token_from_string(token: str) -> Token:
    return Token.from_string(token)


class CombinedPermission(BasePermission):
    """
    Permission class that allows you to apply the `or` operation to a group of BasePermission
    """

    permissions: List[BasePermission] = []

    def __call__(self):
        return self

    def __init__(self, permissions: List[BasePermission], message: Optional[str] = None):
        if not message:
            message = ' or '.join([p.message for p in permissions])
        self.message = message
        self.permissions = permissions

    def has_permission(self, source: Any, info: Info, **kwargs) -> bool:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("perm_combined") if tracer else ContextStub() as span:
            span.set_attribute("permission_list", str(self.permissions))

            for permission in self.permissions:
                if permission.has_permission(self=self, source=source, info=info, **kwargs):
                    return True
            return False


class IsAuthenticated(BasePermission):
    message = "User is not authenticated"

    def has_permission(self, source: Any, info: Info, **kwargs) -> bool:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("perm_is_authenticated") if tracer else ContextStub() as span:
            user = get_user(info=info)

            return not isinstance(user, AnonymousUser)


class IsAgentToken(BasePermission):
    message = "Agent token is invalid"

    def has_permission(self, source: Any, info: Info, **kwargs) -> bool:
        token = get_token(info=info)

        try:
            token = get_token_from_string(token)
        except InvalidToken:
            return False
        if token.is_agent():
            info.context.request.META['HTTP_TOKEN'] = token
        else:
            return False
        return True


class IsHaveAccess(BasePermission):
    message = "User has no access or id is incorrect"

    @staticmethod
    def __check_workspace_and_return_access(workspace_id: Union[str, uuid.UUID],
                                            user: User) -> Optional[Access]:
        if isinstance(user, AnonymousUser):
            user = None

        try:
            in_access = Access.objects.select_related('workspace').get(workspace_id=workspace_id)
        except Access.DoesNotExist:
            in_access = None

        if (user is not None) and (in_access is not None) and (in_access in user.accesses.all()):
            return in_access
        else:
            return None

    def has_permission(self, source: Any, info: Info, **kwargs) -> bool:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("perm_is_have_access") if tracer else ContextStub() as span:
            token = get_token(info=info)
            workspace_id = get_workspace_id(info=info)
            user = get_user(info=info)

            if token:

                try:
                    token = get_token_from_string(token)
                except InvalidToken:
                    return False

                workspace_id = token.workspace_id

            elif workspace_id is not None:

                try:
                    workspace_id = uuid.UUID(workspace_id)
                except ValueError:
                    return False

                if (access := self.__check_workspace_and_return_access(workspace_id, user)) is not None:
                    token = access.id
                    workspace_id = access.workspace_id

            if workspace_id is not None and token is not None:
                info.context.request.META['workspace_id'] = str(workspace_id)
                info.context.request.META['HTTP_TOKEN'] = str(token)

                return True
            else:
                return False


class IsHaveAccessUpd(BasePermission):
    message = "User has no access or id is incorrect"

    @staticmethod
    def __check_workspace_and_return_access(workspace_id: Union[str, uuid.UUID],
                                            user: User) -> Optional[Access]:
        if isinstance(user, AnonymousUser):
            user = None

        try:
            in_access = Access.objects.select_related('workspace').get(workspace_id=workspace_id)
        except Access.DoesNotExist:
            in_access = None

        if (user is not None) and (in_access is not None) and (in_access in user.accesses.all()):
            return in_access
        else:
            return None

    def has_permission(self, source: Any, info: Info, **kwargs) -> bool:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("perm_is_have_access_upd") if tracer else ContextStub() as span:
            token = get_token(info=info)
            workspace_id = get_workspace_id(info=info)
            user = get_user(info=info)
            workspace = None

            if token:

                try:
                    token = get_token_from_string(token)
                except InvalidToken:
                    return False

                if token.is_access():
                    workspace = Workspace.objects.get(accesses__id=token.id)
                if token.is_agent():
                    workspace = Workspace.objects.get(agents__id=token.id)

            elif workspace_id is not None:

                try:
                    workspace_id = uuid.UUID(workspace_id)
                except ValueError:
                    return False

                if (access := self.__check_workspace_and_return_access(workspace_id, user)) is not None:
                    token = access.id
                    workspace = access.workspace

            if workspace is not None and token is not None:
                info.context.request.META['workspace_id'] = str(workspace.id)
                info.context.request.META['workspace_object'] = workspace
                info.context.request.META['HTTP_TOKEN'] = str(token)

                return True
            else:
                return False


class IsWorkspaceActive(BasePermission):
    message = "User workspace is inactive"

    def has_permission(self, source: Any, info: Info, **kwargs) -> bool:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("perm_is_workspace_active") if tracer else ContextStub() as span:

            workspace_id = get_workspace_id(info=info)

            try:
                workspace = Workspace.objects.get(id=workspace_id)
            except Workspace.DoesNotExist:
                return False

            return bool(workspace.config.get('is_active'))


class IsServiceToken(BasePermission):
    message = "Wrong token or token is not provided"

    def has_permission(self, source: Any, info: Info, **kwargs) -> bool:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("perm_is_service_token") if tracer else ContextStub() as span:
            try:
                token = get_token_from_string(get_token(info=info))
            except InvalidToken:
                return False

            return token.is_service()


class IsAccessToken(BasePermission):
    message = "Wrong token or token is not provided"

    def has_permission(self, source: Any, info: Info, **kwargs) -> bool:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("perm_is_access_token") if tracer else ContextStub() as span:
            try:
                token = get_token_from_string(get_token(info=info))
            except InvalidToken:
                return False
            if token.is_access():
                info.context.request.user = token.model.user
                return True

            return False
