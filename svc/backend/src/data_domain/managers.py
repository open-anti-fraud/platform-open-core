import asyncio
import base64
import copy
import datetime
import json
import uuid
from asyncio import Event
from collections import namedtuple
from dataclasses import dataclass, field
from functools import wraps
from itertools import chain
from typing import List, Tuple, Union, Optional, Set

import bson
import jsonschema
from aiohttp import FormData, ClientSession
from django.core.cache import cache
from django.db import transaction
from django.db.models import QuerySet
from plib.tracing.utils import get_tracer, ContextStub, get_current_tracing_context

from data_domain.models import Sample, Activity, BlobMeta, Blob, bsm_indicator
from main import settings
from platform_lib.exceptions import BadInputDataException
from platform_lib.managers import BaseProcessManager, CacheManager
from platform_lib.utils import (
    convert_new_sample_to_old,
    utcnow_with_tz,
    SampleObjectsName,
    camel,
    snake,
    fixed_validate_image,
)
from platform_lib.validation.schemes import (
    activity_meta_scheme,
    sample_meta_scheme,
    sample_meta_scheme_v3,
)
from user_domain.models import Workspace


class AgentDataManager:
    bsm_indicator = "$"

    @classmethod
    def decode(cls, data: bytes) -> Tuple[dict, bool]:
        """Decodes abstract sample got over network
        :param data: "network sample" or raw_image
        :return: decoded sample and flag that specifies is input was a raw image or "network sample"
        """
        version_size = 1
        version = int.from_bytes(data[:version_size], byteorder="big")
        if version == 1:
            return bson.loads(data[version_size:]), False

        # assume that it is a raw image and pack it to bsm format
        return cls.__bsm("image", data), True

    @classmethod
    def __bsm(cls, key: str, blob: bytes):
        return {f"{cls.bsm_indicator}{key}": {"format": "IMAGE", "blob": blob}}

    @classmethod
    def parse_extra(cls, data: dict):
        result = {k: v for k, v in data.items() if k not in ["prediction"]}
        prediction = json.loads(data.get("prediction", "{}"))
        if "objects" in prediction:
            result["objects"] = prediction["objects"]
        return result

    @classmethod
    def validate_sample_meta(cls, meta: dict):
        return True
        # try:
        #      jsonschema.validate(meta, activity_meta_scheme)
        # except jsonschema.ValidationError:
        #     try:
        #         # bson doesn't decode numbers therefore try to decode meta data
        #         s = {k: cls.__json_decoder(v) for k, v in meta.items()}
        #         jsonschema.validate(s, activity_meta_scheme)
        #     except Exception:
        #         return False
        # return True

    @classmethod
    def __json_decoder(cls, o: Union[list, tuple, str]):
        if isinstance(o, str):
            try:
                return int(o)
            except ValueError:
                try:
                    return float(o)
                except ValueError:
                    return o
        elif isinstance(o, dict):
            return {k: cls.__json_decoder(v) for k, v in o.items()}
        elif isinstance(o, (list, tuple)):
            return type(o)([cls.__json_decoder(v) for v in o])
        else:
            return o

    @classmethod
    def extract_bsms(cls, meta: dict, bsms: Optional[list] = None):
        """Extracts bsms values from meta and replaces it to id"""
        if bsms is None:
            bsms = []
        if isinstance(meta, dict):
            for k, v in meta.items():
                if k.startswith(cls.bsm_indicator):
                    unique_hash = str(uuid.uuid4())
                    bsms.append((k, v, unique_hash))
                    meta[k] = bsms.index((k, v, unique_hash))
                else:
                    meta[k], bsms = cls.extract_bsms(v, bsms)
        elif isinstance(meta, list):
            tmp_data = []
            for d in meta:
                m, bsms = cls.extract_bsms(d, bsms)
                tmp_data.append(m)
            return tmp_data, bsms

        return meta, bsms

    @classmethod
    def substitute_bsms(
        cls, meta: Union[list, dict, int, str], bsms: list, is_bsm: bool = False
    ):
        """Substitute bsm info into meta. This method applies after extract method"""
        if isinstance(meta, dict):
            return {
                k: cls.substitute_bsms(v, bsms, k.startswith(cls.bsm_indicator))
                for k, v in meta.items()
            }
        elif isinstance(meta, list):
            return [cls.substitute_bsms(m, bsms) for m in meta]
        return bsms[meta] if is_bsm else meta


class AgentDataManagerV3(AgentDataManager):
    bsm_indicator = "_"


class OngoingManager(CacheManager):
    @classmethod
    def set_ongoings(
        cls, ongoings: List[dict], workspace_id: str, camera_id: str
    ) -> None:
        cls.cache_set(cls._build_cache_key(workspace_id, camera_id), ongoings)

    @classmethod
    def get_ongoings(cls, workspace_id: str, location_id: str = "") -> List[dict]:
        cameras = Workspace.objects.get(id=workspace_id).cameras.all()
        if location_id:
            cameras = cameras.filter(camera_location__label_id=location_id)

        ongoings = [
            cache.get(cls._build_cache_key(workspace_id, camera.id), [])
            for camera in cameras
        ]
        return list(chain(*ongoings))

    @staticmethod
    def get_parent_process(ongoing: dict) -> dict:
        return next(
            filter(
                lambda proc: proc.get("object", {}).get("class", "") == "human",
                ongoing["processes"],
            ),
            {},
        )

    @staticmethod
    def _build_cache_key(workspace_id: str, camera_id: str) -> str:
        return f"ongoings:workspace:{workspace_id}:camera:{camera_id}"


