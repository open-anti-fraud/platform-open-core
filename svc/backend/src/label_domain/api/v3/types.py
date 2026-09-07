import datetime
from enum import Enum
from typing import Optional, List

import strawberry
from strawberry import ID

from main import settings
from platform_lib.utils import get_collection


@strawberry.enum()
class ProfileGroupColor(Enum):
    red_600 = 'red.600'
    purple_700 = 'purple.700'
    gray_400 = 'gray.400'
    gray_600 = 'gray.600'
    blue_500 = 'blue.500'
    orange_400 = 'orange.400'
    green_600 = 'green.600'
    white = 'white'
    orange_700 = 'orange.700'
    yellow_300 = 'yellow.300'
    pink_300 = 'pink.300'
    green_200 = 'green.200'
    red = 'red.300'
    black = 'black'
    green = 'green.400'
    pink = 'pink.600'
    cyan_400 = 'cyan.400'


@strawberry.input(description="Information about group")
class ProfileGroupInfoInput:
    color: Optional[ProfileGroupColor] = strawberry.field(description="Group color", default=ProfileGroupColor.white)


@strawberry.input(description="Information needed to create a profile group")
class ProfileGroupInput:
    title: str = strawberry.field(description='Profile group title')
    info: Optional[ProfileGroupInfoInput] = strawberry.field(description='Additional profile group info',
                                                             default=None)


@strawberry.type(description="Information about group")
class ProfileGroupInfo:

    @strawberry.field(description="Group color")
    def color(root) -> Optional[ProfileGroupColor]:
        return ProfileGroupColor(root.get('color', 'white'))


@strawberry.type(description="Information about the profile group, e.g. name, linked profiles, etc. v3")
class ProfileGroupOutput:
    description_name = "profile groups"

    id: ID
    title: str = strawberry.field(description="Profile group title")
    info: ProfileGroupInfo = strawberry.field(description="Profile group info")

    last_modified: datetime.datetime = strawberry.field(description="Last profile group modification date")
    creation_date: datetime.datetime = strawberry.field(description="Profile group creation date")

    @strawberry.field(description="Ids of linked profiles")
    def profile_ids(self, offset: int = 0, limit: int = settings.QUERY_LIMIT) -> Optional[List[ID]]:
        limit = min(limit, settings.QUERY_LIMIT)
        return self.profiles.values_list('id', flat=True)[offset:offset + limit]


ProfileGroupsCollection = strawberry.type(get_collection(ProfileGroupOutput, 'ProfileGroupsCollection'),
                                          description="Filtered profile group collection and total profile group count")

label_map = {'creationDate': 'creation_date', 'lastModified': 'last_modified'}
