import copy
import json
import uuid
import base64
import re
import threading
from collections import ChainMap
from inspect import Signature, Parameter
from contextlib import contextmanager

from enum import Enum
from pathlib import Path
from functools import wraps

import django
import strawberry
import pytz
import requests
from django.urls import reverse
from django.utils.html import format_html, escape
from plib.tracing.utils import get_current_tracing_context, ContextStub, get_tracer
from PIL import ImageOps
from abc import ABC, abstractmethod
from channels.layers import get_channel_layer
from dateutil import parser as date_parser
from datetime import datetime, timezone
from functools import reduce, wraps
# TODO: add Annotated type after python>=3.9
from typing import List, Optional, Tuple, Union, Dict, Any, Callable
from strawberry import ID
from strawberry.arguments import UNSET
from strawberry.types import Info
from graphql import GraphQLError
from django.db import models
from django.db.models import Q, signals
from django.db.transaction import atomic
from asgiref.sync import SyncToAsync, async_to_sync

from io import BytesIO
from PIL.Image import open as open_image, Image
from PIL import UnidentifiedImageError
from django.conf import settings

from platform_lib.exceptions import BadInputDataException
from platform_lib.types import emotions_map, keypoints_map, JSONString, WithArchived, EyesInput, PointInputType, \
    CountList, FilterLookupCustom
from platform_lib.validation import is_valid_json
from platform_lib.matcher import MatcherAdapter
from strawberry_django import utils
from strawberry_django.fields.field import StrawberryDjangoField
from strawberry_django.pagination import apply as apply_pagination
from strawberry_django.filters import apply as apply_filters
from strawberry_django.ordering import apply as apply_ordering
from django.db.models import QuerySet
import asyncio


class OffsetPaginationInput:
    limit: int = settings.QUERY_LIMIT
    offset: int = 0


class SampleObjectsName(str, Enum):
    CAPTURER = Path(settings.CAPTURER_NAME).stem


def _get_type_from_count(type: Any):
    items_type = [f.type for f in type._type_definition._fields if f.name == 'collection_items']

    return utils.unwrap_type(items_type[0]) if items_type else None


class FilterByWorkspaceMixin:
    def get_queryset(self, queryset, info, **kwargs):
        workspace_id = get_workspace_id(info)
        return queryset.filter(workspace_id=workspace_id)


class StrawberryDjangoCountList(StrawberryDjangoField):
    @property
    def is_list(self):
        return True

    @property
    def django_model(self):
        # Get model type from collection_items field of CountList type
        if type_ := _get_type_from_count(self.type):
            return utils.get_django_model(type_)
        else:
            return None

    def get_queryset(self, queryset: QuerySet[Any], info, pagination=OffsetPaginationInput, filters=UNSET, order=UNSET,
                     **kwargs):
        def none_to_unset(filter_):
            for key_, value_ in vars(filter_).items():
                if value_ is None:
                    setattr(filter_, key_, UNSET)

        # prepare filters to replace none values to UNSET
        for value in vars(filters).values():
            if isinstance(value, FilterLookupCustom):
                none_to_unset(value)

        queryset = apply_filters(filters, queryset)
        queryset = apply_ordering(order, queryset)

        optimize_queryset_by_custom_joins = None
        get_queryset = None
        if type_ := _get_type_from_count(self.type):
            get_queryset = getattr(type_, 'get_queryset', None)
            optimize_queryset_by_custom_joins = getattr(type_, 'optimize_queryset_by_custom_joins', None)

        # use additional filters from type's get_queryset method.
        # MUST BE AFTER other filters and ordering because of query optimization
        if get_queryset:
            queryset = get_queryset(self, queryset, info, **kwargs)

        self.total_count = queryset.count()  # noqa calculate and apply total_count to result for futher representation
        pagination.limit = min(pagination.limit, settings.QUERY_LIMIT)
        queryset = apply_pagination(pagination, queryset)

        # MUST BE AFTER pagination for proper limiting requested entities
        if optimize_queryset_by_custom_joins:
            queryset = optimize_queryset_by_custom_joins(self, queryset, info, **kwargs)

        return queryset

    def resolver(self, info, source, **kwargs):
        qs = super().resolver(info, source, **kwargs)
        return CountList[self.type](
            total_count=self.total_count,  # noqa get total_count from get_queryset
            collection_items=qs,
        )


