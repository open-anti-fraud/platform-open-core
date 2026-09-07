from label_domain.api.v3.mutations import Mutation
from label_domain.api.v3.queries import Query
import strawberry


schema = strawberry.Schema(query=Query, mutation=Mutation)
