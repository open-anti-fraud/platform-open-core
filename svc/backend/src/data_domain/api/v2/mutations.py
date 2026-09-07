import base64
import math
from typing import List, Optional

import strawberry
from django.apps import apps
from django.db import transaction
from django.conf import settings
from strawberry.types import Info

from data_domain.api.v2.types import SampleOutput, MultifacePolicy
from data_domain.managers import SampleManager, SampleObjectsName, ActivityManager
from data_domain.matcher import ActivityMatcherAPI
from data_domain.models import BlobMeta
from user_domain.managers import WorkspaceManager
from platform_lib.exceptions import BadInputDataException
from platform_lib.strawberry_auth.permissions import IsHaveAccess, IsWorkspaceActive
from platform_lib.types import CustomBinaryType, JSON, EyesInput, MutationResult
from platform_lib.utils import (
    get_workspace_id,
    validate_image,
    type_desc,
)


@strawberry.type
class Mutation:
    @strawberry.mutation(
        permission_classes=[IsHaveAccess, IsWorkspaceActive],
        description="Create a sample object from an image or raw sampleData",
    )
    def create_sample(
        self,
        info: Info,
        image: Optional[CustomBinaryType] = None,
        sample_data: Optional[JSON] = None,
        anonymous_mode: Optional[bool] = False,
        multiface_policy: Optional[MultifacePolicy] = MultifacePolicy.ALLOW_MULTIFACE,
    ) -> List[SampleOutput]:
        if sum(map(bool, [image, sample_data])) != 1:
            raise BadInputDataException("0xnf5825dh")

        workspace_id = get_workspace_id(info=info)
        request_id = info.context.request.META.get(
            "HTTP_X_REQUEST_ID"
        )  # TODO there are 2 ids somehow....
        objects_key = f"objects@{SampleObjectsName.CAPTURER}"
        template_version = WorkspaceManager.get_template_version(workspace_id)

        samples = []

        if image:
            validate_image(image)
            sample_data = SampleManager.process_image(
                image=image,
                template_version=template_version,
                request_id=request_id,
                is_anonymous=anonymous_mode,
                attributes=settings.CALCULATED_ATTRIBUTES,
            )
        elif sample_data:
            sample_data = (
                sample_data.get("data") or sample_data
            )  # if input with data or not

        if (errors := sample_data.get("errors")) is not None:
            raise Exception(errors[0])

        if anonymous_mode:
            sample_data.update({"$image": None})
        else:
            validate_image(base64.standard_b64decode(sample_data.get("$image")))

        if len(sample_data[objects_key]) > 1:
            # TODO: replace exception class and code
            if multiface_policy == MultifacePolicy.NOT_ALLOW_MULTIFACE:
                raise BadInputDataException(
                    f"Multiface policy is {MultifacePolicy.NOT_ALLOW_MULTIFACE} but more then one face found"
                )
            if multiface_policy == MultifacePolicy.BEST_QUALITY_FACE:
                if "quality" in settings.CALCULATED_ATTRIBUTES:
                    sample_data[objects_key] = [
                        max(sample_data[objects_key], key=lambda x: x["quality"]['total_score'])
                    ]
                else:
                    sample_data[objects_key] = [
                        max(
                            sample_data[objects_key],
                            key=lambda x: math.sqrt(
                                (pow(x["keypoints"]["right_pupil"]["x"] - x["keypoints"]["left_pupil"]["x"], 2) +
                                 pow(x["keypoints"]["right_pupil"]["y"] - x["keypoints"]["left_pupil"]["y"], 2))
                            )
                        )
                    ]

        face_meta_with_ids = SampleManager.create_blobs(workspace_id, sample_data)
        for face_meta in face_meta_with_ids[objects_key]:
            with transaction.atomic():
                sample = SampleManager.create_sample(
                    workspace_id=workspace_id,
                    sample_meta={
                        "$image": face_meta_with_ids["$image"],
                        objects_key: [face_meta],
                    },
                )

                template_blob_meta_id = face_meta["templates"][f"${template_version}"][
                    "id"
                ]

                template_blob_meta = BlobMeta.objects.select_for_update().get(
                    id=template_blob_meta_id
                )
                template_blob_meta.meta.update({"sample_id": str(sample.id)})
                template_blob_meta.save()

            samples.append(sample)

        return samples


@strawberry.type
class InternalMutation:
    @strawberry.mutation(
        permission_classes=[IsHaveAccess, IsWorkspaceActive],
        description="Delete activity",
    )
    def delete_activity(
        self,
        info: Info,
        activities_ids: type_desc(List[str], "List of activity ids"),
    ) -> MutationResult:
        workspace_id = get_workspace_id(info)
        template_version = WorkspaceManager.get_template_version(workspace_id)
        ActivityMatcherAPI.set_base_remove(workspace_id, template_version, activities_ids)

        activity_manager = ActivityManager()
        activity_manager.delete(workspace_id, activities_ids)
        return MutationResult(ok=True)
