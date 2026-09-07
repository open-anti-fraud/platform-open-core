import strawberry
from strawberry.tools import merge_types

from data_domain.api.v3 import schema as data_schema
from person_domain.api.v3 import schema as person_schema
from label_domain.api.v3 import schema as label_schema

Query = merge_types("Query", (data_schema.Query,
                              person_schema.Query,
                              label_schema.Query))
Mutation = merge_types("Mutation", (person_schema.Mutation,
                                    data_schema.Mutation,
                                    label_schema.Mutation))
schema = strawberry.Schema(query=Query, mutation=Mutation)