def from_dict_to_class(attrs: Dict, class_name: Optional[str] = 'ClassDict') -> object:
    return type(class_name, (), attrs)


def isoformat_time(time: Union[str, int], time_format: Optional[str] = None) -> str:
    """
    Convert time string or timestamp int to isoformat string use format to convert form string if presented
    Parameters
    ----------
    time: Union[str, int]
        time in string or int timestamp format
    time_format: Optional[str]
        time format if presented
    Returns
    -------
    str:
        time in isoformat string
    """
    if type(time) is int:
        return datetime.fromtimestamp(time / 1000.0, tz=timezone.utc).isoformat()
    if type(time) is str:
        if time_format is not None:
            return datetime.strptime(time, time_format).isoformat()
        else:
            return datetime.fromisoformat(time).isoformat()


def custom_asdict_factory(data):

    def convert_value(obj):
        if isinstance(obj, Enum):
            return obj.value
        return obj

    return dict((k, convert_value(v)) for k, v in data)


def get_paginated_model(model_class,
                        workspace_id: Union[str, uuid.UUID],
                        ids: Union[list, set] = None,
                        order: list = None,
                        offset: int = 0,
                        limit: int = settings.QUERY_LIMIT,
                        model_filter: dict = None,
                        filter_map: dict = None,
                        model_exclude: dict = None,
                        with_archived: str = None,
                        predefine_queryset: QuerySet = None,
                        optimize_query: Callable = None,
                        get_total_count: bool = True,
                        selected_fields=None) -> Tuple[int, List[Any]]:
    query_filter = Q(workspace__id=workspace_id)

    if ids is not None:
        query_filter &= Q(id__in=ids)
    if model_exclude:
        query_filter &= ~get_filters(model_exclude, filter_map)
    if model_filter:
        query_filter &= get_filters(model_filter, filter_map)

    if predefine_queryset is not None:
        start_objects = predefine_queryset
    else:
        start_objects = model_class.objects

    if with_archived is None:
        queryset = start_objects.filter(query_filter).distinct()
    elif with_archived.lower() == 'all':
        query_filter &= Q(is_active__in=[True, False])
        queryset = start_objects.all(query_filter).distinct()
    elif with_archived.lower() == 'archived':
        query_filter &= Q(is_active=False)
        queryset = start_objects.all(query_filter).distinct()
    else:
        queryset = start_objects.filter(query_filter).distinct()

    tracer = get_tracer(__name__)
    with tracer.start_as_current_span("count") if tracer else ContextStub() as span:
        if get_total_count:
            # https://code.djangoproject.com/ticket/30685
            total_count = start_objects.filter(query_filter).values("id").order_by().count()
        else:
            total_count = 0

    if limit is None or limit > settings.QUERY_LIMIT:
        limit = settings.QUERY_LIMIT

    if order is not None:
        order = [
            reduce(lambda string, pair: string.replace(pair[0], pair[1]),
                   (filter_map or {}).items(),
                   order_string) for order_string in order
        ]
        queryset = queryset.order_by(*order)

    with tracer.start_as_current_span("optimize_query_profile") if tracer else ContextStub() as span:
        if optimize_query is not None:
            queryset = optimize_query(queryset)
    if selected_fields is not None:
        queryset = queryset.only(*selected_fields)
    with tracer.start_as_current_span("get_slice") if tracer else ContextStub() as span:
        # list добавлен чтобы инициализация и запрос в базу были в этом методе - для удобства трэйсинга
        sliced_queryset = list(get_slice(queryset, offset=offset, limit=limit))

    return total_count, sliced_queryset


def type_desc(type_, description):
    return type_
    # Not supported on python 3.8 only above 3.9
    # return Annotated[type_, strawberry.argument(description=description)]


def get_process_object(meta: dict, object_class: str) -> dict:
    return next(
        filter(lambda track: track.get('object', {}).get('class', '') == object_class, meta['processes']), {}
    )


