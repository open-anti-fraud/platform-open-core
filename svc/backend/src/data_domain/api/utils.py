import base64
import asyncio
from typing import Optional, List, Dict

from aiohttp import ClientSession
from strawberry.types import Info

from data_domain.managers import SampleManager, SampleEnricher
from data_domain.models import Sample, BlobMeta
from platform_lib.exceptions import BadInputDataException
from platform_lib.utils import SampleObjectsName, validate_image, fixed_validate_image


def check_search_input_data_v3(**kwargs) -> bool:
    score_threshold = kwargs.get("score_threshold")
    max_num_of_candidates_returned = kwargs.get("max_num_of_candidates_returned")

    if score_threshold < 0 or score_threshold > 1:
        raise BadInputDataException("0xf47f116a")

    if max_num_of_candidates_returned < 1 or max_num_of_candidates_returned > 100:
        raise BadInputDataException("0xf8be6762")

    return True


def check_search_input_data(**kwargs) -> bool:
    source_sample_ids = kwargs.get("source_sample_ids")
    source_sample_data = kwargs.get("source_sample_data")
    source_image_upload = kwargs.get("source_image_upload")
    source_image_base64 = kwargs.get("source_image_base64") or kwargs.get("source_image")
    score_threshold = kwargs.get("score_threshold")
    if score_threshold is None:
        score_threshold = kwargs.get("confidence_threshold")
    max_num_of_candidates_returned = kwargs.get("max_num_of_candidates_returned")

    if sum(map(bool, [source_sample_ids, source_sample_data, source_image_upload, source_image_base64])) != 1:
        raise BadInputDataException("0x963fb254")

    if score_threshold < 0 or score_threshold > 1:
        raise BadInputDataException("0xf47f116a")

    if max_num_of_candidates_returned < 1 or max_num_of_candidates_returned > 100:
        raise BadInputDataException("0xf8be6762")

    return True


async def check_faces_count(image: bytes, session: Optional[ClientSession] = None):
    enricher = SampleEnricher(image=image)

    if session is None:
        async with ClientSession() as session:
            await enricher.face_detector_face_fitter(session)
    else:
        await enricher.face_detector_face_fitter(session)

    objects = enricher.get_result().get('objects') or []

    if len(objects) == 0:
        raise BadInputDataException("0x95bg42fd")
    if len(objects) > 1:
        raise BadInputDataException("0x35vd45ms")


def get_templates_from_image(image: bytes, template_version, objects_key) -> list:
    validate_image(image)
    processing_result = SampleManager.process_image(image=image, template_version=template_version)
    return [face['templates'][f"${template_version}"] for face in processing_result[objects_key]]


async def get_templates_from_image_v3(image: bytes, template_version, session: Optional[ClientSession] = None) -> list:
    fixed_validate_image(image)
    enricher = SampleEnricher(image=image)

    if session is None:
        async with ClientSession() as session:
            await enricher.face_detector_template_extractor(session, template_version)
    else:
        await enricher.face_detector_template_extractor(session, template_version)

    version, weight = template_version.replace("template", '').split("v")
    return [face['template'][f"_face_template_extractor_{weight}_{version}"] for face in
            enricher.get_result()['objects']]


def check_fields_for_create_sample(info: Info):
    selected_fields = info.selected_fields[0].selections
    try:
        next(filter(lambda x: x.name == '_image', selected_fields))
        next(filter(lambda x: x.name == 'id', selected_fields))
    except StopIteration:
        raise BadInputDataException("0x42a331a0")


def get_templates_from_database(template_ids) -> list:
    templates_meta = sorted(BlobMeta.objects.select_related('blob').filter(id__in=template_ids),
                            key=lambda x: template_ids.index(str(x.id)))
    return [base64.standard_b64encode(template_meta.blob.data.tobytes()).decode() for
            template_meta in templates_meta]


def get_templates_from_samples_id(source_sample_ids, template_version) -> list:
    samples = sorted(Sample.objects.filter(id__in=source_sample_ids),
                     key=lambda x: source_sample_ids.index(str(x.id)))
    template_ids = [SampleManager.get_template_id(sample.meta, template_version) for sample in samples]
    return get_templates_from_database(template_ids)


def get_templates_with_meta_from_raw_sample_v3(source_sample_data, template_version) -> list:
    source_templates = [face.get('template', {}).get(template_version) for
                        face in source_sample_data["objects"]]
    return [template for template in source_templates if template is not None]


def get_templates_from_sample_data(source_sample_data, template_version, objects_key) -> list:
    source_sample_data = source_sample_data.get('data') or source_sample_data  # if input with data or not
    source_templates = [face['templates'][f"${template_version}"] for
                        face in source_sample_data[objects_key]]
    try:
        template_ids = [template.get('id') for template in source_templates]
        return get_templates_from_database(template_ids)
    except AttributeError:
        return source_templates


def get_templates(template_version, source_sample_ids, source_sample_data, source_image) -> Dict[str, list]:
    objects_key = f'objects@{SampleObjectsName.CAPTURER}'
    templates_map = {
        "source_sample_ids": [],
        "source_sample_data": [],
        "source_image": []
    }

    # TODO WTF!? Why do we select only source_sample_ids if we have multiple parameters passed?
    # TODO It is necessary to separate the search into separate cases
    if source_sample_ids:
        templates_map["source_sample_ids"].extend(
            get_templates_from_samples_id(source_sample_ids, template_version)
        )
    elif source_sample_data:
        templates_map["source_sample_data"].extend(
            get_templates_from_sample_data(source_sample_data, template_version, objects_key)
        )
    elif source_image:
        try:
            loop = asyncio.get_event_loop()
        except Exception:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        templates_map["source_image"].extend([
            elem['blob'] for elem in loop.run_until_complete(get_templates_from_image_v3(
                source_image, template_version)
            )
        ])
        if (not templates_map["source_sample_ids"] and not templates_map["source_sample_data"]
                and not templates_map["source_image"]):
            raise BadInputDataException('0x95bg42fd')

    return templates_map


def search_result_to_response_v3(search_result: List, templates: List[str], score_threshold: float) -> List:
    response = []
    for i in range(len(search_result['matches'])):
        response.append({
            "template": templates[i],
            "search_result": [mtch for mtch in search_result['matches'][i] if mtch['score'] > score_threshold]
        })

    return response
