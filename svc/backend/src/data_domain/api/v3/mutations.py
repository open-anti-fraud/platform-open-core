import asyncio
from typing import List, Optional

import jsonschema
import strawberry
from django.apps import apps
from django.conf import settings
from strawberry.types import Info

from data_domain.api.utils import check_fields_for_create_sample
from data_domain.api.v2.types import SampleOutput
from data_domain.api.v3.types import ImageInput, CreateSampleInfo
from data_domain.managers import SampleEnricher
from person_domain.api.utils import save_sample_data
from platform_lib.strawberry_auth.permissions import IsWorkspaceActive, IsHaveAccessUpd
from platform_lib.types import JSON
from platform_lib.utils import get_workspace_object, fixed_validate_image
from platform_lib.validation.schemes import create_sample_scheme_v3
from plib.tracing.utils import get_tracer, ContextStub

workspace_model = apps.get_model('user_domain', 'Workspace')


@strawberry.type
class Mutation:
    @strawberry.mutation(permission_classes=[IsHaveAccessUpd, IsWorkspaceActive],
                         description="Create a sample object from an image or raw sampleData")
    def create_sample(self, info: Info,
                      by: ImageInput,
                      anonymous_mode: Optional[bool] = False) -> CreateSampleInfo:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("create_sample") if tracer else ContextStub() as span:
            check_fields_for_create_sample(info)
            workspace = get_workspace_object(info=info)
            workspace_id = str(workspace.id)
            template_version = workspace.config['template_version']

            with tracer.start_as_current_span("sample_enricher") if tracer else ContextStub() as span:
                image = by.base64 or by.upload.file.read()
                fixed_validate_image(image)
                try:
                    objects_selection = next(filter(lambda x: x.name == 'objects', info.selected_fields[0].selections))
                    requested_fields = [field.name for field in objects_selection.selections]
                except StopIteration:
                    requested_fields = []
                enricher = SampleEnricher(image=image)
                raw_sample = asyncio.get_event_loop().run_until_complete(
                    enricher.async_execute_functions_by_fields(requested_fields)
                )

            return save_sample_data(raw_sample, workspace_id, template_version, anonymous_mode)

    @strawberry.mutation(permission_classes=[IsHaveAccessUpd, IsWorkspaceActive],
                         description="Create a sample object from an image or raw sampleData")
    def save_sample(self, info: Info,
                    sample_data: Optional[JSON] = None,
                    anonymous_mode: Optional[bool] = False) -> SampleOutput:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("save_sample") if tracer else ContextStub() as span:
            workspace = get_workspace_object(info=info)
            workspace_id = str(workspace.id)
            template_version = workspace.config['template_version']
            sample_data = sample_data.get('data') or sample_data
            jsonschema.validate(sample_data, create_sample_scheme_v3)
            return save_sample_data(sample_data, workspace_id, template_version, anonymous_mode)
