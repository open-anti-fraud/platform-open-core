import datetime
from typing import Optional, List

import strawberry
from django.conf import settings
from strawberry import ID

from data_domain.api.v2.types import SampleOutput
from label_domain.api.v3.types import ProfileGroupOutput
from person_domain.utils import get_age_from_birthday
from platform_lib.types import JSON
from platform_lib.utils import get_collection, convert_old_sample_to_new


@strawberry.type(description="Object that represents a detected person"
                             " and contains all the information about that person")
class ProfileOutput:
    description_name = "profiles"

    id: ID

    last_modified: datetime.datetime = strawberry.field(description="Last profile modification date")
    creation_date: datetime.datetime = strawberry.field(description="Profile creation date")

    @strawberry.field(description="Info about human")
    def info(self) -> JSON:
        if birthday := self.info.get('birthday'):
            self.info['age'] = get_age_from_birthday(birthday)
        return self.info

    @strawberry.field(description="Objects that stored processed info about human blobs.")
    def samples(self, offset: int = 0, limit: int = settings.QUERY_LIMIT) -> Optional[List[SampleOutput]]:
        limit = min(limit, settings.QUERY_LIMIT)
        s = self.samples.all()[offset:offset + limit]
        for sample in s:
            sample.meta = convert_old_sample_to_new(sample.meta)
        return s

    @strawberry.field(description="Groups the profile belongs to")
    def profile_groups(self, offset: int = 0, limit: int = settings.QUERY_LIMIT) -> Optional[List[ProfileGroupOutput]]:
        limit = min(limit, settings.QUERY_LIMIT)
        try:
            pg = self._prefetched_objects_cache[self.profile_groups.prefetch_cache_name]
        except (AttributeError, KeyError):
            pg = self.profile_groups.filter()
        return pg[offset:offset + limit]


ProfilesCollection = strawberry.type(get_collection(ProfileOutput, "ProfilesCollection"),
                                     description="Filtered profiles collection and total profiles count")

profile_map = {'personInfo': 'person_info', 'groupsIds': 'profile_groups__id__in', 'mainSampleId': 'main_sample_id',
               'avatarId': 'avatar_id', 'creationDate': 'creation_date', 'lastModified': 'last_modified',
               'info__age': 'age'}
