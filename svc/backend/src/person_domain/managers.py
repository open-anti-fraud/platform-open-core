import uuid
from typing import Iterable, List, Optional, Tuple, Union

from django.core.cache import cache
from django.db import transaction
from django.conf import settings
from django.utils import timezone
from django.db.models import Prefetch
from django.apps import apps
from plib.tracing.utils import get_tracer, ContextStub

from data_domain.managers import SampleManager
from label_domain.managers import LabelManager
from platform_lib.managers import CacheManager
from platform_lib.matcher import MatcherAdapter
from person_domain.models import Person, Profile
from data_domain.models import Sample
from data_domain.matcher.main import ActivityMatcherAPI
from platform_lib.exceptions import BadInputDataException

from user_domain.managers import WorkspaceManager
from user_domain.models import Workspace


matcher_adapter = MatcherAdapter()


class PersonManager:
    def __init__(self, workspace_id: str, person_id: Optional[str] = None):
        self.workspace_id = workspace_id
        self.person = None
        if person_id:
            self.person = Person.objects.get(workspace_id=self.workspace_id, id=person_id)

    def get_person(self) -> Person:
        return self.person

    def get_persons_profile(self) -> Profile:
        return self.person.profile

    @staticmethod
    def create_person(workspace: Workspace, id: Optional[str] = None, profile_info: Optional[dict] = None) -> Person:
        if profile_info is None:
            profile_info = {}

        # Todo: Validate with custom_fields
        # if not is_valid_json(profile_info, profile_info_scheme):
        #     raise InvalidJsonRequest()

        with transaction.atomic():
            if id:
                person = Person.objects.create(id=id, workspace=workspace, info=profile_info)
            else:
                person = Person.objects.create(workspace=workspace, info=profile_info)

        return person

    @classmethod
    def merge_persons(cls, source_person_id, activities_ids, target_person_id):
        pass
        # source_person = Person.objects.get(id=source_person_id)
        # target_person = Person.objects.get(id=target_person_id)
        #
        # if source_person == target_person:
        #     raise BadInputDataException("0x81dcd1d4")
        #
        # with transaction.atomic():
        #     if activities_ids:
        #         filter_ = Q(person=source_person) & Q(id__in=activities_ids)
        #     else:
        #         filter_ = Q(person=source_person)
        #
        #     activities_to_update = Activity.objects.select_for_update().filter(filter_)
        #
        #     if activities_to_update.count() == 0:
        #         raise BadInputDataException("0x581bd57e")
        #
        #     activities_to_update.update(person=target_person)
        #     for activity in activities_to_update:
        #         for blob in activity.blobs:
        #             blob.sample.update(profile=target_person.profile)
        #
        #     if Activity.objects.filter(Q(person=source_person)).count() == 0:
        #         source_person.delete()
        #
        # cls.__set_base(str(target_person.workspace.id))
        # return target_person

    @classmethod
    def delete_persons(cls, workspace_id, person_ids: List[str], skip_validation: Optional[bool] = False):
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("delete_profile_form_base") if tracer else ContextStub() as span:
            with transaction.atomic():
                workspace = Workspace.objects.get(id=workspace_id)

                # TODO Maybe delete this condition?
                # if not skip_validation and not person_ids:
                #     raise BadInputDataException("0xd2ae0ef8")

                persons = Person.objects

                if settings.ENABLE_AGENT:
                    persons = persons.prefetch_related("activities")
                persons = persons.select_for_update().filter(id__in=person_ids, workspace=workspace)

                # TODO Same like the prev?
                if not skip_validation and persons.count() != len(set(person_ids)):
                    raise BadInputDataException("0x51b4c0e2")

                activity_ids = []
                if settings.ENABLE_AGENT:
                    for person in persons:
                        activity_ids += list(person.activities.values_list("id", flat=True))

                persons.delete()

        cls.__set_base_remove(workspace, person_ids, activity_ids)

    @staticmethod
    def __set_base_remove(workspace, person_ids, activity_ids):
        template_version = workspace.config["template_version"]
        if not settings.DISABLE_WORKSPACE_SEARCH_INDEX:
            matcher_adapter.delete_vectors_from_indexes(
                index_ids=[str(workspace.id)],
                vector_ids=person_ids,
                old_state={str(workspace.id): ""},
                new_state=""
            )
        if activity_ids:
            ActivityMatcherAPI.set_base_remove(str(workspace.id), template_version, activity_ids)

    def update(self, info: Optional[dict] = None, sample_ids: Optional[list] = None) -> Person:
        with transaction.atomic():
            locked_person = Person.objects.select_for_update().get(id=self.person.id)
            if info is not None:
                locked_person.info.update(info)
            if sample_ids is not None:
                locked_person.samples.add(*SampleManager.get_sample_ids(self.workspace_id, sample_ids))
            locked_person.save()

        self.person.refresh_from_db()

        return self.person

    def delete_person_main_sample(self):
        with transaction.atomic():
            self.person.samples.remove(self.person.info['main_sample_id'])
            self.person.info['main_sample_id'] = None
            self.person.save()

    def update_person_main_sample(self, sample: Sample):
        with transaction.atomic():
            self.person.samples.add(sample)
            self.person.info['main_sample_id'] = str(sample.id)
            self.person.save()


