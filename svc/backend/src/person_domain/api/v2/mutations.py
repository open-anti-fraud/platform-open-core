import os
from typing import List, Optional
import base64

from person_domain.utils import ProfileMutationEventManager
from user_domain.managers import WorkspaceManager
from user_domain.models import Workspace  # noqa
from person_domain.managers import ProfileManager
from person_domain.managers import PersonManager
from person_domain.models import Person, Profile, ProfileSettings  # noqa
from person_domain.api.v2.types import ProfileInput, ProfileCreateOutput, ProfileUpdateOutput, ProfilesUpdateOutput
from person_domain.api.utils import check_profile_query, create_sample_by_image
from person_domain.tasks import create_deferred_remove_tasks
from data_domain.managers import SampleManager, ActivityManager
from data_domain.models import Sample
from label_domain.managers import LabelManager
from collector_domain.models import AgentIndexEvent

import django
import strawberry
from strawberry import ID
from strawberry.types import Info
from django.db.transaction import atomic
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from platform_lib.exceptions import InvalidJsonRequest, BadInputDataException
from platform_lib.validation import is_valid_json
from platform_lib.validation.schemes import profile_info_scheme
from platform_lib.utils import get_workspace_id, type_desc, validate_image
from platform_lib.types import CustomBinaryType, MutationResult, ModifyExtraField, FieldsModifyResult
from platform_lib.strawberry_auth.permissions import IsHaveAccess, IsWorkspaceActive
from platform_lib.matcher import MatcherAdapter
from plib.tracing.utils import get_tracer, ContextStub

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main.settings")
django.setup()
matcher_adapter = MatcherAdapter()