class SampleManager:

    @staticmethod
    def base_attributes_list() -> List[str]:
        return [
            "id",
            "confidence",
            "bbox",
            "keypoints",
            "pose",
            "class",
            "template",
        ]

    @staticmethod
    def default_attributes_list() -> List[str]:
        return [
            "gender",
            "age",
            "has_medical_mask",
            "quality",
            "emotions",
            "liveness",
        ]

    @staticmethod
    def __platform_object_key() -> str:
        return f"objects@{SampleObjectsName.CAPTURER}"

    @classmethod
    def create_sample(cls, workspace_id: str, sample_meta: dict) -> Sample:
        with transaction.atomic():
            sample = Sample.objects.create(workspace_id=workspace_id, meta=sample_meta)
        return sample

    @staticmethod
    def get_sample(workspace_id: str, sample_id: str) -> Sample:
        return Sample.objects.get(id=sample_id, workspace_id=workspace_id)

    @staticmethod
    def get_samples(workspace_id: str, sample_ids: List[str]) -> QuerySet:
        return Sample.objects.filter(id__in=sample_ids, workspace_id=workspace_id)

    @staticmethod
    def get_sample_ids(workspace_id: str, sample_ids: list) -> QuerySet:
        samples = Sample.objects.filter(
            workspace_id=workspace_id, id__in=sample_ids
        ).values_list("id", flat=True)
        if samples.count() != len(set(sample_ids)):
            raise BadInputDataException("0x943b3c24")
        return samples

    @classmethod
    def update_sample_meta(cls, sample_id: Union[str, uuid.UUID], meta: dict) -> Sample:
        with transaction.atomic():
            locked_sample = Sample.objects.select_for_update().get(id=sample_id)
            locked_sample.meta.update(meta)
            locked_sample.save()
        return locked_sample

    @classmethod
    def update_face_object(cls, sample_meta: dict, new_info: dict):
        sample_meta.get(cls.__platform_object_key(), [{}])[0].update(new_info)

    @classmethod
    def get_template_id(cls, sample_meta: dict, template_version: str) -> Optional[str]:
        old_template = sample_meta.get(f"${template_version}", {}).get("id")
        new_template = (
            sample_meta.get(cls.__platform_object_key(), [{}])[0]
            .get("templates", {})
            .get(f"${template_version}", {})
            .get("id")
        )

        return old_template or new_template

    @classmethod
    def get_objects(cls, sample_meta: dict) -> Optional[dict]:
        return sample_meta.get(cls.__platform_object_key())

    @classmethod
    def get_raw_template(cls, sample_meta: dict, template_version: str) -> str:
        blob = cls.get_template_bytes(sample_meta, template_version)
        return base64.standard_b64encode(blob).decode()

    @classmethod
    def get_template_bytes(cls, sample_meta: dict, template_version: str) -> str:
        template_id = cls.get_template_id(sample_meta, template_version)

        blob = (
            BlobMeta.objects.select_related("blob")
            .get(id=template_id)
            .blob.data.tobytes()
        )
        return blob

    @classmethod
    def get_template_with_meta(cls, sample_meta: dict, template_version: str) -> Optional[dict]:
        template_id = cls.get_template_id(sample_meta, template_version)

        if template_id is None:
            return None

        blob_meta = BlobMeta.objects.select_related("blob").get(id=template_id)
        blob_data_str = base64.standard_b64encode(blob_meta.blob.data.tobytes()).decode()
        blob_meta_dict = blob_meta.meta

        try:
            del blob_meta_dict["sample_id"]
        except KeyError:
            pass

        blob_meta_dict["blob"] = blob_data_str

        return blob_meta_dict

    @classmethod
    def get_image(cls, sample_meta: dict) -> Optional[bytes]:
        blob_meta = (
            BlobMeta.objects.filter(id=sample_meta.get("$image", {}).get("id"))
            .select_related("blob")
            .first()
        )
        if not blob_meta:
            return None
        return blob_meta.blob.data

    @classmethod
    def get_age(cls, sample_meta: dict) -> int:
        return sample_meta.get(cls.__platform_object_key(), [{}])[0].get("age", "25")

    @classmethod
    def get_gender(cls, sample_meta: dict) -> str:
        return sample_meta.get(cls.__platform_object_key(), [{}])[0].get("gender", "MALE")

    @classmethod
    def get_face_crop_id(cls, sample_meta: dict) -> str:
        best_shot = sample_meta.get("$best_shot", {}).get("id")
        if len(objects := sample_meta.get(cls.__platform_object_key(), [{}])):
            face_crop = objects[0].get("$cropImage", {}).get("id")
        else:
            face_crop = None
        image = sample_meta.get("$image", {}).get("id")

        return best_shot or face_crop or image

    @classmethod
    def get_face_quality(cls, sample_meta: dict) -> float:
        new_quality = sample_meta.get(cls.__platform_object_key(), [{}])[0].get(
            "quality"
        )
        old_quality = sample_meta.get("quality")

        return old_quality or new_quality

    @staticmethod
    def __create_bsms(bsms: list, workspace_id: Union[str, uuid.UUID]) -> list:
        written_bsms = []
        for key, bsm, _ in bsms:
            try:
                blob = base64.b64decode(bsm)
            except TypeError:
                written_bsms.append(None)
                continue
            blob_type = None

            # TODO remove hardcoded bsm types
            binary_format = "NDARRAY"

            if key == "$cropImage" or key == "image":
                blob_type = "image"
                binary_format = "IMAGE"
            elif key.startswith(AgentDataManager.bsm_indicator):
                blob_type = key.replace(AgentDataManager.bsm_indicator, "")

            blob_obj = Blob.objects.create(data=blob)
            blob_meta = BlobMeta.objects.create(
                workspace_id=workspace_id,
                blob=blob_obj,
                meta={"type": blob_type, "format": binary_format},
            )

            written_bsms.append({"id": str(blob_meta.id)})

        return written_bsms

    @classmethod
    def create_blobs(cls, workspace_id: str, meta: dict) -> dict:
        with transaction.atomic():
            meta, bsms = AgentDataManager.extract_bsms(meta)
            created_bsms = cls.__create_bsms(bsms, workspace_id)
            meta = AgentDataManager.substitute_bsms(meta, created_bsms)
        return meta

    @staticmethod
    def process_image(
        image, template_version, request_id=None,
            is_anonymous: Optional[bool] = False,
            attributes: Optional[List[str]] = None
    ) -> dict:
        if attributes is None:
            fields = SampleManager.base_attributes_list() + SampleManager.default_attributes_list()
        else:
            fields = attributes + SampleManager.base_attributes_list()
        tracing_context = get_current_tracing_context()
        headers = {'X_REQUEST_ID': request_id}
        headers.update(tracing_context)
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("process_image") if tracer else ContextStub():
            try:
                loop = asyncio.get_event_loop()
            except Exception:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            enricher = SampleEnricher(image, request_id=request_id)

            # TODO Make it compatible with the main version
            sample_v3 = loop.run_until_complete(
                enricher.async_execute_functions_by_fields(
                    fields
                )
            )

        if len(sample_v3['objects']) == 0:
            raise BadInputDataException("0x95bg42fd")

        return convert_new_sample_to_old(sample_v3)

    @classmethod
    def delete(cls, workspace_id: str, sample_ids: list):
        with transaction.atomic():
            Sample.objects.select_for_update().filter(
                workspace_id=workspace_id, id__in=sample_ids
            ).delete()

    @staticmethod
    def delete_samples(samples: QuerySet[Sample]) -> int:
        return samples.select_for_update().delete()[0]

    @staticmethod
    def change_meta_to_new(
        workspace_id: str, destination_sample_id: str, origin_sample_id: str
    ) -> Sample:
        new_sample = Sample.objects.get(
            workspace_id=workspace_id, id=origin_sample_id
        )
        with transaction.atomic():
            sample = Sample.objects.select_for_update().get(
                workspace_id=workspace_id, id=destination_sample_id
            )

            sample.meta, new_sample.meta = new_sample.meta, sample.meta
            sample.save()
            new_sample.save()

        return sample

    @staticmethod
    def get_blobmeta_ids(meta: Union[dict, list], exclude_ids: Optional[List[str]] = None) -> List[str]:
        bm_ids = list()
        if isinstance(meta, dict):
            for key, value in meta.items():
                if value is None:
                    continue
                if key.startswith(bsm_indicator) and value.get("id") not in (exclude_ids or []):
                    bm_ids.append(value["id"])
                else:
                    bm_ids.extend(SampleManager.get_blobmeta_ids(value, exclude_ids))
        elif isinstance(meta, list):
            for item in meta:
                bm_ids.extend(SampleManager.get_blobmeta_ids(item, exclude_ids))
        return bm_ids