class ProfileManager:
    def __init__(self, workspace_id: str, profile_id: str):
        self.workspace_id = workspace_id
        self.profile: Profile = Profile.objects.get(workspace_id=self.workspace_id, id=profile_id)
        self.current_groups_ids = None

    @staticmethod
    def _add_to_indexes(indexes: Iterable[str], template_version: str, workspace_id: str, profile: Profile):
        sample = SampleManager.get_sample(
            workspace_id=workspace_id,
            sample_id=profile.info['main_sample_id'],
        )
        template = SampleManager.get_template_bytes(sample.meta, template_version)
        with transaction.atomic():
            now = timezone.now()
            _, indexes_old_states = LabelManager.get_labels_and_touch(
                workspace_id=workspace_id,
                label_ids=indexes,
                now=now
            )

            def on_commit_handler():
                matcher_adapter.add_vectors_to_indexes(
                    index_ids=indexes,
                    vector_ids=[str(profile.person_id)],
                    vectors=[template],
                    old_state=indexes_old_states,
                    new_state=now.isoformat()
                )
            transaction.on_commit(on_commit_handler)

    @staticmethod
    def _remove_from_indexes(indexes: Iterable[str], template_version: str, workspace_id: str, person_ids: List[str]):
        now = timezone.now()
        _, indexes_old_states = LabelManager.get_labels_and_touch(
            workspace_id=workspace_id,
            label_ids=indexes,
            now=now
        )

        def on_commit_handler():
            matcher_adapter.delete_vectors_from_indexes(
                index_ids=indexes,
                vector_ids=person_ids,
                old_state=indexes_old_states,
                new_state=now.isoformat()
            )
        transaction.on_commit(on_commit_handler)

    def get_profile(self) -> Profile:
        return self.profile

    def add_samples(self, sample_ids: Optional[list]) -> Profile:
        if sample_ids is not None:
            with transaction.atomic():
                self.profile.samples.add(*SampleManager.get_sample_ids(self.workspace_id, sample_ids))
                self.profile.save()
        return self.profile

    def delete_profile_main_sample(self):
        with transaction.atomic():
            self.profile.samples.remove(self.profile.info['main_sample_id'])
            self.profile.info['main_sample_id'] = None
            self.profile.save()

    def add_labels(self, label_ids: Optional[list]) -> Profile:
        # WTF? if no label_ids function does nothing
        if not label_ids:
            return self.profile

        template_version = WorkspaceManager.get_template_version(self.workspace_id)
        now = timezone.now()

        with transaction.atomic():
            # Lock profile row before modifying
            self.profile = Profile.objects.select_for_update().get(id=self.profile.id)

            # Get current profile group IDs
            current_groups_ids = set(self.profile.profile_groups.values_list('id', flat=True))

            # Fetch only new labels that need to be added (updates last_modified internally)
            labels_to_update = set(label_ids) - current_groups_ids
            labels_to_index, indexes_old_states = LabelManager.get_labels_and_touch(
                workspace_id=self.workspace_id,
                label_ids=labels_to_update,
                now=now
            )

            self.current_groups_ids = {str(pg_id) for pg_id in current_groups_ids}

            if labels_to_index:
                self.profile.profile_groups.add(*labels_to_index)
                self.profile.save()

            def on_commit_add_to_indexes():
                if sample_id := self.profile.info.get('main_sample_id'):
                    sample = SampleManager.get_sample(
                        workspace_id=self.workspace_id,
                        sample_id=sample_id
                    )
                    template = SampleManager.get_template_bytes(sample.meta, template_version)

                matcher_adapter.add_vectors_to_indexes(
                    index_ids=[str(label.id) for label in labels_to_index],
                    vector_ids=[str(self.profile.person_id)] if sample_id else [],
                    vectors=[template] if sample_id else [],
                    old_state=indexes_old_states,
                    new_state=now.isoformat()
                )

            transaction.on_commit(on_commit_add_to_indexes)

        return self.profile

    def remove_labels(self, label_ids: Optional[list]) -> Profile:
        template_version = WorkspaceManager.get_template_version(self.workspace_id)
        now = timezone.now()
        if label_ids is not None:
            with transaction.atomic():
                self.profile.profile_groups.remove(*LabelManager.get_label_ids(self.workspace_id, label_ids))
                self.profile.save()
                now = timezone.now()
                _, indexes_old_states = LabelManager.get_labels_and_touch(
                    workspace_id=self.workspace_id,
                    label_ids=label_ids,
                    now=now
                )

                def on_commit_delete_from_indexes():
                    matcher_adapter.delete_vectors_from_indexes(
                        index_ids=label_ids,
                        vector_ids=[str(self.profile.person_id)],
                        old_state=indexes_old_states,
                        new_state=now.isoformat()
                    )
                transaction.on_commit(on_commit_delete_from_indexes)

            # self._remove_from_indexes(label_ids, template_version, [str(self.profile.person_id)])

        return self.profile

    @classmethod
    @transaction.atomic
    def create_with_person(cls,
                           workspace: Workspace,
                           info: Optional[dict] = None,
                           label_ids: Optional[list] = None,
                           sample_ids: Optional[list] = None,
                           person_id: Optional[str] = None) -> Tuple[Profile, Person]:
        person = PersonManager.create_person(workspace, person_id, info)
        profile = cls.create(workspace, person, info, label_ids, sample_ids)

        return profile, person

    @classmethod
    @transaction.atomic
    def create(cls, workspace: Workspace, person: Person, info: Optional[dict] = None,
               label_ids: Optional[list] = None, sample_ids: Optional[list] = None):
        profile = Profile.objects.create(workspace=workspace, person=person, info=info)
        profile_manager = cls(str(workspace.id), str(profile.id))
        profile_manager.add_labels(label_ids)
        profile_manager.add_samples(sample_ids)

        return profile_manager.get_profile()

    @staticmethod
    def delete(workspace_id: str, profile_id: str):
        profile = Profile.objects.get(workspace_id=workspace_id, id=profile_id)
        PersonManager.delete_persons(workspace_id, [profile.person.id])

    @staticmethod
    def delete_profiles(
            workspace_id: str,
            profile_ids: List[Union[str, uuid.UUID]],
            skip_validation: Optional[bool] = False
    ):
        Label = apps.get_model('label_domain', 'Label')
        template_version = WorkspaceManager.get_template_version(workspace_id)
        profiles = Profile.objects.filter(workspace_id=workspace_id, id__in=profile_ids).prefetch_related(
            Prefetch(
                'profile_groups',
                queryset=Label.objects.filter(is_active=True),
                to_attr='active_profile_groups'
            )
        )
        if not skip_validation and profiles.count() != len(set(profile_ids)):
            raise BadInputDataException("0x51b4c0e2")
        label_ids = set()
        person_ids = list()

        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("get_labels_from_profile") if tracer else ContextStub() as span:
            for profile in profiles:
                person_ids.append(str(profile.person_id))
                for label in profile.active_profile_groups:
                    label_ids.add(str(label.id))

        PersonManager.delete_persons(workspace_id, person_ids, skip_validation)

        ProfileManager._remove_from_indexes(label_ids, template_version, workspace_id, person_ids)

    def update(self, info: Optional[dict] = None, label_ids: Optional[list] = None,
               sample_ids: Optional[list] = None) -> Profile:
        avatar_id = None
        current_groups_ids = set()
        template_version = WorkspaceManager.get_template_version(self.workspace_id)
        with transaction.atomic():
            locked_profile = Profile.objects.select_for_update().get(id=self.profile.id)
            if info is not None:
                if avatar_id := info.get('avatar_id'):
                    info['main_sample_id'] = str(avatar_id)
                    locked_profile.samples.add(avatar_id)

                locked_profile.info.update(info)
            if label_ids is not None:
                current_groups_ids = {str(pg_id) for pg_id
                                      in locked_profile.profile_groups.values_list('id', flat=True)}
                locked_profile.profile_groups.set(LabelManager.get_label_ids(self.workspace_id, label_ids))
            if sample_ids is not None:
                locked_profile.samples.add(*SampleManager.get_sample_ids(self.workspace_id, sample_ids))
            locked_profile.save()

            main_sample_id = locked_profile.info.get('main_sample_id')

            if avatar_id is not None:
                if label_ids is not None:
                    group_ids = current_groups_ids
                    ProfileManager._add_to_indexes(
                        indexes=group_ids.difference(label_ids),
                        template_version=template_version,
                        workspace_id=self.workspace_id,
                        profile=locked_profile
                    )
                    ProfileManager._remove_from_indexes(
                        indexes=set(label_ids).difference(group_ids),
                        template_version=template_version,
                        workspace_id=self.workspace_id,
                        person_ids=[str(locked_profile.person_id)]
                    )
                else:
                    if not settings.DISABLE_WORKSPACE_SEARCH_INDEX and main_sample_id is not None:

                        sample = SampleManager.get_sample(
                            workspace_id=self.workspace_id,
                            sample_id=main_sample_id,
                        )
                        template = SampleManager.get_template_bytes(sample.meta, template_version)
                        matcher_adapter.add_vectors_to_indexes(
                            index_ids=[self.workspace_id],
                            vector_ids=[str(locked_profile.person_id)],
                            vectors=[template],
                            old_state={self.workspace_id: ""},
                            new_state=""
                        )
            elif label_ids is not None and main_sample_id is not None:
                ProfileManager._add_to_indexes(
                    indexes=set(label_ids).difference(current_groups_ids),
                    template_version=template_version,
                    workspace_id=self.workspace_id,
                    profile=locked_profile
                )
                ProfileManager._remove_from_indexes(
                    indexes=current_groups_ids.difference(label_ids),
                    template_version=template_version,
                    workspace_id=self.workspace_id,
                    person_ids=[str(locked_profile.person_id)]
                )

        self.profile.refresh_from_db()
        return self.profile

    def update_profile_main_sample(self, sample: Sample):
        with transaction.atomic():
            self.profile.samples.add(sample)
            self.profile.info['main_sample_id'] = str(sample.id)
            self.profile.save()


class ProfileCountManager(CacheManager):
    timeout = None  # infinity cache

    @classmethod
    def set_count(cls, count: int) -> None:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("set_count") if tracer else ContextStub() as span:
            cls.cache_set(cls._build_cache_key(), count)

    @classmethod
    def incr(cls) -> int:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("incr") if tracer else ContextStub() as span:
            return cache.incr(cls._build_cache_key())

    @classmethod
    def decr(cls) -> int:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("decr") if tracer else ContextStub() as span:
            return cache.decr(cls._build_cache_key())

    @staticmethod
    def _build_cache_key() -> str:
        return f'count:profile'