def get_collection(gr_type: object, collection_name: str) -> object:
    if name := getattr(gr_type, "description_name", None):
        class_name = name
    else:
        class_name = gr_type.__name__

    meta_class = type(collection_name, (), {
        'total_count': strawberry.field(description=f'Total count of {class_name}'),
        'collection_items': strawberry.field(description=f'Filtered collection of {class_name}'),
    })
    meta_class.__annotations__ = {"total_count": int, "collection_items": List[gr_type]}

    return meta_class


# TODO remove generator
def paginated_field_generator(func, extra_args: Optional[Dict] = None, with_archived: Optional[bool] = False):
    description = {
        "ids": "Ids of objects",
        "filter": "Json filter",
        "order": "Order for objects",
        "offset": "Offset for objects",
        "limit": "Limit for objects",
        "with_archived": "Show archived objects or not"
    }

    def wrap_arg_in_param(args: ChainMap) -> List[Parameter]:
        return [Parameter(name=param_name,
                          annotation=param_annotation,
                          kind=Parameter.POSITIONAL_OR_KEYWORD,
                          default=None) for param_name, param_annotation in args.items()]

    signature_args = {
        'info': Info,
        'ids': type_desc(Optional[List[Optional[ID]]], description['ids']),
        'filter': type_desc(Optional[JSONString], description['filter']),
        'order': type_desc(Optional[List[Optional[str]]], description['order']),
        'offset': type_desc(Optional[int], description['offset']),
        'limit': type_desc(Optional[int], description['limit']),
    }

    if with_archived:
        signature_args['with_archived'] = type_desc(Optional[WithArchived], description["with_archived"])

    arg_chain_map = ChainMap(signature_args, extra_args or {})

    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    wrapper.__signature__ = Signature(parameters=wrap_arg_in_param(arg_chain_map))

    # additional annotate because strawberry need it
    wrapper.__annotations__ = dict(arg_chain_map)

    return wrapper


def get_filters(filter_dict, filter_map=None):
    if filter_map:
        new_dict = {}
        for key in filter_dict.keys():
            new_key = reduce(lambda string, pair: string.replace(pair[0], pair[1]), filter_map.items(), key)
            new_dict[new_key] = filter_dict[key]
        filter_dict = new_dict
    query_filter = Q()
    for key, value in filter_dict.items():
        if key == 'and':
            query_filter &= get_filters(value, filter_map)
        elif key == 'or':
            or_filter = Q()
            for i in filter_dict['or']:
                or_filter |= get_filters(i, filter_map)
            query_filter &= or_filter
        else:
            query_filter &= Q(**{key: value})
    return query_filter


def get_slice(queryset, offset: int = 0, limit: int = settings.QUERY_LIMIT):
    if offset is not None:
        queryset = queryset[offset:]
    if limit is not None:
        queryset = queryset[:limit]

    return queryset


class ApiError(GraphQLError):
    NOT_AUTHORIZED = 1
    WRONG_TOKEN = 2
    SERVICE_TOKEN_ERROR = 3
    WORKSPACE_NOT_FOUND = 4
    PASSWORDS_DO_NOT_MATCH = 5
    USER_EXISTS = 6
    INVALID_PASSWORD = 7
    INACTIVE_USER = 8

    def __init__(self, message, error_code=None, *args, **kwargs):
        self.error_code = error_code
        super().__init__(message, *args, **kwargs)


def get_token(info: Info) -> str:
    token = info.context.request.META.get('HTTP_TOKEN') or info.context.request.session.get('token')
    return str(token) if token is not None else None


def get_user(info: Info):
    return info.context.request.user


def get_workspace_id(info: Info) -> Optional[str]:
    workspace_id = info.context.request.GET.get('workspace') or\
        info.context.request.GET.get('workspace_id') or \
        info.context.request.META.get('workspace_id')

    return None if workspace_id is None else str(workspace_id)


def get_workspace_object(info: Info):
    return info.context.request.META.get('workspace_object')


