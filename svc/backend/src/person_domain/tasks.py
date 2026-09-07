import copy
import time
import uuid
from typing import Tuple, List, Optional

from celery import shared_task
from django.apps import apps
from django.db import transaction
from django.db.models import F, Func
from main import settings

from person_domain.managers import ProfileCountManager, ProfileManager
from person_domain.models import Person, Profile
from person_domain.utils import ProfileMutationEventManager
from data_domain.managers import SampleManager
from label_domain.managers import LabelManager
from platform_lib.utils import split_list
from platform_lib.context_manager import context_manager


@shared_task
def duplicate_persons(person_id: str, count: int, template_version: str, insert_batch: int = 10000):
    sample_model = apps.get_model('data_domain', 'Sample')
    blob_model = apps.get_model('data_domain', 'Blob')
    blob_meta_model = apps.get_model('data_domain', 'BlobMeta')
    label_model = apps.get_model('label_domain', 'Label')

    person = Person.objects.get(id=person_id)
    workspace_id = person.workspace_id
    profile = person.profile
    main_sample = sample_model.objects.get(id=profile.info['main_sample_id'])
    label_id = person.profile.profile_groups.values_list('id', flat=True)[0]
    label = label_model.objects.get(id=label_id)

    try:
        image = SampleManager.get_image(main_sample.meta)
    except AttributeError:
        image = None

    template_id = SampleManager.get_template_id(main_sample.meta, template_version)
    template = blob_meta_model.objects.get(id=template_id).blob.data

    def create_person(person_,
                      profile_m,
                      main_sample_,
                      workspace_id_) -> (Person, Profile, sample_model, Tuple[blob_meta_model], Tuple[blob_model]):

        person_info = copy.deepcopy(person_.info)
        profile_info = copy.deepcopy(profile_m.info)
        sample_meta = copy.deepcopy(main_sample_.meta)

        profile_id = str(uuid.uuid4())
        person_id = str(uuid.uuid4())
        sample_id = str(uuid.uuid4())
        image_bm_id = str(uuid.uuid4())
        template_bm_id = str(uuid.uuid4())

        person_info['main_sample_id'] = person_info['avatar_id'] = sample_id
        profile_info['main_sample_id'] = profile_info['avatar_id'] = sample_id

        if sample_meta["$image"] is not None:
            sample_meta['$image']['id'] = image_bm_id
        sample_meta['objects@common_capturer_uld_fda'][0]['templates'][f'${template_version}']["id"] = template_bm_id

        template_blob = blob_model(data=template)
        template_bm = blob_meta_model(id=template_bm_id,
                                      meta={"type": template_version, "format": "NDARRAY"},
                                      blob=template_blob,
                                      workspace_id=workspace_id_)

        image_blob = blob_model(data=image)
        image_bm = blob_meta_model(id=image_bm_id,
                                   meta={"type": "image", "format": "NDARRAY"},
                                   blob=image_blob,
                                   workspace_id=workspace_id_)

        main_sample = sample_model(id=sample_id, workspace_id=workspace_id_, meta=sample_meta)

        person_in_base = Person(id=person_id, workspace_id=workspace_id_, info=person_info)
        many_to_many_per = Person.samples.through(person_id=person_id, sample_id=sample_id)

        profile_in_base = Profile(id=profile_id, workspace_id=workspace_id_, info=profile_info, person_id=person_id)
        many_to_many_prof = Profile.samples.through(profile_id=profile_id, sample_id=sample_id)
        many_to_many_pg = Profile.profile_groups.through(profile=profile_in_base, label=label)

        return (
            person_in_base,
            profile_in_base,
            main_sample,
            (image_bm, template_bm),
            (image_blob, template_blob),
            many_to_many_per,
            many_to_many_prof,
            many_to_many_pg
        )

    batch = insert_batch

    if batch > count:
        increase = count
    else:
        increase = batch

    remain = count

    start = time.time()
    while remain != 0:
        print(f'Creating {increase} profiles')
        person_list = []
        profile_list = []
        main_sample_list = []
        blob_meta_list = []
        blob_list = []
        mtm_profile = []
        mtm_person = []
        mtm_pgs = []

        mean_time = 0

        start_loop = time.time()

        for _ in range(increase):
            start_loop_inner = time.time()
            cr_person, cr_profile, cr_sample, cr_bl_metas, cr_bl, mtm_per, mtm_prof, mtm_profg = create_person(
                person,
                profile,
                main_sample,
                workspace_id
            )

            end = time.time() - start_loop_inner
            mean_time += end

            person_list.append(cr_person)
            profile_list.append(cr_profile)
            main_sample_list.append(cr_sample)
            blob_meta_list += cr_bl_metas
            blob_list += cr_bl
            mtm_profile.append(mtm_prof)
            mtm_person.append(mtm_per)
            mtm_pgs.append(mtm_profg)

        print(f'Func creation mean: {mean_time / increase}')
        print(f'Objects created: {time.time() - start_loop}')

        start_creation = time.time()

        with transaction.atomic():
            start_bulk = time.time()
            blob_model.objects.bulk_create(blob_list, batch)
            print(f'Create blob: {time.time() - start_bulk}')
            start_bulk = time.time()
            blob_meta_model.objects.bulk_create(blob_meta_list, batch)
            print(f'Create blob meta: {time.time() - start_bulk}')
            start_bulk = time.time()
            sample_model.objects.bulk_create(main_sample_list, batch)
            print(f'Create sample: {time.time() - start_bulk}')
            start_bulk = time.time()
            Profile.objects.bulk_create(profile_list, batch)
            print(f'Create profile: {time.time() - start_bulk}')
            start_bulk = time.time()
            Person.objects.bulk_create(person_list, batch)
            print(f'Create person: {time.time() - start_bulk}')
            start_bulk = time.time()
            Profile.samples.through.objects.bulk_create(mtm_profile, batch)
            print(f'Create profile sample: {time.time() - start_bulk}')
            start_bulk = time.time()
            Person.samples.through.objects.bulk_create(mtm_person, batch)
            print(f'Create person sample: {time.time() - start_bulk}')
            Profile.profile_groups.through.objects.bulk_create(mtm_pgs, batch)
            print(f'Create profile groups: {time.time() - start_bulk}')

        print(f'Create by bulk {increase} profiles. By {time.time() - start_creation} seconds')

        remain -= increase

        if batch > remain:
            increase = remain
        else:
            increase = batch

    print(f'Total task time: {time.time() - start}')


