import logging
from datetime import timedelta
from django.db import transaction
from django.conf import settings
from django.db.models.signals import pre_delete, post_delete
from celery import shared_task, execute
from requests.exceptions import ConnectionError as RequestConnectionError
from data_domain.managers import ActivityManager, SampleManager
from data_domain.matcher.main import ActivityMatcherAPI
from data_domain.models import Activity, Sample, BlobMeta, Blob
from person_domain.models import Profile, Person
from platform_lib.utils import utcnow_with_tz, mute_signal
from user_domain.models import Workspace
from data_domain.signals import pre_delete_sample, post_delete_blob_meta

logger = logging.getLogger(__name__)


@shared_task(autoretry_for=(RequestConnectionError,),
             max_retries=4,
             retry_backoff=5,
             retry_backoff_max=700,
             retry_jitter=True)
def add_to_activity_index(index: str, template_version: str, templates_info: list):
    ActivityMatcherAPI.set_base_add(index, template_version, templates_info)


@shared_task
def sample_retention_policy():
    cutoff_date = utcnow_with_tz() - timedelta(seconds=settings.SAMPLE_TTL)
    deleted_samples = 0
    deleted_blobs = 0
    keyset = "1970-01-01 09:00:00.005605+00"

    protected_ids = set()
    if settings.ENABLE_AGENT:
        for activity in Activity.objects.all().iterator():
            protected_ids.update(ActivityManager.get_samples_ids(activity))

    profile_sample_through = Profile.samples.through.objects

    while True:
        bsm_ids = []

        samples_batch = list(
            Sample.objects.filter(
                creation_date__lt=cutoff_date,
                creation_date__gt=keyset
            ).order_by(
                "creation_date"
            ).values_list(
                "id",
                "creation_date"
            )[:settings.DELETE_SAMPLE_BATCH_SIZE]
        )

        if not samples_batch:
            break

        if protected_ids:
            samples_batch = [sample for sample in samples_batch if sample[0] not in protected_ids]
            if not samples_batch:
                keyset = samples_batch[-1][1]
                continue

        set_sample_ids = {sample[0] for sample in samples_batch}
        existing_relations = profile_sample_through.filter(
            sample_id__in=set_sample_ids).values_list("sample_id", flat=True)

        to_delete_ids = set_sample_ids - set(existing_relations)

        if to_delete_ids:
            with transaction.atomic():
                to_delete = Sample.objects.select_for_update().filter(id__in=to_delete_ids)
                [
                    bsm_ids.extend(SampleManager.get_blobmeta_ids(sample.meta))
                    for sample in to_delete
                ]

                with mute_signal(pre_delete, pre_delete_sample, Sample):
                    deleted_samples += to_delete.delete()[0]
                with mute_signal(post_delete, post_delete_blob_meta, BlobMeta):
                    deleted_blobs += Blob.objects.select_for_update().filter(meta__id__in=bsm_ids).delete()[0]

        keyset = samples_batch[-1][1]

    logger.info(f"Sample retention policy: {deleted_samples} samples have been deleted. With {deleted_blobs} blobs.")


@shared_task
def activity_retention_policy():
    def delete_activities(workspace_id: str, template_version: str) -> int:
        with transaction.atomic():
            activities = Activity.objects.filter(
                workspace_id=workspace_id
            )
            if activity_ttl := settings.ACTIVITY_TTL:
                activities = activities.filter(
                    creation_date__lt=(utcnow_with_tz() - timedelta(seconds=activity_ttl))
                )
            else:
                activities = Activity.objects.filter(
                    id__in=activities.order_by("-creation_date").values_list('id')[settings.RETENTION_ACTIVITIES_COUNT:]
                )

            activity_ids = list(activities.values_list("id", flat=True))
            ActivityMatcherAPI.set_base_remove(workspace_id, template_version, activity_ids)

            num, _ = activities.delete()
            return num

    for (workspace_id, template_version) in Workspace.objects.values_list('id', 'config__template_version').iterator():
        try:
            num = delete_activities(str(workspace_id), template_version)
        except Exception as ex:
            logger.error(f'ERROR: activity retention policy:workspace:{workspace_id}\n{ex}')
            continue
        if num:
            logger.info(f'Activity retention policy:workspace:{workspace_id} - {num} activities have been deleted.')


@shared_task
def finalize_dangling_activites():
    with transaction.atomic():
        activities = Activity.objects.select_for_update().filter(
            status=Activity.Type.PROGRESS,
            last_modified__lte=(utcnow_with_tz() - timedelta(seconds=settings.ACTIVITY_FAILED_TIME)).isoformat())

        activities.update(status=Activity.Type.FAILED)