def get_license_id(info: Info) -> Optional[str]:
    license_id = info.context.request.GET.get('license') or info.context.request.GET.get('license_id') or \
                 info.context.request.META.get('license_id')

    return str(license_id) if license_id is not None else None


def get_content_type(content_type_header):
    # Content type may be like this "application/json;charset=UTF-8"
    return content_type_header.split(';')[0]


def graphql_request_extract(info, var):
    content_type = get_content_type(info.context.request.content_type)
    if content_type == 'application/json':
        return json.loads(info.context.request.body).get(var)
    return info.context.request.POST.get(var)


def extract_variables(info):
    return graphql_request_extract(info, 'variables')


def extract_query(info):
    return graphql_request_extract(info, 'query')


def utcnow_with_tz():
    """
    Return current utc time with timezone info
    :return: datetime
    """
    return datetime.now(pytz.utc)


def utcfromtimestamp_with_tz(timestamp: float):
    """
    Return datetime constructed from timestamp, with timezone info.
    :return: datetime
    """
    return datetime.fromtimestamp(timestamp, tz=pytz.utc)


def convert_datestr_to_date(value):
    if isinstance(value, str):
        return date_parser.parse(value)

    return value


class DatabaseSyncToAsync(SyncToAsync):
    """
    SyncToAsync version that cleans up old database connections when it exits.
    """

    def thread_handler(self, loop, *args, **kwargs):
        django.db.close_old_connections()
        try:
            return super().thread_handler(loop, *args, **kwargs)
        finally:
            django.db.close_old_connections()


# The class is TitleCased, but we want to encourage use as a callable/decorator
database_sync_to_async = DatabaseSyncToAsync


# def elk_checker(func):
#     def wrapper(*args, **kwargs):
#         if settings.ENABLE_ELK:
#             return func(*args, **kwargs)
#         return
#
#     return wrapper
#
#
# class UsageAnalytics(threading.Thread):
#     def __init__(self, operation: str, username: str, meta: dict = {}, space_id: Optional[str] = None):
#         self.ok = True
#         self.url = f'{settings.ELASTIC_URL_INT}/na-usage-analytics/_doc'
#         self.data = {
#             'ver': settings.APP_VERSION,
#             'date': utcnow_with_tz().isoformat(),
#             'user': username,
#             'operation': operation,
#             'meta': meta,
#         }
#         if space_id:
#             self.data['space_id'] = space_id
#         if not is_valid_json(self.data, usage_analytics_schema):
#             print("Not valid data!", self.data)
#             self.ok = False
#         threading.Thread.__init__(self)
#
#     @elk_checker
#     def run(self):
#         if self.ok:
#             try:
#                 requests.post(self.url, headers=settings.ELASTIC_HEADERS_INT,
#                               data=json.dumps(self.data), timeout=settings.USAGE_SEND_TIMEOUT)
#             except requests.exceptions.ConnectTimeout:
#                 pass


class AbstractManager(ABC):

    @staticmethod
    @abstractmethod
    def create():
        """
        Create managed object
        """
        pass

    @staticmethod
    @abstractmethod
    def delete():
        """
        Delete managed object
        """
        pass

    @staticmethod
    @abstractmethod
    def get():
        """
        Get managed object
        """
        pass


def validate_image(image: bytes):
    tracer = get_tracer(__name__)
    with tracer.start_as_current_span("validate_image") if tracer else ContextStub():
        with BytesIO(image) as file:
            try:
                img_object = open_image(file)
            except UnidentifiedImageError:
                raise BadInputDataException("0xc69c44d4")

            if img_object.width > settings.MAX_IMAGE_WIDTH:
                raise BadInputDataException("0x006dd808")
            if img_object.height > settings.MAX_IMAGE_HEIGHT:
                raise BadInputDataException("0x006dd809")


def fixed_validate_image(image: bytes):
    with BytesIO(image) as file:
        try:
            img_object = open_image(file)
        except UnidentifiedImageError:
            raise BadInputDataException("0x6fd9bed7")

        if img_object.width > settings.MAX_IMAGE_WIDTH:
            raise BadInputDataException("0x006dd808")
        if img_object.height > settings.MAX_IMAGE_HEIGHT:
            raise BadInputDataException("0x006dd809")


