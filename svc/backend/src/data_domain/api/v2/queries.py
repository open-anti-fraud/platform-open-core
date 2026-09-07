import base64
from json import JSONDecodeError
from typing import List, Optional

import requests
import strawberry
from django.apps import apps
from main import settings
from strawberry import ID
from strawberry.types import Info
from plib.tracing.utils import get_tracer, ContextStub, get_current_tracing_context

from data_domain.api.utils import check_search_input_data, get_templates
from data_domain.api.v2.types import (
    ActivityFilter,
    ActivityOrdering,
    ActivityOutput,
    ActivitySearchType,
    MatchResult,
    SearchType,
    SampleCollection,
    sample_map
)
from data_domain.managers import SampleManager
from data_domain.matcher import ActivityMatcherAPI
from data_domain.models import BlobMeta, Sample
from label_domain.managers import LabelManager
from user_domain.managers import WorkspaceManager
from platform_lib.exceptions import BadInputDataException, InternalException
from platform_lib.strawberry_auth.permissions import IsHaveAccess, IsWorkspaceActive
from platform_lib.utils import (
    SampleObjectsName,
    StrawberryDjangoCountList,
    get_workspace_id,
    validate_image,
    get_paginated_model,
    paginated_field_generator,
)
from platform_lib.types import CountList, JSON, CustomBinaryType
from platform_lib.matcher import MatcherAdapter

workspace_model = apps.get_model("user_domain", "Workspace")


matcher_adapter = MatcherAdapter()


def resolve_samples_raw(*args, **kwargs) -> SampleCollection:
    info = kwargs.get('info')
    ids = kwargs.get('ids')
    order = kwargs.get('order')
    offset = kwargs.get('offset')
    limit = kwargs.get('limit')
    model_filter = kwargs.get('filter')

    workspace_id = get_workspace_id(info)
    total_count, samples = get_paginated_model(model_class=Sample,
                                               workspace_id=workspace_id,
                                               ids=ids,
                                               order=order,
                                               offset=offset,
                                               limit=limit,
                                               model_filter=model_filter,
                                               filter_map=sample_map)

    return SampleCollection(total_count=total_count, collection_items=samples)  # noqa


resolve_samples = paginated_field_generator(resolve_samples_raw)


