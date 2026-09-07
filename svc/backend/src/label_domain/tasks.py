from typing import List

from celery import shared_task
from django.db import transaction

from main import settings
from label_domain.managers import LabelManager
from platform_lib.utils import split_list


@shared_task(autoretry_for=(Exception,),
             max_retries=4,
             retry_backoff=5,
             retry_backoff_max=30,
             retry_jitter=True)
def delete_profile_groups(workspace_id: str, profile_group_ids: List[str]):
    lm = LabelManager(workspace_id=workspace_id)
    for delete_list in split_list(profile_group_ids, settings.DELETE_BATCH_SIZE):
        with transaction.atomic():
            lm.delete_labels(delete_list, LabelManager.Types.PROFILE_GROUP, skip_validation=True)