def point_transform(face_info: dict) -> list:
    size = [face_info['bounding_box']['face_rectangle']['width'],
            face_info['bounding_box']['face_rectangle']['height']]
    left_pupil = face_info['bounding_box']['facial_landmarks'][7]
    right_pupil = face_info['bounding_box']['facial_landmarks'][10]
    for k, v in zip(left_pupil.keys(), size):
        left_pupil[k] = left_pupil[k] * v if left_pupil[k] < 1 else left_pupil[k]
    for k, v in zip(right_pupil.keys(), size):
        right_pupil[k] = right_pupil[k] * v if right_pupil[k] < 1 else right_pupil[k]
    return [{'leftPupil': left_pupil, 'rightPupil': right_pupil}]


def face_processing_data_parser_v3(faces: List[dict]) -> dict:
    def parse_face_info(face: dict, idx: int) -> dict:
        templates_to_create = []
        regex = re.compile('^template')
        for key in face.keys():
            if regex.search(key):
                templates_to_create.append(key)

        processing_info = json.loads(face['processingInfo'])

        keypoints = processing_info['keypoints']
        face_meta = processing_info['face_meta']

        emotions = {emotions_map.get(emotion['value'], emotion['value']): emotion['confidence']
                    for emotion in face_meta['emotions'] or []}

        keypoints = {keypoints_map.get(keypoint_key, keypoint_key): {
            'x': keypoint_value['proj'][0],
            'y': keypoint_value['proj'][1]
        } for keypoint_key, keypoint_value in keypoints.items()}

        def convert_templates(face_obj: dict, templates: list):
            try:
                new_templates = {}

                for template_version in templates:
                    version, weight = template_version.replace("template", '').split("v")
                    new_version = f"_face_template_extractor_{weight}_{version}"

                    new_templates[new_version] = face_obj[template_version]
                return new_templates
            except KeyError:
                pass

        return {
            'id': idx,
            'class': 'face',
            'templates': convert_templates(face, templates_to_create),
            'bbox': processing_info['bbox'],
            'keypoints': keypoints,
            'age': face_meta['age']['value'],
            'emotions': emotions,
            'gender': face_meta['gender']['value'],
            'liveness': face_meta['liveness'],
            'angles': processing_info['angles'],
            'mask': face_meta['mask']
        }

    source_image = faces[0]['sourceImage']

    result = {
        '_image': base64.b64encode(source_image).decode(),
        'objects': [
            parse_face_info(face, idx) for idx, face in enumerate(faces, 1)
        ],
    }
    if (errors := json.loads(faces[0]['processingInfo']).get('errors')) is not None:
        result['errors'] = errors

    return result


def load_image(path: str) -> Image:
    img = open_image(path)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    return img


def _bbox_size(bbox):
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    return width, height


def crop_image_bbox(image, bbox) -> Image:
    width, height = image.size
    context_size = 0.2
    bbox = [bbox[0] * width, bbox[1] * height, bbox[2] * width, bbox[3] * height]

    for i in range(2):
        if round(bbox[i]) == round(bbox[i + 2]):
            bbox[i + 2] += 1
        elif bbox[i] > bbox[i + 2]:
            bbox[i], bbox[i + 2] = bbox[i + 2], bbox[i]

    bw, bh = _bbox_size(bbox.copy())

    pad_x = abs(bbox[0] - bbox[2]) * context_size
    pad_y = abs(bbox[1] - bbox[3]) * context_size
    bbox = [bbox[0] - pad_x, bbox[1] - pad_y, bbox[2] + pad_x, bbox[3] + pad_y]

    crop = image.crop(bbox)

    pad_size = 256
    img = ImageOps.pad(crop, (pad_size, pad_size), color='white')  # fix size of crop to avoid bbox blurring

    # scale bbox size due to pad function uses resize of image
    if bh > bw:
        scale = img.height / crop.height
    else:
        scale = img.width / crop.width
    bw *= scale
    bh *= scale

    return img


