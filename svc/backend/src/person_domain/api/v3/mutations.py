import asyncio
import os
import uuid
from typing import List, Optional

import django
import strawberry
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.transaction import atomic
from strawberry import ID
from strawberry.file_uploads import Upload
from strawberry.types import Info

from data_domain.managers import SampleManager, SampleManagerV3, SampleEnricher
from person_domain.api.utils import create_profile_v3, save_sample_data, check_fields_for_create_profile
from person_domain.api.v3.types import ProfileOutput
from person_domain.managers import ProfileManager
from person_domain.models import Profile, ProfileSettings  # noqa
from platform_lib.exceptions import BadInputDataException
from platform_lib.strawberry_auth.permissions import IsWorkspaceActive, IsHaveAccessUpd
from platform_lib.types import CustomBinaryType, UniversalExtraFieldInput, MutationResult
from platform_lib.utils import type_desc, get_workspace_object, fixed_validate_image, get_workspace_id, \
    convert_old_sample_to_new
from platform_lib.matcher import MatcherAdapter
from platform_lib.validation import is_valid_json
from platform_lib.validation.schemes import profile_info_scheme
from plib.tracing.utils import get_tracer, ContextStub
from data_domain.api.v3.types import ImageInput
from person_domain.managers import PersonManager

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main.settings")
django.setup()

matcher_adapter = MatcherAdapter()


