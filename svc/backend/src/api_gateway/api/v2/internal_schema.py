import strawberry
from strawberry.tools import merge_types

from user_domain.api.v2 import schema as user_schema
from data_domain.api.v2 import schema as data_schema

queries = (
    user_schema.InternalQuery,
    data_schema.InternalQuery,
)
mutations = (
    user_schema.InternalMutation,
    data_schema.InternalMutation,
)

Query = merge_types("Query", queries)
Mutation = merge_types("Mutation", mutations)

schema = strawberry.Schema(query=Query, mutation=Mutation)
