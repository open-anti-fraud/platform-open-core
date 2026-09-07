import base64
from typing import Tuple, Optional

from django.db import transaction
from strawberry import ID

from main.settings import CREATE_PROFILE_QUALITY_THRESHOLD, QUERY_LIMIT, CALCULATED_ATTRIBUTES
from platform_lib.exceptions import BadInputDataException
from platform_lib.types import CustomBinaryType
from platform_lib.utils import SampleObjectsName, fixed_validate_image, convert_new_sample_to_old, \
    convert_old_sample_to_new
from plib.tracing.utils import get_tracer, ContextStub

from django.db.transaction import atomic
from django.db.models import QuerySet, Prefetch
from django.apps import apps

from person_domain.models import Person, Profile
from person_domain.managers import ProfileManager
from person_domain.api.v2.types import ProfileInput

from data_domain.models import Sample, BlobMeta
from data_domain.managers import SampleManager, SampleManagerV3
from user_domain.models import Workspace


def check_face_quality(quality: float):
    if quality < CREATE_PROFILE_QUALITY_THRESHOLD:
        raise BadInputDataException('0x86bd49dh')


def check_profile_query(query: list):
    if len(query) > QUERY_LIMIT:
        raise BadInputDataException("0x28c06517")


def check_fields_for_create_profile(sample: Sample):
    if not any(key.endswith('image') for key in sample.meta.keys()):
        raise BadInputDataException("0x819f9416")
    for key, sample_field in sample.meta.items():
        if key.startswith('objects'):
            print('check_fields_for_create_profile', len(sample_field), sample_field)
            if len(sample_field) < 1:
                raise BadInputDataException("0x95bg42fd")
            if len(sample_field) > 1:
                raise BadInputDataException("0x35vd45ms")
            if 'template' not in sample_field[0] or 'age' not in sample_field[0] or 'gender' not in sample_field[0]:
                raise BadInputDataException("0x819f9416")


def save_sample_data(sample_data: dict, workspace_id: str, template_version: str,
                     anonymous_mode: Optional[bool] = False):
    tracer = get_tracer(__name__)
    with tracer.start_as_current_span("save_sample_data") if tracer else ContextStub() as span:
        objects_key = f'objects'

        if (errors := sample_data.get('errors')) is not None:
            raise Exception(errors[0])

        if anonymous_mode:
            sample_data.update({'_image': None})
        else:
            fixed_validate_image(base64.standard_b64decode(sample_data.get('_image').get('blob')))

        with transaction.atomic():
            with tracer.start_as_current_span("create_blobs") if tracer else ContextStub() as span:
                face_meta_with_ids = SampleManagerV3.create_blobs(workspace_id, sample_data)

            with tracer.start_as_current_span("save_sample_in_db") if tracer else ContextStub() as span:
                objects = face_meta_with_ids.get(objects_key) or []
                sample = SampleManagerV3.create_sample(workspace_id=workspace_id,
                                                       sample_meta=convert_new_sample_to_old(
                                                           {'_image': face_meta_with_ids['_image'],
                                                            objects_key: objects}))
                sample.meta = convert_old_sample_to_new(sample.meta)
                for obj in objects:
                    if obj.get('template'):
                        version, weight = template_version.replace("template", '').split("v")
                        template_blob_meta_id = obj['template'][f"_face_template_extractor_{weight}_{version}"]['id']
                        template_blob_meta = BlobMeta.objects.select_for_update().get(id=template_blob_meta_id)
                        template_blob_meta.meta.update({'sample_id': str(sample.id)})
                        template_blob_meta.save()
    return sample


def create_profile_v3(profile_info: dict,
                      workspace: Workspace,
                      p_sample: Optional[Sample] = None,
                      p_groups: Optional[ID] = None) -> Tuple[Profile, Person]:
    sample_ids = None
    if p_sample is not None:
        profile_info = {
            'age': SampleManagerV3.get_age(p_sample.meta),
            'gender': SampleManagerV3.get_gender(p_sample.meta).upper(),
            'main_sample_id': str(p_sample.id),
            'avatar_id': str(p_sample.id),
            **profile_info
        }

        sample_ids = [str(p_sample.id)]

    created_profile, created_person = ProfileManager.create_with_person(workspace=workspace,
                                                                        info=profile_info,
                                                                        label_ids=p_groups,
                                                                        sample_ids=sample_ids)

    return created_profile, created_person


def create_sample_by_image(img: CustomBinaryType, template_version: str, workspace_id: str) -> Tuple[Sample, float]:
    with atomic():
        objects_key = f'objects@{SampleObjectsName.CAPTURER}'
        processing_result = SampleManager.process_image(
            image=img,
            template_version=template_version,
            attributes=CALCULATED_ATTRIBUTES
        )
        if len(processing_result[objects_key]) > 1:
            raise BadInputDataException('0x35vd45ms')
        if (errors := processing_result.get('errors')) is not None:
            raise Exception(errors[0])
        # raw_template = processing_result[objects_key][0]['templates'][f"${template_version}"]

        processing_result_with_ids = SampleManager.create_blobs(workspace_id, meta=processing_result)
        face_meta_with_ids = processing_result_with_ids[objects_key][0]

        if 'quality' not in CALCULATED_ATTRIBUTES:
            raise RuntimeError(
                "Can't create profile without quality estimator. "
                f"Enabled attributes {CALCULATED_ATTRIBUTES}"
            )
        quality = processing_result[objects_key][0]['quality']['total_score']
        check_face_quality(quality)
        created_sample = SampleManager.create_sample(
            workspace_id=workspace_id,
            sample_meta={
                '$image': processing_result_with_ids['$image'],
                objects_key: [face_meta_with_ids]
            })

    return created_sample, quality


def optimizer_profile_queryset(queryset: QuerySet):
    activities = apps.get_model('data_domain', 'Activity')
    return queryset.prefetch_related(
        'profile_groups',
        'samples',
        Prefetch('person__activities', queryset=activities.objects.order_by('creation_date'))
    ).distinct()


def optimizer_profile_queryset_v3(queryset: QuerySet):
    return queryset.prefetch_related(
        'profile_groups',
        'samples'
    ).distinct()
