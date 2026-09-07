import strawberry
from strawberry.schema.config import StrawberryConfig

from data_domain.api.v3.mutations import Mutation
from data_domain.api.v3.queries import Query

schema = strawberry.Schema(query=Query, mutation=Mutation)