@strawberry.type
class Mutation:
    @strawberry.mutation(permission_classes=[IsHaveAccessUpd, IsWorkspaceActive],
                         description="Create profile by photo or profile info")
    def create_profile(
            self, info: Info,
            image: type_desc(Optional[Upload], "Image for profile creation") = None,
            base64_img: type_desc(Optional[CustomBinaryType], "Image in base64 for profile creation") = None,
            sample_id: type_desc(Optional[ID], "Sample id for profile creation") = None,
            profile_info: type_desc(Optional[List[UniversalExtraFieldInput]], "Data for profile creation") = None,
            profile_groups_id: type_desc(Optional[List[ID]], "Sample id for profile creation") = None
    ) -> ProfileOutput:
        # TODO: requires impelementation to work with matcher (see v2 implementation)
        raise NotImplementedError("create_profile is not implemented for v3 API")
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("create_profile") if tracer else ContextStub() as span:

            if sum(map(bool, [image, base64_img, sample_id])) != 1:
                raise BadInputDataException("0x4694e6ef")

            if profile_info is not None:
                profile_info_json = {field.name.lower(): field.value for field in profile_info}
                if not is_valid_json(profile_info_json, profile_info_scheme):
                    raise BadInputDataException("0x2316d4c5")
            else:
                profile_info_json = {}

            workspace = get_workspace_object(info=info)
            workspace_id = str(workspace.id)
            template_version = workspace.config['template_version']

            with tracer.start_as_current_span("profile_settings") if tracer else ContextStub() as span:
                # TODO [PCP] move to fields manager
                with atomic():
                    p_settings, created = ProfileSettings.objects.get_or_create(workspace_id=workspace_id)
                    if created:
                        p_settings.extra_fields = settings.REQUIRED_PROFILE_FIELDS
                        p_settings.save()
                    available_fields = p_settings.extra_fields
                missed_fields = set(profile_info_json) - set(available_fields)
                if missed_fields:
                    raise BadInputDataException("0x3bc7c4af")

            with tracer.start_as_current_span("create_sample") if tracer else ContextStub() as span:
                if image is not None or base64_img is not None:
                    sample_image = base64_img or image.read()
                    fixed_validate_image(sample_image)
                    requested_fields = ['age', 'gender', 'template']

                    enricher = SampleEnricher(image=sample_image)
                    raw_sample = asyncio.get_event_loop().run_until_complete(
                        enricher.async_execute_functions_by_fields(requested_fields)
                    )

                    sample = save_sample_data(raw_sample, workspace_id, template_version)
                else:
                    try:
                        Profile.samples.through.objects.get(sample_id=sample_id)
                        raise BadInputDataException("0xf1e94dca")
                    except ObjectDoesNotExist:
                        pass

                    sample = SampleManager.get_sample(workspace_id, sample_id)
                    sample.meta = convert_old_sample_to_new(sample.meta)
                check_fields_for_create_profile(sample)

            with tracer.start_as_current_span("create_profile") if tracer else ContextStub() as span:
                profile, person = create_profile_v3(
                    profile_info_json,
                    workspace,
                    sample,
                    profile_groups_id
                )
                template_b64 = SampleManagerV3.get_raw_template(sample.meta, template_version)

            matcher_adapter.add_person_to_index(workspace_id, str(person.id), template_b64)

        return profile

    @strawberry.mutation(permission_classes=[IsHaveAccessUpd, IsWorkspaceActive], description="Delete profiles by ids")
    def delete_profiles(root, info: Info,
                        profile_ids: type_desc(List[ID], "Profile ids to delete")) -> MutationResult:

        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("create_profile") if tracer else ContextStub() as span:
            span.set_attribute("profile_ids", profile_ids)
            workspace_id = get_workspace_id(info=info)

            with atomic():
                with tracer.start_as_current_span("delete_profiles") if tracer else ContextStub() as span:
                    ProfileManager.delete_profiles(workspace_id, profile_ids)

            return MutationResult(ok=True)

    @strawberry.mutation(permission_classes=[IsHaveAccessUpd, IsWorkspaceActive], description="Add groups to profiles")
    def add_profiles_to_groups(self,
                               info: Info,
                               group_ids: type_desc(List[ID], "List of group ids"),
                               profiles_ids: type_desc(List[ID], "List of profile ids")) -> MutationResult:
        ws_id = get_workspace_id(info)

        with atomic():
            for profile_id in set(profiles_ids):
                profile_m = ProfileManager(workspace_id=ws_id, profile_id=profile_id)
                profile_m.add_labels(group_ids)

        return MutationResult(ok=True)

    @strawberry.mutation(permission_classes=[IsHaveAccessUpd, IsWorkspaceActive],
                         description="Remove groups from profiles")
    def remove_profiles_from_groups(self,
                                    info: Info,
                                    group_ids: type_desc(List[ID], "List of group ids"),
                                    profiles_ids: type_desc(List[ID], "List of profile ids")) -> MutationResult:
        ws_id = get_workspace_id(info)

        with atomic():
            for profile_id in set(profiles_ids):
                profile_m = ProfileManager(workspace_id=ws_id, profile_id=profile_id)
                profile_m.remove_labels(group_ids)

        return MutationResult(ok=True)

    @strawberry.mutation(permission_classes=[IsHaveAccessUpd, IsWorkspaceActive],
                         description="Update profile main sample")
    def update_profile_main_sample(self,
                                   info: Info,
                                   by: ImageInput,
                                   profile_id: ID) -> MutationResult:
        # TODO: required adoptation to use new matcher with state control (see implementation in v2)
        raise NotImplementedError("update_profile_main_sample is not implemented for v3 API")
        tracer = get_tracer(__name__)

        image = by.base64 or by.upload.file.read()
        fixed_validate_image(image)

        workspace = get_workspace_object(info=info)
        workspace_id = workspace.id
        template_version = workspace.config["template_version"]

        with tracer.start_as_current_span("sample_enricher") if tracer else ContextStub() as _:
            fixed_validate_image(image)
            enricher = SampleEnricher(image=image)
            # check face count before calculating template
            check_sample = asyncio.get_event_loop().run_until_complete(
                enricher.async_execute_functions_by_fields([
                    "bbox",
                ])
            )
            obj_len = len(check_sample["objects"])

            if obj_len == 0:
                raise BadInputDataException("0x95bg42fd")
            elif obj_len > 1:
                raise BadInputDataException("0x35vd45ms")

            raw_sample = asyncio.get_event_loop().run_until_complete(
                enricher.async_execute_functions_by_fields([
                    "gender",
                    "age",
                    "template"
                ])
            )

        with atomic():
            new_sample = save_sample_data(raw_sample, workspace_id, template_version, False)
            profile_manager = ProfileManager(workspace_id=workspace_id, profile_id=profile_id)
            profile_manager.update_profile_main_sample(new_sample)
            profile = profile_manager.get_profile()
            person_id = str(profile.person_id)
            person_manager = PersonManager(workspace_id=workspace_id, person_id=person_id)
            person_manager.update_person_main_sample(new_sample)

        template_b64 = SampleManagerV3.get_raw_template(new_sample.meta, template_version)

        index_ids = [str(label.id) for label in profile.profile_groups.all()]
        index_ids.append(workspace_id)
        for index_id in index_ids:
            matcher_adapter.delete_vectors_from_indexes([index_id], [person_id])
            matcher_adapter.add_person_to_index(index_id, person_id, template_b64)

        return MutationResult(ok=True)