@shared_task
def cache_update():
    count = Profile.objects.count()
    ProfileCountManager.set_count(count)


@shared_task(autoretry_for=(Exception,),
             max_retries=4,
             retry_backoff=5,
             retry_backoff_max=30,
             retry_jitter=True)
def deferred_delete(
        workspace_id: str,
        x_request_id: str,
        profile_ids: Optional[List[str]] = None,
        profile_group_ids: Optional[List[str]] = None,
):
    context_manager.set_context_var("request_id", x_request_id)
    delete_batch_size = settings.DELETE_BATCH_SIZE
    if profile_ids:
        for delete_list in split_list(profile_ids, delete_batch_size):
            with transaction.atomic():
                ProfileManager.delete_profiles(workspace_id, delete_list, skip_validation=True)
    if profile_group_ids:
        lm = LabelManager(workspace_id=workspace_id)
        for delete_list in split_list(profile_group_ids, delete_batch_size):
            with transaction.atomic():
                lm.delete_labels(delete_list, LabelManager.Types.PROFILE_GROUP, skip_validation=True)


@shared_task(autoretry_for=(Exception,),
             max_retries=4,
             retry_backoff=5,
             retry_backoff_max=30,
             retry_jitter=True)
def create_deferred_remove_tasks(field_name: str, workspace_id: str, batch_size: int = 10000):
    profiles_ids = Profile.objects.filter(workspace_id=workspace_id).values_list("id", flat=True)
    queue_size = len(profiles_ids)
    progress = 0
    while queue_size:
        if queue_size > batch_size:
            profiles_ids_batch = profiles_ids[progress:progress + batch_size]
            deferred_remove_profiles_field.delay(
                field_name=field_name,
                workspace_id=workspace_id,
                profiles_ids=profiles_ids_batch
            )
            progress += batch_size
            queue_size -= batch_size
        else:
            deferred_remove_profiles_field.delay(
                field_name=field_name,
                workspace_id=workspace_id,
                profiles_ids=profiles_ids[progress:]
            )
            queue_size = 0


@shared_task(autoretry_for=(Exception,),
             max_retries=4,
             retry_backoff=5,
             retry_backoff_max=30,
             retry_jitter=True)
def deferred_remove_profiles_field(field_name: str, workspace_id: str, profiles_ids: list):
    Profile.objects.filter(workspace_id=workspace_id, id__in=profiles_ids).update(
        info=Func(
            F("info") - field_name,
            function="jsonb")
    )


@shared_task
def deferred_remove_profiles_from_groups(profiles_ids: list, group_ids: list):
    profiles_ids = set(profiles_ids)
    for profile_id in profiles_ids:
        profile = Profile.objects.get(id=profile_id)
        profile_groups_ids = list(map(str, profile.profile_groups.all().values_list('id', flat=True)))
        if settings.ENABLE_AGENT:
            pmem = ProfileMutationEventManager(profile)
            if pmem.prev_group_count:
                pmem.update_profile(list(set(profile_groups_ids) - set(group_ids)))
            else:
                pmem.forced_delete_profile()