def delete_none_from_dict(_dict):
    """Delete None values recursively from all of the dictionaries"""
    for key, value in list(_dict.items()):
        if isinstance(value, dict):
            delete_none_from_dict(value)
        elif value is None:
            del _dict[key]
        elif isinstance(value, list):
            for v_i in value:
                if isinstance(v_i, dict):
                    delete_none_from_dict(v_i)
    return _dict


def django_admin_inline_link(app_name: str, model_name: str, action: str, args: tuple, link_text: str):
    return format_html(
        '<a href="{}">{}</a>',
        reverse(f'admin:{app_name}_{model_name}_{action}', args=args),
        escape(link_text)
    )


class ModelMixin:
    class Queryset(models.QuerySet):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

        @atomic
        def delete(self):
            lock = self.select_for_update().all()
            items_count = int(lock.count())

            for item in lock:
                item.delete()

            return items_count, {getattr(self.model, '_meta').label: items_count}

        def with_archived(self):
            return self.filter(is_active__in=[True, False])

    class Manager(models.Manager):

        def all(self, query_filter: Q = Q(is_active=True)):
            if hasattr(self, 'core_filters'):
                query_filter &= Q(**self.core_filters)
            return self.__queryset(query_filter)

        def get_queryset(self):
            return self.__queryset(Q(is_active=True))

        def __queryset(self, query_filter: Q = Q()):
            queryset = ModelMixin.Queryset(model=self.model, using=self._db, hints=self._hints).filter(query_filter)
            return queryset

    @atomic
    def delete(self, using=None, keep_parents=False):
        signals.pre_delete.send(
            sender=type(self), instance=self
        )

        lock = type(self).objects.select_for_update().get(pk=self.pk)
        lock.is_active = False
        lock.save()

        signals.post_delete.send(
            sender=type(self), instance=self
        )


def camel(snake_str: str) -> str:
    """Convert string form camel to snake"""
    first, *others = snake_str.split('_')
    return ''.join([first.lower(), *map(str.title, others)])


def snake(camel_str: str) -> str:
    """Convert string form sale to camel"""
    return re.sub(r'(?<!^)(?=[A-Z])', '_', camel_str).lower()


def convert_old_template_key_to_new(old_template: str) -> str:
    version, weight = old_template.replace("$template", '').split("v")
    return f"_face_template_extractor_{weight}_{version}"


def convert_new_template_key_to_old(new_template: str) -> str:
    rows = new_template.split("_")
    return f"$template{rows[-1]}v{rows[-2]}"


