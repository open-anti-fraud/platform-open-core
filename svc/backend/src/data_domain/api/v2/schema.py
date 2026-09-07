import strawberry

from data_domain.api.v2.mutations import Mutation, InternalMutation
from data_domain.api.v2.queries import Query, InternalQuery

internal_schema = strawberry.Schema(query=InternalQuery, mutation=InternalMutation)
schema = strawberry.Schema(query=Query, mutation=Mutation)
