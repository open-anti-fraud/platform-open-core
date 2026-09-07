import dataclasses
from typing import List

import jsonschema
import strawberry
from django.db.transaction import atomic
from strawberry import ID
from strawberry.types import Info

from label_domain.api.v3.types import ProfileGroupInput, ProfileGroupOutput, ProfileGroupInfoInput
from label_domain.managers import LabelManager
from label_domain.models import Label
from platform_lib.strawberry_auth.permissions import IsHaveAccessUpd, IsWorkspaceActive
from platform_lib.types import MutationResult
from platform_lib.utils import get_workspace_id, type_desc, custom_asdict_factory
from platform_lib.validation.schemes import profile_group_scheme


@strawberry.type
class Mutation:
    @strawberry.mutation(permission_classes=[IsHaveAccessUpd, IsWorkspaceActive],
                         description="Create new profile group")
    def create_profile_group(
            self,
            info: Info,
            profile_group_data: type_desc(ProfileGroupInput, "Profile group data")
    ) -> ProfileGroupOutput:
        workspace_id = get_workspace_id(info)

        group_info = dataclasses.asdict(profile_group_data.info or ProfileGroupInfoInput(),
                                        dict_factory=custom_asdict_factory)
        jsonschema.validate(group_info, profile_group_scheme)

        with atomic():
            manager = LabelManager(workspace_id=workspace_id)
            label = manager.create_label(group_info,
                                         profile_group_data.title,
                                         label_type=str(Label.PROFILE_GROUP))

        return label  # noqa

    @strawberry.mutation(permission_classes=[IsHaveAccessUpd, IsWorkspaceActive], description="Delete profile group")
    def delete_profile_group(self,
                             info: Info,
                             group_ids: type_desc(List[ID], "Profile group ids to delete")) -> MutationResult:
        workspace_id = get_workspace_id(info)

        with atomic():
            lm = LabelManager(workspace_id=workspace_id)
            lm.delete_labels_v3(group_ids, LabelManager.Types.PROFILE_GROUP)

        return MutationResult(ok=True)

    @strawberry.mutation(permission_classes=[IsHaveAccessUpd, IsWorkspaceActive],
                         description="Update profile group info")
    def update_profile_group_info(
            self,
            info: Info,
            profile_group_id: type_desc(ID, "Updated profile group id"),
            profile_group_data: type_desc(ProfileGroupInput, "Data for update")
    ) -> ProfileGroupOutput:
        workspace_id = get_workspace_id(info)

        if profile_group_data.info is not None:
            group_info = dataclasses.asdict(profile_group_data.info,
                                            dict_factory=custom_asdict_factory)
            jsonschema.validate(group_info, profile_group_scheme)
        else:
            group_info = None

        manager = LabelManager(workspace_id=workspace_id, label_id=profile_group_id)
        manager.change_label_info(info=group_info, title=profile_group_data.title)

        return manager.get_label() # noqa