class SampleManagerV3(SampleManager):
    @staticmethod
    def __platform_object_key() -> str:
        return f"objects"

    @classmethod
    def create_blobs(cls, workspace_id: str, meta: dict) -> dict:
        with transaction.atomic():
            meta, bsms = AgentDataManagerV3.extract_bsms(meta)
            created_bsms = cls.__create_bsms(bsms, workspace_id)
            meta = AgentDataManagerV3.substitute_bsms(meta, created_bsms)
        return meta

    @staticmethod
    def __create_bsms(bsms: list, workspace_id: Union[str, uuid.UUID]) -> list:
        written_bsms = []
        for key, bsm, _ in bsms:
            try:
                blob = base64.b64decode(bsm.pop("blob"))
            except (TypeError, AttributeError):
                written_bsms.append(None)
                continue

            blob_obj = Blob.objects.create(data=blob)
            blob_meta = BlobMeta.objects.create(
                workspace_id=workspace_id, blob=blob_obj, meta=bsm
            )

            written_bsms.append({"id": str(blob_meta.id), **bsm})

        return written_bsms

    @classmethod
    def get_template_id(cls, sample_meta: dict, template_version: str) -> Optional[str]:
        version, weight = template_version.replace("template", "").split("v")
        old_template = sample_meta.get(f"${template_version}", {}).get("id")
        new_template = (
            sample_meta.get(cls.__platform_object_key(), [{}])[0]
            .get("template", {})
            .get(f"_face_template_extractor_{weight}_{version}", {})
            .get("id")
        )

        return old_template or new_template

    @classmethod
    def get_raw_template(cls, sample_meta: dict, template_version: str) -> str:
        template_id = cls.get_template_id(sample_meta, template_version)

        blob = (
            BlobMeta.objects.select_related("blob")
            .get(id=template_id)
            .blob.data.tobytes()
        )
        return base64.standard_b64encode(blob).decode()

    @classmethod
    def get_age(cls, sample_meta: dict) -> int:
        return sample_meta.get(cls.__platform_object_key(), [{}])[0].get("age")

    @classmethod
    def get_gender(cls, sample_meta: dict) -> str:
        return sample_meta.get(cls.__platform_object_key(), [{}])[0].get("gender")


