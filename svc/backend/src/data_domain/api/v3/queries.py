import asyncio
from functools import reduce
from typing import List, Optional

import requests
import strawberry
from aiohttp import ClientSession
from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist

from data_domain.models import Sample
from platform_lib.types import JSON

from main import settings
from plib.tracing.utils import get_tracer, ContextStub, get_current_tracing_context
from strawberry import ID
from strawberry.types import Info

from data_domain.api.utils import check_faces_count, get_templates_from_image_v3, check_search_input_data_v3, \
    get_templates_from_samples_id, get_templates_with_meta_from_raw_sample_v3, search_result_to_response_v3

from data_domain.api.v2.types import MatchResult
from data_domain.api.v3.types import ImageProcessInfo, ImageInput, SearchType, SampleInput
from data_domain.managers import SampleEnricher, SampleManager
from platform_lib.exceptions import InternalException, BadInputDataException
from platform_lib.strawberry_auth.permissions import IsWorkspaceActive, IsHaveAccessUpd
from platform_lib.utils import get_workspace_object, get_token, \
    convert_old_template_key_to_new
from platform_lib.matcher import MatcherAdapter

workspace_model = apps.get_model('user_domain', 'Workspace')
matcher_adapter = MatcherAdapter()


@strawberry.type
class Query:

    @strawberry.field(permission_classes=[IsHaveAccessUpd, IsWorkspaceActive],
                      description="Detect and process faces on the image")
    def process_image(self, info: Info,
                      by: ImageInput,
                      ) -> ImageProcessInfo:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("process_image") if tracer else ContextStub() as span:
            faces_selection = next(filter(lambda x: x.name == 'objects', info.selected_fields[0].selections))
            requested_fields = [field.name for field in faces_selection.selections]

            enricher = SampleEnricher(image=by.base64 or by.upload.file.read())

            return asyncio.get_event_loop().run_until_complete(
                enricher.async_execute_functions_by_fields(requested_fields)
            )

    @strawberry.field(permission_classes=[IsHaveAccessUpd, IsWorkspaceActive],
                      description="Search similar people in a workspace based on images" +
                                  (". If a found human doesn't have a profile in system, profile output will be 'null'."
                                   if not settings.ENABLE_PROFILE_AUTOGENERATION else ''))
    def search_profiles(self, info: Info,
                        by: ImageInput,
                        scope: Optional[ID] = None,
                        score_threshold: Optional[float] = 0.0,
                        max_num_of_candidates_returned: Optional[int] = 5) -> List[SearchType]:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("search_profiles") if tracer else ContextStub() as span:
            check_search_input_data_v3(**locals())
            workspace = get_workspace_object(info=info)
            workspace_id = str(workspace.id)
            template_version = workspace.config['template_version']

            with tracer.start_as_current_span("get_templates_from_image") if tracer else ContextStub() as span:
                templates = asyncio.get_event_loop().run_until_complete(
                    get_templates_from_image_v3(by.base64 or by.upload.file.read(), template_version)
                )
                templates = [template["blob"] for template in templates]

            result = matcher_adapter.search_index(scope or workspace_id, templates, max_num_of_candidates_returned)
            return search_result_to_response_v3(result, templates, score_threshold)  # noqa

    @strawberry.field(permission_classes=[IsHaveAccessUpd, IsWorkspaceActive],
                      description="Search similar people in a workspace based on images" +
                                  (". If a found human doesn't have a profile in system, profile output will be 'null'."
                                   if not settings.ENABLE_PROFILE_AUTOGENERATION else ''))
    def search_profiles_by_sample(self,
                                  info: Info,
                                  sample_id: ID,
                                  scope: Optional[ID] = None,
                                  score_threshold: Optional[float] = 0.0,
                                  max_num_of_candidates_returned: Optional[int] = 5) -> List[SearchType]:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("search_profiles") if tracer else ContextStub() as span:
            check_search_input_data_v3(**locals())
            workspace = get_workspace_object(info=info)
            workspace_id = str(workspace.id)
            template_version = workspace.config['template_version']

            with tracer.start_as_current_span("get_templates_from_sample") if tracer else ContextStub() as span:
                try:
                    templates = get_templates_from_samples_id([sample_id], template_version)
                except IndexError:
                    raise BadInputDataException("0xa01dec6e")

            if len(templates) != 1:
                raise BadInputDataException("0xa01dec6e")

            result = matcher_adapter.search_index(scope or workspace_id, templates, max_num_of_candidates_returned)
            return search_result_to_response_v3(result, templates, score_threshold)  # noqa

    @strawberry.field(permission_classes=[IsHaveAccessUpd, IsWorkspaceActive],
                      description="Compare the sample from the two picture")
    def verify_by_image(self, info: Info,
                        source_image: ImageInput,
                        target_image: ImageInput) -> MatchResult:

        async def check_image_get_template():
            async with ClientSession() as session:
                with tracer.start_as_current_span("check_face_count") if tracer else ContextStub() as span:
                    await asyncio.gather(
                        check_faces_count(tg_image, session),
                        check_faces_count(sc_image, session)
                    )

                with tracer.start_as_current_span("get_templates") if tracer else ContextStub() as span:
                    templates = await asyncio.gather(
                        get_templates_from_image_v3(tg_image, template_version, session),
                        get_templates_from_image_v3(sc_image, template_version, session)
                    )
                    return reduce(lambda a, b: a + b, templates)

        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("verify_by_image") if tracer else ContextStub() as span:
            token = get_token(info)
            workspace = get_workspace_object(info=info)
            template_version = workspace.config['template_version']

            tg_image = source_image.base64 or source_image.upload.file.read()
            sc_image = target_image.base64 or target_image.upload.file.read()

            match_templates = asyncio.get_event_loop().run_until_complete(check_image_get_template())
            version, weight = template_version.replace("template", '').split("v")

            # SEND REQUEST
            data = {"objects": [
                {
                    "class": "face",
                    "template": {f"_face_template_extractor_{weight}_{version}": template}
                }
                for idx, template in enumerate(match_templates, 1)
            ]}

            tracing_context = get_current_tracing_context()

            response = requests.post(
                f'{settings.image_api_service_map["verify-matcher"]}/v2/process/sample',
                json=data,
                headers={'TOKEN': token, **tracing_context}
            )
            if response.status_code != 200:
                try:
                    err = response.json()['detail']
                except AttributeError:
                    err = response.content
                raise InternalException('0x176cbb31', err)

            return MatchResult(**response.json()['verification'])

    @strawberry.field(permission_classes=[IsHaveAccessUpd, IsWorkspaceActive],
                      description="Compare the sample from the two picture")
    def verify_by_sample(self, info: Info,
                         source_sample: ID,
                         target_sample: ID) -> MatchResult:
        async def check_image_get_template(image: Optional[bytes], template_version_: str) -> dict:
            if image is None:
                raise BadInputDataException("0xc335c6b9")

            async with ClientSession() as session:
                await check_faces_count(image, session)
                templates = await get_templates_from_image_v3(image, template_version_, session)

            return templates[0]

        async def get_template(image: Optional[bytes], template_version_: str) -> dict:
            if image is None:
                raise BadInputDataException("0xc335c6b9")

            templates = await get_templates_from_image_v3(image, template_version_)

            return templates[0]

        async def do_nothing_return_template(template_data: dict) -> dict:
            return template_data

        async def check_sample_get_template(tasks: list):
            async with ClientSession():
                with tracer.start_as_current_span("get_templates") if tracer else ContextStub() as span:
                    return await asyncio.gather(*tasks)

        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("verify_by_image") if tracer else ContextStub() as span:
            token = get_token(info)
            workspace = get_workspace_object(info=info)
            template_version = workspace.config.get('template_version')

            task_to_process = []

            try:
                source_sample = Sample.objects.get(id=source_sample)
                target_sample = Sample.objects.get(id=target_sample)
            except ObjectDoesNotExist:
                raise BadInputDataException("0xe23f8aac")

            source_objects = SampleManager.get_objects(source_sample.meta)
            target_objects = SampleManager.get_objects(target_sample.meta)

            if len(source_objects) > 1:
                raise BadInputDataException("0x35vd45ms")

            if len(target_objects) > 1:
                raise BadInputDataException("0x35vd45ms")

            if len(source_objects) == 0:
                task_to_process.append(
                    check_image_get_template(SampleManager.get_image(source_sample.meta), template_version)
                )
            else:
                source_template = SampleManager.get_template_with_meta(source_sample.meta, template_version)

                if source_template is not None:
                    task_to_process.append(do_nothing_return_template(source_template))
                else:
                    task_to_process.append(get_template(SampleManager.get_image(source_sample.meta), template_version))

            if len(target_objects) == 0:
                task_to_process.append(
                    check_image_get_template(SampleManager.get_image(target_sample.meta), template_version)
                )
            else:
                target_template = SampleManager.get_template_with_meta(target_sample.meta, template_version)

                if target_template is not None:
                    task_to_process.append(do_nothing_return_template(target_template))
                else:
                    task_to_process.append(get_template(SampleManager.get_image(target_sample.meta), template_version))

            match_templates = asyncio.get_event_loop().run_until_complete(check_sample_get_template(task_to_process))
            version, weight = template_version.replace("template", '').split("v")

            # SEND REQUEST
            data = {"objects": [
                {
                    "class": "face",
                    "template": {f"_face_template_extractor_{weight}_{version}": template}
                }
                for idx, template in enumerate(match_templates, 1)
            ]}

            tracing_context = get_current_tracing_context()

            response = requests.post(
                f'{settings.image_api_service_map["verify-matcher"]}/v2/process/sample',
                json=data,
                headers={'TOKEN': token, **tracing_context}
            )
            if response.status_code != 200:
                try:
                    err = response.json()['detail']
                except AttributeError:
                    err = response.content
                raise InternalException('0x176cbb31', err)

            return MatchResult(**response.json()['verification'])

    @strawberry.field(permission_classes=[IsHaveAccessUpd, IsWorkspaceActive],
                      description="Compare the sample from the two picture")
    def verify_by_raw_sample(self,
                             info: Info,
                             source_sample: JSON,
                             target_sample: SampleInput[JSON]) -> MatchResult:
        tracer = get_tracer(__name__)
        templates_to_compare = []

        with tracer.start_as_current_span("verify_by_sample") if tracer else ContextStub() as span:
            token = get_token(info)
            workspace = get_workspace_object(info=info)
            old_template_version = workspace.config.get('template_version')
            new_template_version = convert_old_template_key_to_new(f"${old_template_version}")

            source_templates = get_templates_with_meta_from_raw_sample_v3(source_sample, new_template_version)

            if len(source_templates) != 1:
                raise BadInputDataException("0xa01dec6e")

            templates_to_compare.append(source_templates[0])

            if target_sample.custom_input is not None:
                # handle json input
                target_templates = get_templates_with_meta_from_raw_sample_v3(target_sample.custom_input,
                                                                              new_template_version)

                if len(target_templates) != 1:
                    raise BadInputDataException("0xa01dec6e")

                templates_to_compare.append(target_templates[0])
            else:
                # handle sample id input
                try:
                    sample = Sample.objects.get(id=target_sample.sample_id)
                except ObjectDoesNotExist:
                    raise BadInputDataException("0xe23f8aac")

                if len(SampleManager.get_objects(sample.meta)) > 1:
                    raise BadInputDataException("0x35vd45ms")

                target_template = SampleManager.get_template_with_meta(sample.meta, old_template_version)

                if target_template is None:
                    raise BadInputDataException("0xa01dec6e")

                templates_to_compare.append(target_template)

            data = {"objects": [
                {
                    "class": "face",
                    "template": {new_template_version: template}
                }
                for idx, template in enumerate(templates_to_compare, 1)
            ]}

            tracing_context = get_current_tracing_context()

            response = requests.post(
                f'{settings.image_api_service_map["verify-matcher"]}/v2/process/sample',
                json=data,
                headers={'TOKEN': token, **tracing_context}
            )
            if response.status_code != 200:
                try:
                    err = response.json()['detail']
                except AttributeError:
                    err = response.content
                raise InternalException('0x176cbb31', err)

            return MatchResult(**response.json()['verification'])
