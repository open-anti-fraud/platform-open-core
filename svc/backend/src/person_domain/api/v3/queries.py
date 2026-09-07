import strawberry
from django.db.models import Func, IntegerField, DateField
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Coalesce, Cast

from person_domain.api.utils import optimizer_profile_queryset_v3
from person_domain.api.v3.types import ProfilesCollection, profile_map
from person_domain.models import Profile
from platform_lib.strawberry_auth.permissions import IsHaveAccessUpd
from platform_lib.utils import get_workspace_id, get_paginated_model, paginated_field_generator, snake
from plib.tracing.utils import get_tracer, ContextStub


def resolve_profiles_raw(*args, **kwargs) -> ProfilesCollection:
    info = kwargs.get('info')
    ids = kwargs.get('ids')
    order = kwargs.get('order')
    offset = kwargs.get('offset')
    limit = kwargs.get('limit')
    model_filter = kwargs.get('filter')
    profile_selections = next(filter(lambda x: x.name == 'profiles', info.selected_fields))
    try:
        items_selections = next(filter(lambda x: x.name == 'collectionItems', profile_selections.selections))
        profile_fields = [snake(field.name) for field in items_selections.selections]
    except StopIteration:
        profile_fields = []
    try:
        next(filter(lambda x: x.name == 'totalCount', profile_selections.selections))
        get_total_count = True
    except StopIteration:
        get_total_count = False
    workspace_id = get_workspace_id(info)

    tracer = get_tracer(__name__)
    with tracer.start_as_current_span("profile_annotate") if tracer else ContextStub() as span:
        predefine_queryset = Profile.objects.annotate(
            age=Coalesce(
                Func(
                    Func(
                        Cast(KeyTextTransform('birthday', 'info'), output_field=DateField()),
                        function='AGE'),
                    function='DATE_PART', template="%(function)s('year', %(expressions)s)",
                    output_field=IntegerField()
                ),
                Cast(
                    KeyTextTransform('age', 'info'),
                    output_field=IntegerField()
                )
            )
        )
    with tracer.start_as_current_span("profile_query") if tracer else ContextStub() as span:
        total_count, profiles = get_paginated_model(model_class=Profile,
                                                    workspace_id=workspace_id,
                                                    ids=ids,
                                                    order=order,
                                                    offset=offset,
                                                    limit=limit,
                                                    model_filter=model_filter,
                                                    filter_map=profile_map,
                                                    predefine_queryset=predefine_queryset,
                                                    optimize_query=optimizer_profile_queryset_v3,
                                                    get_total_count=get_total_count,
                                                    selected_fields=profile_fields)
    return ProfilesCollection(total_count=total_count, collection_items=profiles)


resolve_profiles = paginated_field_generator(resolve_profiles_raw)


@strawberry.type
class Query:
    profiles: ProfilesCollection = strawberry.field(permission_classes=[IsHaveAccessUpd],
                                                    resolver=resolve_profiles,
                                                    description="List of profiles")