class BlobMetaManager:
    def __init__(self, blobmeta_id: str):
        self.id = blobmeta_id

    @property
    def blob(self):
        return BlobMeta.objects.get(id=self.id).blob


class ActivityManager:
    @staticmethod
    def get_activities(workspace_id: str, activities_ids: list):
        activities = Activity.objects.select_for_update().filter(
            workspace_id=workspace_id, id__in=activities_ids
        )
        if activities.count() != len(set(activities_ids)):
            raise BadInputDataException("0x86bjl434")
        return activities

    @staticmethod
    def get_activity(workspace_id: str, activity_id: str) -> Activity:
        return Activity.objects.get(id=activity_id, workspace_id=workspace_id)

    @staticmethod
    def lock_activity(activity: Activity) -> Activity:
        return Activity.objects.select_for_update().get(id=activity.id)

    # TODO: Wrap getting different processes into one method
    # TODO: Maybe create ProcessManager in platform_lib
    @staticmethod
    def get_face_processes(activity: Activity) -> list:
        return list(
            filter(
                lambda track: track.get("object", {}).get("class", {}) == "face",
                activity.data["processes"],
            )
        )

    @staticmethod
    def get_first_face_process(activity: Activity) -> Optional[dict]:
        processes = ActivityManager.get_face_processes(activity)
        if processes:
            return processes[0]

    @staticmethod
    def get_body_processes(activity: Activity) -> list:
        return list(
            filter(
                lambda track: track.get("object", {}).get("class", {}) == "body",
                activity.data["processes"],
            )
        )

    @classmethod
    def get_best_shot_ids(cls, activity: Activity) -> list:
        if activity:
            face_processes = cls.get_face_processes(activity)
            return [
                process.get("$best_shot", {}).get("id") for process in face_processes
            ]
        else:
            return [None]

    @classmethod
    def get_template_ids(cls, activity: Activity, template_version: str) -> List[str]:
        if activity:
            processes = cls.get_face_processes(activity)
            return [
                p.get("object", {})
                .get("embeddings", {})
                .get(f"${template_version}")
                .get("id")
                for p in processes
            ]
        return []

    @staticmethod
    def get_parent_process(activity: Activity) -> dict:
        return next(
            filter(
                lambda proc: proc.get("object", {}).get("class", "") == "human",
                activity.data["processes"],
            ),
            {},
        )

    @classmethod
    def get_parent_time_interval(cls, activity: Activity) -> list:
        parent = cls.get_parent_process(activity)
        time_interval = parent.get("time_interval")
        return time_interval

    @staticmethod
    def isoformat_date(time):
        try:
            return datetime.datetime.fromisoformat(time).isoformat()
        except (TypeError, ValueError):
            return datetime.datetime.fromtimestamp(
                time / 1000.0, tz=datetime.timezone.utc
            ).isoformat()

    @staticmethod
    def create_manual_activity(
        workspace_id: str,
        age: int,
        gender: str,
        template_blob_meta_id: str,
        best_shot_blob_meta_id: str,
        template_version: str,
    ) -> Activity:
        human_track_id = str(uuid.uuid4())
        person_id = str(uuid.uuid4())
        time_interval = [
            utcnow_with_tz().isoformat(),
            utcnow_with_tz().isoformat(),
        ]
        data = {
            "manual": True,
            "processes": [
                {
                    "id": human_track_id,
                    "type": "track",
                    "object": {"id": person_id, "class": "human"},
                    "time_interval": time_interval,
                },
                {
                    "id": str(uuid.uuid4()),
                    "type": "track",
                    "object": {
                        "id": person_id,
                        "age": age,
                        "class": "face",
                        "gender": gender,
                        "embeddings": {
                            f"${template_version}": {"id": template_blob_meta_id}
                        },
                    },
                    "parent": human_track_id,
                    "$best_shot": {"id": best_shot_blob_meta_id},
                    "time_interval": time_interval,
                },
            ],
        }
        with transaction.atomic():
            activity = Activity.objects.create(
                data=data, creation_date=utcnow_with_tz(), workspace_id=workspace_id
            )

        return activity

    @classmethod
    def delete(cls, workspace_id: str, activities_ids: List[str]):
        with transaction.atomic():
            cls.get_activities(workspace_id, activities_ids).delete()

    @classmethod
    def get_last_face_process(cls, activity: Activity) -> dict:
        face_processes = list(
            filter(
                lambda process: bool(process.get("$best_shot")),
                cls.get_face_processes(activity),
            )
        )

        return (
            sorted(
                face_processes,
                key=lambda process: datetime.datetime.fromisoformat(
                    process["time_interval"][0]
                ),
                reverse=True,
            )[0]
            if face_processes
            else None
        )

    @classmethod
    def get_last_body_process(cls, activity: Activity) -> dict:
        body_processes = list(
            filter(
                lambda process: bool(process.get("$best_shot")),
                cls.get_body_processes(activity),
            )
        )

        return (
            sorted(
                body_processes,
                key=lambda process: datetime.datetime.fromisoformat(
                    process["time_interval"][0]
                ),
                reverse=True,
            )[0]
            if body_processes
            else None
        )

    @classmethod
    def get_sample_id(cls, activity: Activity) -> Optional[str]:
        return (cls.get_face_processes(activity) or [{}])[0].get("sample_id")

    @classmethod
    def get_samples_ids(cls, activity: Activity) -> List[str]:
        face_processes = cls.get_face_processes(activity)
        return list(filter(None, map(lambda x: x.get("sample_id"), face_processes)))