@strawberry.type
class Query:
    samples: SampleCollection = strawberry.field(
        permission_classes=[IsHaveAccess], resolver=resolve_samples, description="Get a list of samples"
    )
    activities: CountList[ActivityOutput] = StrawberryDjangoCountList(
        permission_classes=[IsHaveAccess],
        description="Get a list of activities",
        filters=ActivityFilter,
        order=ActivityOrdering,
        pagination=True,
    )

    @strawberry.field(
        permission_classes=[IsHaveAccess, IsWorkspaceActive],
        description="Compare the sample from the picture/sampleData/sampleID with the sample in the DB",
    )
    def verify(
        self,
        info: Info,
        target_sample_id: ID,
        source_sample_id: Optional[ID] = None,
        source_sample_data: Optional[JSON] = None,
        source_image: Optional[CustomBinaryType] = None,
    ) -> MatchResult:
        if sum(map(bool, [source_sample_id, source_sample_data, source_image])) != 1:
            raise BadInputDataException("0x963fb254")

        workspace_id = get_workspace_id(info=info)
        objects_key = f"objects@{SampleObjectsName.CAPTURER}"
        template_version = workspace_model.objects.get(id=workspace_id).config.get(
            "template_version"
        )

        # GET TEMPLATE BLOBS
        target_sample = Sample.objects.get(id=target_sample_id)
        target_template_id = SampleManager.get_template_id(
            target_sample.meta, template_version
        )
        target_blob = (
            BlobMeta.objects.select_related("blob").get(id=target_template_id).blob.data
        )
        target_template = base64.standard_b64encode(target_blob.tobytes()).decode()
        match_templates = [target_template]

        if source_sample_id:
            source_sample = Sample.objects.get(id=source_sample_id)
            source_template_id = SampleManager.get_template_id(
                source_sample.meta, template_version
            )
            source_blob = (
                BlobMeta.objects.select_related("blob")
                .get(id=source_template_id)
                .blob.data
            )
            match_templates.append(
                base64.standard_b64encode(source_blob.tobytes()).decode()
            )

        elif source_sample_data:
            source_sample_data = (
                source_sample_data.get("data") or source_sample_data
            )  # if input with data or not
            source_template = source_sample_data[objects_key][0]["templates"][
                f"${template_version}"
            ]
            try:
                template_id = source_template.get("id")
                source_blob = (
                    BlobMeta.objects.select_related("blob")
                    .get(id=template_id)
                    .blob.data.tobytes()
                )
                match_templates.append(base64.standard_b64encode(source_blob).decode())
            except AttributeError:
                # source_template is already base64
                match_templates.append(source_template)

        else:
            validate_image(source_image)
            processing_result = SampleManager.process_image(
                image=source_image, template_version=template_version
            )
            template = processing_result[objects_key][0]["templates"][
                f"${template_version}"
            ]
            match_templates.append(template)

        assert len(match_templates) == 2, "One or both samples have an invalid format"

        proc_objects = []
        for templ in match_templates:
            proc_objects.append({"$template": templ, "template_size": 0, "class": "face"})

        response = requests.post(
            f"{settings.image_api_service_map['verify-matcher']}/v1/process/sample",
            json={"objects": proc_objects},
        )

        try:
            result_json = response.json()
        except (ValueError, TypeError, JSONDecodeError):
            raise InternalException("0x176cbb31", response.content)

        if response.status_code != 200:
            if (detail := result_json.get("detail")) is not None:
                err = detail
            else:
                err = result_json

            raise InternalException("0x176cbb31", err)

        return MatchResult(**result_json["verification"])

    @strawberry.field(
        permission_classes=[IsHaveAccess, IsWorkspaceActive],
        description="Search similar people in a workspace based on images, sample data, or sample IDs"
        + (
            ". If a found human doesn't have a profile in system, profile output will be 'null'."
            if not settings.ENABLE_PROFILE_AUTOGENERATION
            else ""
        ),
    )
    def search(
        self,
        info: Info,
        source_sample_ids: Optional[List[ID]] = None,
        source_sample_data: Optional[JSON] = None,
        source_image: Optional[CustomBinaryType] = None,
        scope: Optional[ID] = None,
        confidence_threshold: Optional[float] = 0.0,
        max_num_of_candidates_returned: Optional[int] = 5,
    ) -> List[SearchType]:
        # TODO needs to be split into different queries for each source
        check_search_input_data(**locals())

        tracer = get_tracer(__name__)

        workspace_id = get_workspace_id(info=info)
        template_version = WorkspaceManager.get_template_version(workspace_id)

        existed_sample: list = []
        difference = []
        if source_sample_ids is not None:
            existed_sample = list(map(str, SampleManager.get_samples(
                workspace_id=workspace_id, sample_ids=source_sample_ids).values_list("id", flat=True)))
            difference = set(source_sample_ids) - set(existed_sample)
        templates_map = get_templates(
            template_version=template_version,
            source_sample_ids=existed_sample,
            source_sample_data=source_sample_data,
            source_image=source_image
        )

        templates = []
        final_source = None
        for source, temps in templates_map.items():
            if temps:
                final_source = source
            templates.extend(temps)

        if scope and (str(scope) != str(workspace_id)) and not LabelManager.get_label_by_id(scope):
            raise BadInputDataException("0x98c7b2e5")

        if not scope:
            if not settings.DISABLE_WORKSPACE_SEARCH_INDEX:
                scope = workspace_id
            else:
                response = []
                for idx, template in enumerate(templates):
                    source_obj = {
                        "source_type": final_source,
                    }
                    if final_source == "source_sample_ids":
                        source_obj["source_value"] = existed_sample[idx]
                    elif final_source == "source_sample_data":
                        source_obj["source_value"] = source_sample_data[
                            f'objects@{SampleObjectsName.CAPTURER}'][idx]['templates'][f"${template_version}"]
                    else:
                        source_obj["source_value"] = source_image
                    response.append({
                        "template": template,
                        "search_result": [],
                        **source_obj
                    })
                return response

        with (tracer.start_as_current_span("search_query") if tracer else ContextStub()):
            result = matcher_adapter.search_index(scope, templates, max_num_of_candidates_returned)

            response = []
            if difference:
                for diff in difference:
                    response.append(
                        {
                            "source_type": "source_sample_ids",
                            "source_value": diff,
                            "message": "Sample objects does not exist",
                            "search_result": [],
                        }
                    )
            with tracer.start_as_current_span("matching") if tracer else ContextStub():
                for idx, template in enumerate(templates):
                    source_obj = {
                        "source_type": final_source,
                    }
                    if final_source == "source_sample_ids":
                        source_obj["source_value"] = existed_sample[idx]
                    elif final_source == "source_sample_data":
                        source_obj["source_value"] = (
                            source_sample_data[f'objects@{SampleObjectsName.CAPTURER}']
                            [idx]['templates'][f"${template_version}"]
                        )
                    else:
                        source_obj["source_value"] = source_image
                    source_result = {
                        "template": template,
                        "search_result": [
                            mtch for mtch in result['matches'][idx] if mtch['score'] > confidence_threshold
                        ],
                        **source_obj,
                    }
                    response.append(source_result)
            return response

    @strawberry.field(
        permission_classes=[IsHaveAccess, IsWorkspaceActive],
        description="Search similar activity in a workspace based on images, sample data, or sample IDs",
    )
    def search_in_activities(
        self,
        info: Info,
        source_sample_ids: Optional[List[ID]] = None,
        source_sample_data: Optional[JSON] = None,
        source_image: Optional[CustomBinaryType] = None,
        confidence_threshold: Optional[float] = 0.0,
        max_num_of_candidates_returned: Optional[int] = 5,
    ) -> List[ActivitySearchType]:
        check_search_input_data(**locals())

        workspace_id = get_workspace_id(info=info)
        template_version = workspace_model.objects.get(id=workspace_id).config.get(
            "template_version"
        )
        templates = get_templates(
            template_version, source_sample_ids, source_sample_data, source_image
        )

        search_results = ActivityMatcherAPI.search(
            workspace_id,
            template_version,
            templates,
            nearest_count=max_num_of_candidates_returned,
            score=confidence_threshold,
        )

        return search_results  # noqa

    @strawberry.field(
        permission_classes=[IsHaveAccess, IsWorkspaceActive],
        description="Detect faces on the image",
    )
    def detect(
        self,
        info: Info,
        image: CustomBinaryType
    ) -> JSON:

        validate_image(image)
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("get_workspace_get_template") if tracer else ContextStub():
            workspace_id = get_workspace_id(info=info)
            template_version = workspace_model.objects.get(id=workspace_id).config.get(
                "template_version"
            )

        with tracer.start_as_current_span("process_image") if tracer else ContextStub():
            result = SampleManager.process_image(
                image=image,
                template_version=template_version,
                attributes=settings.CALCULATED_ATTRIBUTES
            )

        return result


@strawberry.type
class InternalQuery:
    activities: CountList[ActivityOutput] = StrawberryDjangoCountList(
        permission_classes=[IsHaveAccess],
        description="Get a list of activities",
        filters=ActivityFilter,
        order=ActivityOrdering,
        pagination=True,
    )
