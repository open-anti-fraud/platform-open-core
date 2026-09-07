import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, List, Union, Tuple, Dict

from django.db import transaction
from django.conf import settings
from django.db.models import Q, QuerySet

from label_domain.models import Label
from platform_lib.exceptions import BadInputDataException
from platform_lib.matcher import MatcherAdapter
from user_domain.managers import WorkspaceManager
from plib.tracing.utils import get_tracer, ContextStub

matcher_adapter = MatcherAdapter()


class LabelManager:

    class Types(str, Enum):
        LOCATION = Label.LOCATION
        PROFILE_GROUP = Label.PROFILE_GROUP
        AREA_TYPE = Label.AREA_TYPE

    def __init__(self, workspace_id: str, label_id: Optional[str] = None):
        self.workspace_id = workspace_id
        self.label = None
        if label_id is not None:
            self.label = Label.objects.get(
                workspace_id=self.workspace_id,
                id=label_id
            )

    def get_label(self) -> Optional[Label]:
        return self.label

    @staticmethod
    def get_label_by_id(label_id: Union[str, uuid.UUID]) -> Optional[Label]:
        try:
            return Label.objects.get(id=label_id)
        except Label.DoesNotExist:
            return None

    @staticmethod
    def get_labels_and_touch(workspace_id: str, label_ids: set, now: datetime) -> Tuple[list, Dict[str, str]]:
        """Fetch only the labels that need to be added,
        ensuring they exist, locking them, and updating timestamps efficiently."""

        labels = list(Label.objects.filter(
            workspace_id=workspace_id, id__in=label_ids, type=Label.PROFILE_GROUP
        ).order_by("id").select_for_update())

        indexes_old_states = {str(label.id): label.last_modified.isoformat() for label in labels}

        if len(labels) != len(label_ids):
            raise BadInputDataException("0x573bkd35")

        # Efficiently bulk update last_modified timestamps
        for label in labels:
            label.last_modified = now

        Label.objects.bulk_update(labels, ['last_modified'])

        return labels, indexes_old_states

    @staticmethod
    def get_label_ids(workspace_id: str, label_ids: list) -> QuerySet:
        labels = Label.objects.filter(workspace_id=workspace_id, id__in=label_ids,
                                      type=Label.PROFILE_GROUP).values_list('id', flat=True)
        if labels.count() != len(set(label_ids)):
            raise BadInputDataException("0x573bkd35")
        return labels

    def change_label_info(self, info: Optional[dict] = None, title: Optional[str] = None):
        with transaction.atomic():
            if info is not None:
                self.label.info.update(info)
            if title:
                self.label.title = title
            self.label.save()

    def delete_labels(self, label_ids: List[str], label_type: Types, skip_validation: Optional[bool] = False):
        with transaction.atomic():
            labels = Label.objects.select_for_update().filter(
                id__in=label_ids,
                workspace_id=self.workspace_id,
                type=label_type.value
            )
            if not skip_validation and labels.count() != len(set(label_ids)):
                raise BadInputDataException("0x573bkd35")

            tracer = get_tracer(__name__)
            with tracer.start_as_current_span("label.profiles.clear()") if tracer else ContextStub() as span:
                for label in labels:
                    label.profiles.clear()

            with tracer.start_as_current_span("labels.delete()") if tracer else ContextStub() as span:
                labels.delete()

        for label_id in label_ids:
            matcher_adapter.delete_index(label_id)

    def delete_labels_v3(self, label_ids: List[str], label_type: Types):
        with transaction.atomic():
            labels = Label.objects.select_for_update().filter(
                id__in=label_ids,
                workspace_id=self.workspace_id,
                type=label_type.value
            )

            # for remove links from profiles to archived groups
            for label in labels:
                label.profiles.clear()

            labels.delete()

        for label_id in label_ids:
            matcher_adapter.delete_index(label_id)

    def create_label(self, info: Optional[dict], title: str, label_type: Optional[str] = None) -> Label:
        if info is None:
            info = {}

        with transaction.atomic():
            label = Label.objects.create(
                workspace_id=self.workspace_id,
                type=label_type,
                title=title,
                info=info
            )

            def on_commit_create_index():
                matcher_adapter.init_watchlist(
                    watchlist_id=str(label.id),
                    template_version=WorkspaceManager.get_template_version(self.workspace_id).strip('template'),
                )

            transaction.on_commit(on_commit_create_index)

        return label

    @staticmethod
    def create_default_labels(workspace_id: str,
                              label_titles: List[str] = settings.DEFAULT_PROFILE_LABEL_TITLES) -> List[Label]:
        label_manager = LabelManager(workspace_id=workspace_id)

        return [label_manager.create_label(title=str(title), label_type=Label.PROFILE_GROUP, info={"color": "red.600"})
                for title in label_titles]

    @staticmethod
    def get_label_data(label_id: str) -> Tuple[Optional[str], dict]:
        label = Label.objects.all(Q(is_active__in=[True, False], id=label_id)).first()
        return getattr(label, "title", None), getattr(label, "info", {})