def trace(name):
    def decorator_trace(func):
        @wraps(func)
        def decorator_wrapper(*args, **kwargs):
            tracer = get_tracer(__name__)
            with tracer.start_as_current_span(
                name
            ) if tracer else ContextStub() as span:
                return func(*args, **kwargs)

        return decorator_wrapper

    return decorator_trace


class SampleEnricher:
    image_api_version = "v2"

    @staticmethod
    def _deep_update(original_dict: dict, update_dict: dict):
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span(
            "deep_update"
        ) if tracer else ContextStub() as span:

            def check_values(index, old_value, new_value, original_obj) -> None:
                new_type = type(new_value)
                old_type = type(old_value)

                if new_type != old_type:
                    original_obj[index] = new_value

                if new_type == dict or new_type == list:
                    recursion(old_value, new_value)
                else:
                    original_obj[index] = new_value

            def recursion(old_obj, new_obj):
                if type(old_obj) != type(new_obj):
                    raise Exception(
                        "Different types are provided for merge. It is impossible to unambiguously resolve this case"
                    )

                if isinstance(old_obj, list):
                    for index, element in enumerate(new_obj):
                        if index >= len(old_obj):
                            old_obj.append(element)
                        else:
                            old_value = old_obj[index]

                            check_values(
                                index=index,
                                old_value=old_value,
                                new_value=element,
                                original_obj=old_obj,
                            )

                if isinstance(old_obj, dict):
                    for key, value in new_obj.items():
                        old_value = old_obj.get(key)

                        if old_value is None:
                            old_obj[key] = value
                        else:
                            check_values(
                                index=key,
                                old_value=old_value,
                                new_value=value,
                                original_obj=old_obj,
                            )

        recursion(original_dict, update_dict)

    @dataclass(frozen=True, order=True)
    class FunctionContainer:
        sort_index: int = field(init=False, repr=False)
        func_name: str = field(init=False)

        func: callable = field(compare=False)
        waited_events: Optional[Tuple] = None
        set_events: Optional[Tuple] = None

        def __post_init__(self):
            object.__setattr__(self, "sort_index", self.func.__name__)
            object.__setattr__(self, "func_name", self.func.__name__)

        def __str__(self):
            return f"{self.func_name}_{self.waited_events}_{self.set_events}"

        def get_event_wrapped_func(self, sample_enricher):
            async def func(*args, **kwargs):
                if (w_events := self.waited_events) is not None:
                    done, _ = await asyncio.wait(
                        [event.wait() for event in w_events], timeout=20
                    )
                    if not done:
                        raise Exception("Event timeout, check pipeline correctness")
                    if not all(
                        sample_enricher.blocked_events[event] for event in w_events
                    ):
                        return sample_enricher

                result, can_continue = await self.func(*args, **kwargs)

                if (s_events := self.set_events) is not None:
                    for event in s_events:
                        sample_enricher.blocked_events[event] = can_continue
                        event.set()

                return result

            return func

    fitter_validation_scheme = {
        "type": "object",
        "required": ["_image", "objects"],
        "properties": {
            "_image": {
                "type": "object",
            },
            "objects": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "class": {"type": "string", "enum": ["face", "body"]},
                        "keypoints": {
                            "type": "object",
                        },
                    },
                    "if": {"properties": {"class": {"const": "face"}}},
                    "then": {"required": ["keypoints"]},
                },
                "minimum": 1,
            },
        },
    }
    bbox_validation_scheme = {
        "type": "object",
        "required": ["_image", "objects"],
        "properties": {
            "_image": {
                "type": "object",
            },
            "objects": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "class": {"type": "string", "enum": ["face", "body"]},
                        "bbox": {
                            "type": "array",
                            "minItems": 4,
                            "maxItems": 4,
                            "items": {"type": "number"},
                        },
                    },
                    "if": {"properties": {"class": {"const": "face"}}},
                    "then": {"required": ["bbox"]},
                },
                "minimum": 1,
            },
        },
    }

    to_camel = [
        "total_score",
        "is_sharp",
        "sharpness_score",
        "is_evenly_illuminated",
        "illumination_score",
        "no_flare",
        "is_left_eye_opened",
        "left_eye_openness_score",
        "is_right_eye_opened",
        "right_eye_openness_score",
        "is_rotation_acceptable",
        "max_rotation_deviation",
        "not_masked",
        "not_masked_score",
        "is_neutral_emotion",
        "neutral_emotion_score",
        "is_eyes_distance_acceptable",
        "eyes_distance",
        "is_margins_acceptable",
        "margin_outer_deviation",
        "margin_inner_deviation",
        "is_not_noisy",
        "noise_score",
        "watermark_score",
        "has_watermark",
        "dynamic_range_score",
        "is_dynamic_range_acceptable",
        "background_uniformity_score",
        "is_background_uniform",
    ]

    to_snake = list(map(camel, to_camel))

    @classmethod
    def _convert_to_ias_format(cls, original_sample: dict) -> Tuple[dict, dict]:
        converted_sample = copy.deepcopy(original_sample)

        def recursion(
            original_sample_: Union[dict, list],
            result_sample_: Union[dict, list],
            previous_path: List[str],
            blob_paths: dict,
        ):
            if isinstance(original_sample_, dict):
                for key, value in original_sample_.items():
                    if key.startswith(BaseProcessManager.bsm_indicator) and isinstance(
                        value, dict
                    ):
                        new_path = copy.copy(previous_path)
                        new_path.append(key)
                        blob_paths["_".join(new_path)] = value
                        result_sample_[key] = base64.b64encode(
                            BlobMeta.objects.select_related("blob")
                            .get(id=value["id"])
                            .blob.data
                        ).decode("utf-8")
                    elif isinstance(value, (list, dict)):
                        new_path = copy.copy(previous_path)
                        new_path.append(key)

                        recursion(value, result_sample_[key], new_path, blob_paths)

                    if key in cls.to_camel:
                        result_sample_[camel(key)] = result_sample_.pop(key)

            if isinstance(original_sample_, list):
                for i, sample_object in enumerate(original_sample_):
                    if isinstance(sample_object, dict) or isinstance(
                        sample_object, list
                    ):
                        new_path = copy.copy(previous_path)
                        new_path.append(str(i))

                        recursion(
                            sample_object, result_sample_[i], new_path, blob_paths
                        )

        blob_paths = {}
        previous_path = []

        recursion(original_sample, converted_sample, previous_path, blob_paths)

        return blob_paths, converted_sample

    @classmethod
    def _convert_from_ias_format(cls, original_sample: dict, values_dict: dict):
        converted_sample = copy.deepcopy(original_sample)

        def recursion(
            original_sample_: Union[dict, list],
            result_sample_: Union[dict, list],
            previous_path: List[str],
            values_dict: dict,
        ):
            if isinstance(original_sample_, dict):
                for key, value in original_sample_.items():
                    snake_key = None

                    if key in cls.to_snake:
                        snake_key = snake(key)
                        result_sample_[snake_key] = result_sample_.pop(key)

                    result_key = snake_key or key

                    if key.startswith(BaseProcessManager.bsm_indicator):
                        new_path = copy.copy(previous_path)

                        new_path.append(result_key)

                        key_path = "_".join(new_path)
                        if key_path in values_dict:
                            result_sample_[result_key] = values_dict[key_path]

                    if isinstance(value, (list, dict)):
                        new_path = copy.copy(previous_path)
                        new_path.append(result_key)

                        recursion(
                            value, result_sample_[result_key], new_path, values_dict
                        )

            if isinstance(original_sample_, list):
                for i, sample_object in enumerate(original_sample_):
                    if isinstance(sample_object, dict) or isinstance(
                        sample_object, list
                    ):
                        new_path = copy.copy(previous_path)
                        new_path.append(str(i))

                        recursion(
                            sample_object, result_sample_[i], new_path, values_dict
                        )

        previous_path = []

        recursion(original_sample, converted_sample, previous_path, values_dict)

        return converted_sample

    @staticmethod
    async def _handle_image_api_response(response):
        try:
            result_json = await response.json()
        except ValueError:
            response.raise_for_status()

        if response.status >= 400:
            if (detail := result_json.get("detail")) is not None:  # noqa
                raise Exception(detail)
            else:
                raise Exception(str(result_json))

        return result_json

    @classmethod
    async def _handle_image(
        cls,
        session: ClientSession,
        image: Union[str, bytes],
        service_url: str,
        request_id: Optional[str] = None,
    ) -> dict:
        data = FormData()
        data.add_field("image", image, filename="image.jpg", content_type="image/jpeg")

        headers = {**get_current_tracing_context()}

        if request_id is not None:
            headers["X_REQUEST_ID"] = request_id

        async with session.post(
            url=f"{service_url}/{cls.image_api_version}/process/image",
            data=data,
            headers=headers,
        ) as response:
            return await cls._handle_image_api_response(response)

    @classmethod
    async def _handle_sample(
        cls,
        session: ClientSession,
        sample_data: dict,
        service_url: str,
        request_id: Optional[str] = None,
    ) -> dict:
        headers = {**get_current_tracing_context()}

        if request_id is not None:
            headers["X_REQUEST_ID"] = request_id

        async with session.post(
            url=f"{service_url}/{cls.image_api_version}/process/sample",
            json=sample_data,
            headers=headers,
        ) as response:
            return await cls._handle_image_api_response(response)

    async def _send_request_based_on_source(
        self, session, service_url: str, sample_validation_cheme: dict = None
    ):
        if not self._sample:
            return await self._handle_image(
                session=session,
                image=self._image,
                service_url=service_url,
                request_id=self.request_id,
            )
        else:
            if sample_validation_cheme is not None:
                # Check if current sample data is enough for quality_estimator
                # jsonschema.validate(self._sample, sample_validation_cheme)
                pass

            return await self._handle_sample(
                session=session,
                sample_data=self._sample,
                service_url=service_url,
                request_id=self.request_id,
            )

    def __init__(
        self,
        image: Optional[Union[str, bytes]] = None,
        sample_meta: Optional[dict] = None,
        request_id: Optional[str] = None,
    ):
        self._sample = {}
        self._image = self._sample_indexes = None
        self._sample_blob_ids = {}
        self.request_id = request_id
        self.events = namedtuple("Locks", ["face_detector_face_fitter_event"])(Event())
        self.blocked_events = {
            self.events.face_detector_face_fitter_event: False,
        }

        liveness_container_kwargs = {}
        if settings.LIVENESS_SERVICE_PROVIDER != 'face-detector-liveness-estimator':
            liveness_container_kwargs = {"waited_events": (self.events.face_detector_face_fitter_event,)}

        self.function_containers = namedtuple(
            "FunctionContainers",
            [
                "face_detector_face_fitter",
                "template_extractor",
                "liveness_estimator",
                "gender_estimator",
                "emotion_estimator",
                "mask_estimator",
                "age_estimator",
                "quality_estimator",
            ],
        )(
            self.FunctionContainer(
                self._face_detector_face_fitter, set_events=(self.events.face_detector_face_fitter_event,)
            ),
            self.FunctionContainer(
                self._template_extractor, waited_events=(self.events.face_detector_face_fitter_event,)
            ),
            self.FunctionContainer(
                self._liveness_estimator, **liveness_container_kwargs
            ),
            self.FunctionContainer(
                self._gender_estimator, waited_events=(self.events.face_detector_face_fitter_event,)
            ),
            self.FunctionContainer(
                self._emotion_estimator, waited_events=(self.events.face_detector_face_fitter_event,)
            ),
            self.FunctionContainer(
                self._mask_estimator, waited_events=(self.events.face_detector_face_fitter_event,)
            ),
            self.FunctionContainer(
                self._age_estimator, waited_events=(self.events.face_detector_face_fitter_event,)
            ),
            self.FunctionContainer(
                self._quality_estimator, waited_events=(self.events.face_detector_face_fitter_event,)
            ),
        )

        if (image is not None and sample_meta is not None) or (
            image is None and sample_meta is None
        ):
            raise Exception("Wrong SampleEnricher input")

        if sample_meta is not None:
            # jsonschema.validate(sample_meta, sample_meta_scheme)

            self._sample_indexes = {}

            decoded_img = (
                base64.standard_b64decode(sample_meta["$image"])
                if isinstance(sample_meta["$image"], str)
                else sample_meta["$image"]
            )
            fixed_validate_image(decoded_img)
            self._image = decoded_img

            self._sample_blob_ids, self._sample = self._convert_to_ias_format(
                sample_meta
            )

        if image is not None:
            decoded_img = (
                base64.standard_b64decode(image) if isinstance(image, str) else image
            )
            fixed_validate_image(decoded_img)
            self._image = decoded_img

    # Not use. Detect change bbox and face order
    # async def face_detector(self, session: ClientSession):
    #     detector_result = await self._send_request_based_on_source(session, settings.FACE_DETECTOR_SERVICE_URL)
    #
    #     self._deep_update(
    #         self._sample,
    #         detector_result
    #     )
    #
    #     return self
    async def face_detector_face_fitter(self, session: ClientSession):
        return (await self._face_detector_face_fitter(session))[0]

    async def _face_detector_face_fitter(self, session: ClientSession):
        # TODO: Добавить оброботку ошибок, когда появится использование body-detector
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("face-detector-face-fitter_outer") if tracer else ContextStub():
            fitter_result = await self._send_request_based_on_source(
                session, settings.image_api_service_map['face-detector-face-fitter']
            )
        self._deep_update(self._sample, fitter_result)

        return self, bool(fitter_result.get("objects"))

    async def face_detector_template_extractor(
        self, session: ClientSession, template_version=settings.DEFAULT_TEMPLATE_VERSION
    ):
        return (await self._face_detector_template_extractor(session, template_version))[0]

    async def _face_detector_template_extractor(
        self, session: ClientSession, template_version=settings.DEFAULT_TEMPLATE_VERSION
    ):
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("face-detector-template-extractor_outer") if tracer else ContextStub():
            template_result = await self._send_request_based_on_source(
                session, settings.image_api_service_map['face-detector-template-extractor']
            )

        self._deep_update(self._sample, template_result)
        return self, True

    async def template_extractor(
        self, session: ClientSession, template_version=settings.DEFAULT_TEMPLATE_VERSION
    ):
        return (await self._template_extractor(session, template_version))[0]

    async def _template_extractor(
        self, session: ClientSession, template_version=settings.DEFAULT_TEMPLATE_VERSION
    ):
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("template-extractor_outer") if tracer else ContextStub():
            template_result = await self._send_request_based_on_source(
                session, settings.image_api_service_map['template-extractor']
            )

        self._deep_update(self._sample, template_result)
        return self, True

    async def liveness_estimator(self, session: ClientSession):
        return (await self._liveness_estimator(session))[0]

    async def _liveness_estimator(self, session: ClientSession):
        try:
            tracer = get_tracer(__name__)
            with tracer.start_as_current_span("liveness-estimator_outer") if tracer else ContextStub():
                liveness_result = await self._send_request_based_on_source(
                    session, settings.image_api_service_map[settings.LIVENESS_SERVICE_PROVIDER]
                )

            for obj in liveness_result["objects"]:
                obj["liveness"]["message"] = ""

            self._deep_update(self._sample, liveness_result)
            can_continue = True
        except Exception as e:
            liveness_result = self._sample
            for obj in liveness_result["objects"]:
                obj["liveness"] = {
                    "value": "not estimated",
                    "message": e.__str__(),
                    "confidence": -1,
                }
            can_continue = False

        return self, can_continue

    async def emotion_estimator(self, session: ClientSession):
        return (await self._emotion_estimator(session))[0]

    async def _emotion_estimator(self, session: ClientSession):
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("emotion-estimator_outer") if tracer else ContextStub():
            emotion_result = await self._send_request_based_on_source(
                session,
                settings.image_api_service_map['emotion-estimator'],
                sample_validation_cheme=self.bbox_validation_scheme,
            )

        self._deep_update(self._sample, emotion_result)
        return self, True

    async def gender_estimator(self, session: ClientSession):
        return (await self._gender_estimator(session))[0]

    async def _gender_estimator(self, session: ClientSession):
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("gender-estimator_outer") if tracer else ContextStub():
            gender_result = await self._send_request_based_on_source(
                session,
                settings.image_api_service_map['gender-estimator'],
                sample_validation_cheme=self.bbox_validation_scheme,
            )

        self._deep_update(self._sample, gender_result)
        return self, True

    async def mask_estimator(self, session: ClientSession):
        return (await self._mask_estimator(session))[0]

    async def _mask_estimator(self, session: ClientSession):
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("mask-estimator_outer") if tracer else ContextStub():
            mask_result = await self._send_request_based_on_source(
                session,
                settings.image_api_service_map['mask-estimator'],
                sample_validation_cheme=self.bbox_validation_scheme,
            )

        self._deep_update(self._sample, mask_result)
        return self, True

    async def age_estimator(self, session: ClientSession):
        return (await self._age_estimator(session))[0]

    async def _age_estimator(self, session: ClientSession):
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("age-estimator_outer") if tracer else ContextStub():
            age_result = await self._send_request_based_on_source(
                session,
                settings.image_api_service_map['age-estimator'],
                sample_validation_cheme=self.bbox_validation_scheme,
            )

        self._deep_update(self._sample, age_result)

        return self, True

    async def quality_estimator(self, session: ClientSession):
        return (await self._quality_estimator(session))[0]

    async def _quality_estimator(self, session: ClientSession):
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("quality-assessment-estimator_outer") if tracer else ContextStub():
            quality_result = await self._send_request_based_on_source(
                session,
                settings.image_api_service_map['quality-assessment-estimator'],
                sample_validation_cheme=self.fitter_validation_scheme,
            )

        self._deep_update(self._sample, quality_result)
        return self, True

    def get_result(self) -> dict:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span(
            "get_result"
        ) if tracer else ContextStub() as span:
            if not self._sample:
                self._sample = {
                    "_image": {
                        "blob": base64.b64encode(self._image).decode("utf-8"),
                        "shape": None,
                        "dtype": None,
                        "format": "IMAGE",
                    }
                }
            # jsonschema.validate(self._sample, sample_meta_scheme_v3)
            return self._sample

    def _get_function_containers_by_fields(
        self, fields: List[str]
    ) -> Set[FunctionContainer]:
        _liveness_containers = []
        if settings.LIVENESS_SERVICE_PROVIDER != "face-detector-liveness-estimator":
            _liveness_containers.append(self.function_containers.face_detector_face_fitter)

        _liveness_containers.append(self.function_containers.liveness_estimator)

        function_mapping = {
            ("id", "class", "confidence", "bbox", "keypoints", "pose"): [
                self.function_containers.face_detector_face_fitter
            ],
            ("id", "class", "template"): [
                self.function_containers.face_detector_face_fitter,
                self.function_containers.template_extractor,
            ],
            ("id", "class", "confidence", "bbox", "liveness"): _liveness_containers,
            ("id", "class", "gender"): [
                self.function_containers.face_detector_face_fitter,
                self.function_containers.gender_estimator,
            ],
            ("id", "class", "age"): [
                self.function_containers.face_detector_face_fitter,
                self.function_containers.age_estimator,
            ],
            ("id", "class", "emotions"): [
                self.function_containers.face_detector_face_fitter,
                self.function_containers.emotion_estimator,
            ],
            ("id", "class", "has_medical_mask"): [
                self.function_containers.face_detector_face_fitter,
                self.function_containers.mask_estimator,
            ],
            ("id", "class", "bbox", "quality"): [
                self.function_containers.face_detector_face_fitter,
                self.function_containers.quality_estimator,
            ]
        }

        field_function_containers = []

        for field_ in fields:
            for key, value in function_mapping.items():
                if field_ in key:  # field set
                    field_function_containers.extend(value)
                    break

        return set(field_function_containers)

    async def async_execute_functions_by_fields(self, fields: List[str]):
        function_containers = self._get_function_containers_by_fields(fields)
        async with ClientSession() as session:
            tasks = [
                container.get_event_wrapped_func(self)(session)
                for container in function_containers
            ]
            await asyncio.gather(*tasks)

        return self.get_result()