@strawberry.type
class Mutation:
    @strawberry.mutation(permission_classes=[IsHaveAccess, IsWorkspaceActive], description="Update main sample")
    def update_main_sample(self,
                           info: Info,
                           profile_id: ID,
                           sample_id: ID) -> ProfileUpdateOutput:
        tracer = get_tracer()
        with tracer.start_as_current_span("update_main_sample_mutation") if tracer else ContextStub() as span:
            workspace_id = get_workspace_id(info)

            workspace = Workspace.objects.get(id=workspace_id)
            template_version = workspace.config["template_version"]

            with atomic():
                profile_manager = ProfileManager(workspace_id=workspace_id, profile_id=profile_id)
                profile = profile_manager.get_profile()

                person_id = str(profile.person_id)
                person_manager = PersonManager(workspace_id=workspace_id, person_id=person_id)

                source_sample = SampleManager.get_sample(workspace_id=workspace_id, sample_id=sample_id)
                try:
                    main_sample = SampleManager.change_meta_to_new(
                        workspace_id=workspace_id,
                        destination_sample_id=profile.info.get('main_sample_id'),
                        origin_sample_id=sample_id
                    )
                except Sample.DoesNotExist:
                    main_sample = source_sample
                    with atomic():
                        profile = Profile.objects.select_for_update().get(id=profile.id)
                        profile.samples.add(main_sample.id)
                        profile.info.update({'main_sample_id': str(main_sample.id)})
                        profile.save()

                        person_manager.update(
                            info={'main_sample_id': str(main_sample.id)},
                            sample_ids=[str(main_sample.id)]
                        )

                template = SampleManager.get_template_bytes(main_sample.meta, template_version)

                now = timezone.now()
                index_ids = [str(label.id) for label in profile.profile_groups.all()]
                _, indexes_old_states = LabelManager.get_labels_and_touch(
                    workspace_id=workspace_id,
                    label_ids=set(index_ids),
                    now=now
                )

            if not settings.DISABLE_WORKSPACE_SEARCH_INDEX:
                matcher_adapter.add_vectors_to_indexes(
                    index_ids=[workspace_id],
                    vector_ids=[person_id],
                    vectors=[template],
                    old_state={workspace_id: ""},
                    new_state=""
                )

            matcher_adapter.add_vectors_to_indexes(
                index_ids=index_ids,
                vector_ids=[person_id],
                vectors=[template],
                old_state=indexes_old_states,
                new_state=now.isoformat()
            )

            return ProfileUpdateOutput(ok=True, profile=profile)

    @strawberry.mutation(permission_classes=[IsHaveAccess, IsWorkspaceActive], description="Delete profile sample")
    def delete_profile_sample(self, info: Info, profile_id: ID) -> ProfileUpdateOutput:
        tracer = get_tracer()
        with tracer.start_as_current_span("delete_profile_sample_mutauion") if tracer else ContextStub() as span:
            workspace_id = get_workspace_id(info=info)
            workspace = Workspace.objects.get(id=workspace_id)
            template_version = workspace.config["template_version"]

            profile_m = ProfileManager(workspace_id, profile_id)
            person_m = PersonManager(workspace_id, str(profile_m.profile.person_id))
            if profile_m.profile.info.get('main_sample_id'):
                profile_m.delete_profile_main_sample()
                person_m.delete_person_main_sample()
                person_ids = [str(profile_m.profile.person_id)]
                index_ids = [str(label) for label in profile_m.profile.profile_groups.values_list('id', flat=True)]
                if not settings.DISABLE_WORKSPACE_SEARCH_INDEX:
                    matcher_adapter.delete_vectors_from_indexes(
                        index_ids=[workspace_id],
                        vector_ids=person_ids,
                        old_state={workspace_id: ""},
                        new_state=""
                    )
                with atomic():
                    now = timezone.now()
                    _, indexes_old_states = LabelManager.get_labels_and_touch(
                        workspace_id=workspace_id,
                        label_ids=index_ids,
                        now=now
                    )
                matcher_adapter.delete_vectors_from_indexes(
                    index_ids=index_ids,
                    vector_ids=person_ids,
                    old_state=indexes_old_states,
                    new_state=now.isoformat()
                )

        return ProfileUpdateOutput(ok=True, profile=profile_m.profile)

    @strawberry.mutation(permission_classes=[IsHaveAccess, IsWorkspaceActive], description="Delete profiles by ids")
    def delete_profiles(self, info: Info,
                        profile_ids: type_desc(List[ID], "Profile ids to delete")) -> MutationResult:
        check_profile_query(profile_ids)
        tracer = get_tracer()
        with tracer.start_as_current_span("delete_profile_mutation") if tracer else ContextStub() as span:
            workspace_id = get_workspace_id(info=info)
            with atomic():
                if settings.ENABLE_AGENT:
                    for p_id in set(profile_ids):
                        profile_m = ProfileManager(workspace_id, p_id)
                        pmem = ProfileMutationEventManager(profile_m.profile)
                        pmem.delete_profile()

                ProfileManager.delete_profiles(workspace_id, profile_ids)
            return MutationResult(ok=True)

    @strawberry.mutation(permission_classes=[IsHaveAccess, IsWorkspaceActive], description="Update profile information")
    def update_profile(self, info: Info, profile_id: Optional[ID], profile_data: ProfileInput) -> ProfileUpdateOutput:
        if profile_data.info is not None and profile_data.fields is not None:
            raise Exception("Only one of the parameters info or fields is required")
        tracer = get_tracer()
        with tracer.start_as_current_span("update_profile_mutation") if tracer else ContextStub() as span:
            workspace_id = get_workspace_id(info=info)
            profile_data.info = profile_data.info or {}

            profile_m = ProfileManager(workspace_id, profile_id)
            if settings.ENABLE_AGENT:
                pmem = ProfileMutationEventManager(profile_m.get_profile())
            main_sample_exists = profile_m.profile.info.get('main_sample_id') is not None
            is_update_avatar = profile_data.info and profile_data.info.get('avatar_id') is not None
            with tracer.start_as_current_span("save_profile_settings") if tracer else ContextStub() as span:
                with atomic():
                    p_settings, created = ProfileSettings.objects.get_or_create(workspace_id=workspace_id)
                    if created:
                        p_settings.extra_fields = settings.REQUIRED_PROFILE_FIELDS
                        p_settings.save()

            if profile_data.fields:
                profile_data.info = {item.name.lower(): item.value or None for item in profile_data.fields}

            missed_fields = set(profile_data.info) - set(p_settings.extra_fields)
            if missed_fields:
                raise Exception("One or more profile fields are unavailable")

            if not is_valid_json(profile_data.info, profile_info_scheme):
                raise InvalidJsonRequest()
            with tracer.start_as_current_span("update_profile_query") if tracer else ContextStub() as span:
                with atomic():
                    profile = profile_m.update(profile_data.info, profile_data.profile_group_ids)
                    if settings.ENABLE_AGENT:
                        if is_update_avatar and not main_sample_exists:
                            pmem.add_profile(list(map(str, profile.profile_groups.values_list('id', flat=True))))
                        elif main_sample_exists or is_update_avatar:
                            pmem.update_profile(list(map(str, profile.profile_groups.values_list('id', flat=True))))
            return ProfileUpdateOutput(ok=True, profile=profile)

    @strawberry.mutation(permission_classes=[IsHaveAccess, IsWorkspaceActive], description="Add groups to profiles")
    def add_profiles_to_groups(self, info: Info,
                               group_ids: type_desc(List[ID], "List of group ids"),
                               profiles_ids: type_desc(List[ID], "List of profile ids")) -> ProfilesUpdateOutput:
        check_profile_query(profiles_ids)
        ws_id = get_workspace_id(info)
        profile_list = []
        with atomic():
            for profile_id in set(profiles_ids):
                profile_m = ProfileManager(workspace_id=ws_id, profile_id=profile_id)
                if settings.ENABLE_AGENT:
                    pmem = ProfileMutationEventManager(profile_m.profile)
                profile_m.add_labels(group_ids)
                if profile_m.profile.info.get('main_sample_id') is not None and settings.ENABLE_AGENT:
                    pmem.update_profile(list(set(list(group_ids) + list(profile_m.current_groups_ids))))
                profile_list.append(profile_m.get_profile())

        return ProfilesUpdateOutput(ok=True,
                                    profiles=profile_list)

    @strawberry.mutation(permission_classes=[IsHaveAccess, IsWorkspaceActive],
                         description="Remove groups from profiles")
    def remove_profiles_from_groups(self, info: Info,
                                    group_ids: type_desc(List[ID], "List of group ids"),
                                    profiles_ids: type_desc(List[ID],
                                                            "List of profile ids")) -> ProfilesUpdateOutput:
        check_profile_query(profiles_ids)
        ws_id = get_workspace_id(info)
        profile_list = []
        with atomic():
            for profile_id in set(profiles_ids):
                profile_m = ProfileManager(workspace_id=ws_id, profile_id=profile_id)

                if settings.ENABLE_AGENT:
                    pmem = ProfileMutationEventManager(profile_m.profile)  # warning
                profile_m.remove_labels(group_ids)
                if settings.ENABLE_AGENT:
                    if profile_m.profile.profile_groups.count():
                        pmem.update_profile(group_ids)
                    else:
                        pmem.delete_profile()

                profile_list.append(profile_m.get_profile())

        return ProfilesUpdateOutput(ok=True,
                                    profiles=profile_list)

    @strawberry.mutation(permission_classes=[IsHaveAccess, IsWorkspaceActive],
                         description="Create profile by photo or profile info")
    def create_profile(
        self,
        info: Info,
        image: type_desc(Optional[CustomBinaryType], "Image for profile creation") = None,
        profile_data: type_desc(Optional[ProfileInput], "Data for profile creation") = None
    ) -> ProfileCreateOutput:
        """
        Creates profile and person in the database with optional linking to the profile groups.
        If image is provided, it will be used to create a sample.
        If image is not provided, but info contains main_sample_id, this sample will be used.
        If no image and no main_sample_id profile will be created without sample.
        """
        tracer = get_tracer()
        with tracer.start_as_current_span("create_profile_mutation") if tracer else ContextStub() as span:
            if profile_data is None:
                profile_data = ProfileInput()
            if profile_data.info is not None and profile_data.fields is not None:
                raise Exception("Only one of the parameters info or fields is required")

            profile_data.info = profile_data.info or {}

            if profile_data.fields:
                profile_data.info = {item.name.lower(): item.value or None for item in profile_data.fields}

            if not is_valid_json(profile_data.info, profile_info_scheme):
                raise InvalidJsonRequest()

            workspace_id = get_workspace_id(info=info)

            with tracer.start_as_current_span("get_profile_settings") if tracer else ContextStub() as span:
                p_settings, _ = ProfileSettings.objects.get_or_create(
                    workspace_id=workspace_id,
                    defaults={'extra_fields': settings.REQUIRED_PROFILE_FIELDS})
                available_fields = p_settings.extra_fields

            missed_fields = set(profile_data.info) - set(available_fields)
            if missed_fields:
                raise Exception("One or more profile fields are unavailable")

            template_version = WorkspaceManager.get_template_version(workspace_id)

            sample = None
            with tracer.start_as_current_span("get_main_sample") if tracer else ContextStub() as span:
                if image is None:
                    if main_sample_id := profile_data.info.get('main_sample_id'):
                        sample = SampleManager.get_sample(workspace_id, main_sample_id)
                else:
                    validate_image(image)
                    sample, _ = create_sample_by_image(image, template_version, workspace_id)

            indexes_old_states = {}
            profile_info = profile_data.info
            sample_ids = [str(sample.id)] if sample is not None else None

            if sample is not None:
                profile_info.update({
                    'age': SampleManager.get_age(sample.meta),
                    'gender': SampleManager.get_gender(sample.meta).upper(),
                    'main_sample_id': str(sample.id),
                    'avatar_id': str(sample.id)
                })

            with tracer.start_as_current_span("create_profile_info") if tracer else ContextStub() as span:
                now = timezone.now()
                with atomic():
                    person = Person.objects.create(workspace_id=workspace_id, info=profile_info)
                    profile = Profile.objects.create(workspace_id=workspace_id, person=person, info=profile_info)

                    if profile_data.profile_group_ids is not None and len(profile_data.profile_group_ids) > 0:
                        if sample is not None:
                            _, indexes_old_states = LabelManager.get_labels_and_touch(
                                workspace_id=workspace_id,
                                label_ids=profile_data.profile_group_ids,
                                now=now
                            )
                        else:
                            # will raise error if profile_group_ids contains groups which doesn't exist in workspace
                            LabelManager.get_label_ids(workspace_id, profile_data.profile_group_ids)

                        # profile.profile_groups.add(*labels_to_index) generates 2 queries (select + insert)
                        # we can avoid it by using direct creation of relations
                        through_model = Profile.profile_groups.through
                        relations = [
                            through_model(profile_id=profile.id, label_id=label_id)
                            for label_id in profile_data.profile_group_ids
                        ]
                        through_model.objects.bulk_create(relations, ignore_conflicts=True)

                    if sample is not None:
                        profile.samples.add(*sample_ids)

            need_to_update_indexes = (
                profile_data.profile_group_ids is not None and
                len(profile_data.profile_group_ids) > 0
            )
            if sample is not None:
                template = SampleManager.get_template_bytes(sample.meta, template_version)
                with tracer.start_as_current_span("update_agent_index_event") if tracer else ContextStub() as span:
                    if need_to_update_indexes:
                        data = {
                            "profileGroups": profile_data.profile_group_ids,
                            "template": {
                                    "id": SampleManager.get_template_id(sample.meta, template_version),
                                    "type": template_version,
                                    "binaryData": base64.b64encode(template).decode('utf-8')
                                }
                        }
                        AgentIndexEvent.objects.create(
                            type='add',
                            workspace_id=workspace_id,
                            profile_id=profile.id,
                            person_id=person.id,
                            data=data
                        )

                with tracer.start_as_current_span("matcher_adapter_add_person_to_index") if tracer else ContextStub():
                    if not settings.DISABLE_WORKSPACE_SEARCH_INDEX:
                        matcher_adapter.add_vectors_to_indexes(
                            index_ids=[workspace_id],
                            vector_ids=[str(person.id)],
                            vectors=[template],
                            old_state={workspace_id: ""},
                            new_state=""
                        )

                    if need_to_update_indexes:
                        matcher_adapter.add_vectors_to_indexes(
                            index_ids=profile_data.profile_group_ids,
                            vector_ids=[str(profile.person_id)],
                            vectors=[template],
                            old_state=indexes_old_states,
                            new_state=now.isoformat()
                        )

            return ProfileCreateOutput(ok=True, profile=profile, is_created=True)

    @strawberry.mutation(permission_classes=[IsHaveAccess, IsWorkspaceActive],
                         description="Create profile by activity_id with profile info")
    def create_profile_by_activity(self, info: Info,
                                   activity_id: type_desc(ID, "Activity id for profile creation"),
                                   profile_data: type_desc(Optional[ProfileInput],
                                                           "Data for profile creation") = None) -> ProfileCreateOutput:
        if profile_data is None:
            profile_data = ProfileInput()
        if profile_data.info is not None and profile_data.fields is not None:
            raise Exception("Only one of the parameters info or fields is required")

        workspace_id = get_workspace_id(info=info)
        profile_data.info = profile_data.info or {}

        with atomic():
            p_settings, created = ProfileSettings.objects.get_or_create(workspace_id=workspace_id)
            if created:
                p_settings.extra_fields = settings.REQUIRED_PROFILE_FIELDS
                p_settings.save()
            available_fields = p_settings.extra_fields

        if profile_data.fields:
            profile_data.info = {item.name.lower(): item.value or None for item in profile_data.fields}

        missed_fields = set(profile_data.info) - set(available_fields)
        if missed_fields:
            raise Exception("One or more profile fields are unavailable")

        if not is_valid_json(profile_data.info, profile_info_scheme):
            raise InvalidJsonRequest()

        if not workspace_id:
            raise BadInputDataException('0x500c29fa')

        workspace = WorkspaceManager.get_workspace(workspace_id)
        template_version = WorkspaceManager.get_template_version(workspace_id)

        source_activity = ActivityManager.get_activity(workspace_id, activity_id)
        try:
            sample_id = ActivityManager.get_sample_id(source_activity)
            sample = SampleManager.get_sample(workspace_id, sample_id)
        except ObjectDoesNotExist:
            raise BadInputDataException('0x358vri3s')

        if not sample.meta:
            raise BadInputDataException('0x358vri3s')

        crop_image = SampleManager.get_face_crop_id(sample.meta)
        if not crop_image:
            raise BadInputDataException('0x358vri3s')

        person_id = ActivityManager.get_parent_process(source_activity).get('object', {}).get('id')
        with atomic():
            if person := (source_activity.person or Person.objects.filter(id=person_id).first()):
                if hasattr(person, 'profile') or settings.ENABLE_PROFILE_AUTOGENERATION:
                    is_created = False if person.profile.info.get('avatar_id') else True
                    profile_manager = ProfileManager(workspace_id, str(person.profile.id))
                    profile_manager.add_labels(label_ids=profile_data.profile_group_ids)
                    profile = profile_manager.update(info={**profile_data.info, 'avatar_id': sample_id})
                else:
                    is_created = False
                    profile = ProfileManager.create(
                        workspace=workspace,
                        person=person,
                        info={**person.info, **profile_data.info, 'avatar_id': sample_id},
                        label_ids=profile_data.profile_group_ids,
                        sample_ids=[sample_id]
                    )
            else:
                is_created = True
                profile_info = {
                    'age': SampleManager.get_age(sample.meta),
                    'gender': SampleManager.get_gender(sample.meta)
                }
                profile_info.update({**profile_data.info, **{'main_sample_id': sample_id, 'avatar_id': sample_id}})

                template = SampleManager.get_template_bytes(sample.meta, template_version)

                profile, person = ProfileManager.create_with_person(workspace=workspace,
                                                                    info=profile_info,
                                                                    label_ids=profile_data.profile_group_ids,
                                                                    sample_ids=[sample_id],
                                                                    person_id=person_id)

                if not settings.DISABLE_WORKSPACE_SEARCH_INDEX:
                    matcher_adapter.add_vectors_to_indexes(
                        index_ids=[workspace_id],
                        vector_ids=[person_id],
                        vectors=[template],
                        old_state={workspace_id: ""},
                        new_state="")

            if not source_activity.person:
                with atomic():
                    locked_activity = ActivityManager.lock_activity(activity=source_activity)
                    locked_activity.person = person
                    locked_activity.save()

            if settings.ENABLE_AGENT:
                pmem = ProfileMutationEventManager(profile)
                pmem.add_profile(profile_data.profile_group_ids)

        return ProfileCreateOutput(ok=True, profile=profile, is_created=is_created)

    @strawberry.mutation(permission_classes=[IsHaveAccess, IsWorkspaceActive])
    def add_profiles_field(self, info: Info, field_input: ModifyExtraField) -> FieldsModifyResult:
        workspace_id = get_workspace_id(info=info)

        with atomic():
            p_settings, created = ProfileSettings.objects.get_or_create(workspace_id=workspace_id)
            if created:
                fields = settings.REQUIRED_PROFILE_FIELDS
            else:
                fields = p_settings.extra_fields

            new_field = field_input.name.lower()
            if not new_field:
                return Exception("Profile field are unavailable")
            elif new_field in fields:
                return Exception("Profile field already exists")

            fields = fields + [new_field]
            p_settings.extra_fields = fields
            p_settings.save()

        return FieldsModifyResult(ok=True, fields=fields)

    @strawberry.mutation(permission_classes=[IsHaveAccess, IsWorkspaceActive])
    def remove_profiles_field(self, info: Info, field_input: ModifyExtraField) -> FieldsModifyResult:
        workspace_id = get_workspace_id(info=info)

        with atomic():
            p_settings, created = ProfileSettings.objects.get_or_create(workspace_id=workspace_id)
            if created:
                fields = settings.REQUIRED_PROFILE_FIELDS
            else:
                fields = p_settings.extra_fields

            new_field = field_input.name.lower()
            if new_field in settings.REQUIRED_PROFILE_FIELDS:
                return Exception("Profile field is required")

            if new_field not in fields:
                return Exception("Profile field does not exists")

            fields.remove(new_field)
            p_settings.extra_fields = fields
            p_settings.save()

            create_deferred_remove_tasks.delay(
                field_name=str(new_field),
                workspace_id=str(workspace_id)
            )
        return FieldsModifyResult(ok=True, fields=fields)
