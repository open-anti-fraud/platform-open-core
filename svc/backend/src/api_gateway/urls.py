from .views import PostProcess, GetImage, GetAgentLink, ExternalLogin,\
    GetRealtimeImage, DuplicatePerson, DeleteProfiles, DeleteProfileGroups

from django.urls import path
from django.conf import settings
from api_gateway.api.v2.schema import schema as schema_v2
from api_gateway.api.v2.internal_schema import schema as internal_schema_v2
from api_gateway.api.v3.schema import schema as schema_v3
from api_gateway.views import APIView


urlpatterns = [
    path('api/v2/', APIView.as_view(schema=schema_v2, graphiql=True, allow_queries_via_get=False)),
    path('api/v3/', APIView.as_view(schema=schema_v3, graphiql=True, allow_queries_via_get=False)),
    path('internal-api/v2/', APIView.as_view(
        schema=internal_schema_v2, graphiql=settings.DEBUG, allow_queries_via_get=False)),
    path('internal-api/v2/external-login/', ExternalLogin.as_view()),
    path('rest-api/v1/post-event/', PostProcess.as_view()),
    path('rest-api/v1/delete-profile-groups/', DeleteProfileGroups.as_view()),
    path('rest-api/v1/delete-profiles/', DeleteProfiles.as_view()),
    path('get-image/<input_id>/', GetImage.as_view()),
    path('get-realtime-image/<image_key>/', GetRealtimeImage.as_view()),
    path('get-agent/v1/<os_version>/', GetAgentLink.as_view()),
    path('get-agent/v2/<os_version>/', GetAgentLink.as_view()),
    path('api/qa/duplicate-person/', DuplicatePerson.as_view()),
]