def convert_new_sample_to_old(sample: dict) -> (dict, List[str]):
    def key_error_wrapper(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except KeyError:
                pass

        return wrapper

    @key_error_wrapper
    def upper_liveness_value(sample_obj: dict):
        sample_obj["liveness"]["value"] = sample_obj["liveness"]["value"].upper()

    @key_error_wrapper
    def upper_gender_value(sample_obj: dict):
        sample_obj["gender"] = sample_obj["gender"].upper()

    @key_error_wrapper
    def convert_mask(sample_obj: dict):
        sample_obj["mask"] = sample_obj.pop("has_medical_mask")

    def convert_templates(sample_obj: dict):
        try:
            templates = sample_obj.pop("template")
        except KeyError:
            return

        new_templates = {}

        for key, value in templates.items():
            new_templates[convert_new_template_key_to_old(key)] = value.get('blob') or value

        sample_obj["templates"] = new_templates

    def convert_emotions(sample_obj: dict):
        try:
            emotions = sample_obj["emotions"]
        except KeyError:
            return

        new_emotions = {}

        for emotion_object in emotions:
            new_emotions[emotion_object['emotion']] = emotion_object['confidence']

        sample_obj["emotions"] = new_emotions

    @key_error_wrapper
    def convert_angles(sample_obj: dict):
        sample_obj["angles"] = sample_obj.pop("pose")

    def convert_keypoints(sample_obj: dict):
        try:
            keypoints = sample_obj["keypoints"]
        except KeyError:
            return

        type = keypoints.pop('fitter_type')
        for keypoint in keypoints.values():
            proj = keypoint.pop('proj')
            x = proj[0]
            y = proj[1]
            keypoint.update({"x": x, "y": y})
        keypoints['left_pupil'] = keypoints.pop('left_eye')
        keypoints['right_pupil'] = keypoints.pop('right_eye')
        keypoints['fitter_type'] = type

    def convert_quality(sample_obj: dict):
        pass

    new_sample = copy.deepcopy(sample)
    new_image = new_sample.pop("_image")

    new_sample["$image"] = new_image.get('blob') or new_image

    new_sample[f"objects@{SampleObjectsName.CAPTURER}"] = new_sample.pop("objects")

    for sample_object in new_sample[f"objects@{SampleObjectsName.CAPTURER}"]:
        # cropImage field is dropped after moving from fms processing to image-api
        upper_liveness_value(sample_object)
        upper_gender_value(sample_object)
        convert_mask(sample_object)
        convert_templates(sample_object)
        convert_emotions(sample_object)
        convert_angles(sample_object)
        convert_keypoints(sample_object)
        convert_quality(sample_object)

    return new_sample


def convert_old_sample_to_new(sample: dict) -> (dict, List[str]):
    def convert_quality(sample_obj: dict):
        # TODO made quality convertion
        pass

    def remove_crop(sample_obj: dict):
        try:
            sample_obj.pop("$cropImage")
        except KeyError:
            pass

    def lower_liveness_value(sample_obj: dict):
        try:
            sample_obj["liveness"]["value"] = sample_obj["liveness"]["value"].lower()
        except KeyError:
            pass

    def lower_gender_value(sample_obj: dict):
        try:
            sample_obj["gender"] = sample_obj["gender"].lower()
        except KeyError:
            pass

    def convert_mask(sample_obj: dict):
        try:
            sample_obj["has_medical_mask"] = sample_obj.pop("mask")
        except KeyError:
            pass

    def convert_templates(sample_obj: dict):
        try:
            templates = sample_obj.pop("templates")
            new_templates = {}

            for key, value in templates.items():
                new_templates[convert_old_template_key_to_new(key)] = value

            sample_obj["template"] = new_templates
        except KeyError:
            pass

    def convert_emotions(sample_obj: dict):
        try:
            emotions = sample_obj["emotions"]
            new_emotions = []

            for key, value in emotions.items():
                new_emotions.append({"emotion": key, "confidence": value})

            new_emotions = list(sorted(new_emotions, key=lambda x: x["confidence"], reverse=True))

            sample_obj["emotions"] = new_emotions
        except KeyError:
            pass

    def convert_angles(sample_obj: dict):
        try:
            sample_obj["pose"] = sample_obj.pop("angles")
        except KeyError:
            pass

    def convert_keypoints(sample_obj: dict):
        try:
            keypoints = sample_obj["keypoints"]
            type = keypoints.pop('fitter_type')

            for keypoint in keypoints.values():
                x = keypoint.pop('x')
                y = keypoint.pop('y')
                keypoint["proj"] = [x, y]

            keypoints['fitter_type'] = type
            keypoints['left_eye'] = keypoints.pop('left_pupil')
            keypoints['right_eye'] = keypoints.pop('right_pupil')
        except KeyError:
            pass

    new_sample = copy.deepcopy(sample)
    new_sample["_image"] = new_sample.pop("$image")

    for key, value in sample.items():
        if key.startswith("objects"):
            new_sample.pop(key)
            objects = new_sample["objects"] = value

            for sample_object in objects:
                remove_crop(sample_object)
                lower_liveness_value(sample_object)
                lower_gender_value(sample_object)
                convert_mask(sample_object)
                convert_templates(sample_object)
                convert_emotions(sample_object)
                convert_angles(sample_object)
                convert_keypoints(sample_object)
                convert_quality(sample_object)

    return new_sample


def split_list(input_list, max_size):
    return [input_list[i:i + max_size] for i in range(0, len(input_list), max_size)]


@contextmanager
def mute_signal(signal, receiver, sender=None):
    signal.disconnect(receiver, sender=sender)
    try:
        yield
    finally:
        signal.connect(receiver, sender=sender)
