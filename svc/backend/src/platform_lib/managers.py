import json
import uuid
from abc import abstractmethod, ABC
from enum import Enum
from typing import Optional, Dict, List, Union, Tuple, Callable, Any

import bson
import jsonschema
import requests
from django.core.cache import cache

from platform_lib.validation.schemes import activity_meta_scheme


class BaseProcessManager:
    """
    A class with basic methods for working with processes
    """
    class ProcessClass(str, Enum):
        FACE = "face"
        BODY = "body"
        HUMAN = "human"
        ROI = "roi"
        MEDIA = "media"

    bsm_indicator = "$"

    key_process_info_values = {'quality', 'age', 'gender', 'time_interval', 'finalized', 'class'}

    def __init__(self, processes: List[Dict]):
        self.processes = processes

        # For static method replacing
        self._get_processes = self._get_processes_instance

    @staticmethod
    def _get_processes(process_class: ProcessClass, processes: List[Dict]) -> List[Dict]:
        """
        Get all processes witch class is **process_class**
        Parameters
        ----------
        process_class: ProcessClass
            Process class that defines witch processes is need to be obtained.  Search in **processes**
        processes: List[Dict]:
            List of processes to search in
        Returns
        -------
        List[Dict]
            List of process with process_class
        """
        return list(filter(lambda track: track.get('object', {}).get('class') == process_class.value, processes))

    def _get_processes_instance(self, process_class: ProcessClass) -> List[Dict]:
        """
        Get all processes witch class is **process_class**. Search in **self.processes**
        Parameters
        ----------
        process_class: ProcessClass
            Process class that defines witch processes is need to be obtained
        Returns
        -------
        List[Dict]
            List of process with process_class
        """
        return list(filter(lambda track: track.get('object', {}).get('class') == process_class.value, self.processes))

    @classmethod
    def _iterate_through_process(cls,
                                 process: Dict,
                                 function: Callable,
                                 function_extra: Optional[Dict] = None,
                                 only_bsm: Optional[bool] = False):
        """
        Function that goes through process dict by recursion and call **function** if key of **process** element
        is bsm or located in **key_process_info_values**. All

        Parameters
        ----------
        process: Dict
            Process dict
        function: Callable
            Function that called at elements that start with bsm_indicator
            or located in key_process_info_values.
            Function arguments: key: dict key; value: dict value; process: dict element that iterated now;
            and all kwargs from function_extra if passed
        function_extra: Optional[Dict]
            Additional function arguments
        only_bsm: Optional[False]
            Call function only on keys that start with bsm_indicator
        """
        if isinstance(process, dict):
            for key, value in process.items():
                if key.startswith(cls.bsm_indicator) or (not only_bsm and (key in cls.key_process_info_values)):
                    function(key, value, process, **(function_extra or {}))
                else:
                    cls._iterate_through_process(value, function, function_extra, only_bsm)

        elif isinstance(process, list):
            for value in process:
                cls._iterate_through_process(value, function, function_extra, only_bsm)

    @classmethod
    def get_process_info(cls, process: Dict) -> Dict:
        """
        Get all process info, namely fields that start with **bsm_indicator**
        and fields that keys located in **key_process_info_values**

        Parameters
        ----------
        process: Dict
            Process information in dictionary

        Returns
        -------
        Dict:
            Dictionary with obtained data
        """
        process_info = {}

        def function(key, value, _, result_variable):
            result_variable[key] = value

        cls._iterate_through_process(process, function, function_extra={"result_variable": process_info})

        return process_info

    @classmethod
    def get_process_class(cls, process: Dict) -> Optional[ProcessClass]:
        """
        Get process class

        Parameters
        ----------
        process: Dict
            Process information in dictionary

        Returns
        -------
        ProcessClass:
            Process class representation
        """
        process_class = process.get('object', {}).get('class')

        return cls.ProcessClass(process_class) if process_class is not None else None

    @classmethod
    def is_process_finalized(cls, process: Dict) -> bool:
        """
        Defines if process finalized or not by checking finalized field in process data

        Parameters
        ----------
        process: Dict
            Process dict witch need to checked

        Returns
        -------
        bool:
            Process finalized or not
        """
        return process.get('finalized', True)

    @classmethod
    def get_process_timeinterval(cls, process: Dict) -> Tuple[Optional[str], Optional[str]]:
        """
        Get process time interval

        Parameters
        ----------
        process: Dict
            Process information in dictionary

        Returns
        -------
        Tuple[Optional[str], Optional[str]]
            List of two values. First is start time, second is end time.
        """
        timeinterval = process.get('time_interval', [None, None])

        return timeinterval[0], timeinterval[1]

    @classmethod
    def is_media_process(cls, processes: List[Dict]) -> bool:
        """
        Defines if list of process contains media process or not
        Parameters
        ----------
        processes: List[Dict]
            List of process that need to be checked

        Returns
        -------
        bool:
            List of process have media or not
        """
        media = cls._get_processes(cls.ProcessClass.MEDIA, processes)
        return bool(media)

    def get_human_processes(self) -> List[Dict]:
        """
        Get all human processes

        Returns
        -------
        List[Dict]:
            List of human processes in dictionaries
        """
        return self._get_processes(self.ProcessClass.HUMAN)

    def get_face_processes(self) -> List[Dict]:
        """
        Get all face processes

        Returns
        -------
        List[Dict]:
            List of face processes in dictionaries
        """
        return self._get_processes(self.ProcessClass.FACE)

    def get_body_processes(self) -> List[Dict]:
        """
        Get all body processes

        Returns
        -------
        List[Dict]:
            List of body processes in dictionaries
        """
        return self._get_processes(self.ProcessClass.BODY)

    def get_roi_process(self) -> List[Dict]:
        """
        Get all roi processes

        Returns
        -------
        List[Dict]:
            List of roi processes in dictionaries
        """
        return self._get_processes(self.ProcessClass.ROI)


class RawProcessManager(BaseProcessManager):

    @classmethod
    def validate_sample_meta(cls, meta: Dict) -> bool:

        def json_decoder(o: Union[list, tuple, str]):
            if isinstance(o, str):
                try:
                    return int(o)
                except ValueError:
                    try:
                        return float(o)
                    except ValueError:
                        return o
            elif isinstance(o, dict):
                return {k: json_decoder(v) for k, v in o.items()}
            elif isinstance(o, (list, tuple)):
                return type(o)([json_decoder(v) for v in o])
            else:
                return o

        try:
            jsonschema.validate(meta, activity_meta_scheme)
        except jsonschema.ValidationError:
            try:
                # bson doesn't decode numbers therefore try to decode meta data
                s = {k: json_decoder(v) for k, v in meta.items()}
                jsonschema.validate(s, activity_meta_scheme)
            except Exception:
                return False
        return True

    @classmethod
    def __bsm(cls, key: str, blob: bytes) -> Dict:
        return {
            f'{cls.bsm_indicator}{key}': {
                'format': 'IMAGE',
                'blob': blob
            }
        }

    @classmethod
    def parse_extra(cls, data: Dict) -> Dict:
        result = {k: v for k, v in data.items() if k not in ['prediction']}
        prediction = json.loads(data.get('prediction', '{}'))
        if 'objects' in prediction:
            result['objects'] = prediction['objects']
        return result

    @classmethod
    def decode(cls, data: bytes) -> Tuple[Dict, bool]:
        """
        Decodes abstract sample got over network

        Parameters
        ----------
        data: bytes
            "network sample" or raw_image

        Returns
        -------
        Tuple[Dict, bool]:
            decoded sample and flag that specifies is input was a raw image or "network sample"
        """
        version_size = 1
        version = int.from_bytes(data[:version_size], byteorder='big')
        if version == 1:
            return bson.loads(data[version_size:]), False

        # assume that it is a raw image and pack it to bsm format
        return cls.__bsm('image', data), True

    @classmethod
    def parse_human_processes(cls, processes: List[Dict]) -> List[Dict]:
        humans = cls._get_processes(cls.ProcessClass.HUMAN, processes)
        humans_pack = []

        def get_child(parent_id: str, output: list):
            for proc in processes:
                if proc.get('parent') == parent_id:
                    output.append(proc)
                    get_child(proc['id'], output)

        for human in humans:
            if human.get('finalize', True) and not human.get('object', {}).get('id'):
                continue
            res = [human]
            get_child(human['id'], res)
            humans_pack.append({'processes': res})

        return humans_pack

    @classmethod
    def extract_bsms(cls, meta: Dict) -> Tuple[Dict, List[Tuple]]:
        bsms = []

        def function(key, value, element, result_value):
            result_value.append((key, value, str(uuid.uuid4())))
            element[key] = len(result_value) - 1

        cls._iterate_through_process(meta, function, function_extra={"result_value": bsms}, only_bsm=True)

        return meta, bsms

    @classmethod
    def substitute_bsms(cls, meta: Dict, bsms: List) -> Dict:

        def function(key, value, element, bsms):
            if isinstance(value, int):
                element[key] = bsms[value]

        cls._iterate_through_process(meta, function, function_extra={"bsms": bsms}, only_bsm=True)

        return meta


class ActivityProcessManager(BaseProcessManager):

    @classmethod
    def _get_blob_ids(cls, process: Dict) -> List[str]:
        blob_ids = []

        def function(key, value, element, result_value):
            result_value.append(value["id"])

        cls._iterate_through_process(process, function, function_extra={"result_value": blob_ids}, only_bsm=True)

        return blob_ids

    def __init__(self, activity_data: Dict):
        activity_processes = activity_data['processes']
        super().__init__(activity_processes)
        assert len(self.get_human_processes()) == 1, "Wrong activity. To many human processes"

    @classmethod
    def get_blob_items(cls, process: Dict) -> List[Tuple[str, Union[int, dict]]]:
        blob_ids = []

        def function(key, value, element, result_value):
            result_value.append((key, value))

        cls._iterate_through_process(process, function, function_extra={"result_value": blob_ids}, only_bsm=True)

        return blob_ids

    def get_age_gender(self) -> Tuple[Optional[int], Optional[str]]:
        face_processes = self.get_face_processes()
        if len(face_processes) == 0:
            return None, None

        face_info = self.get_process_info(face_processes[0])

        return face_info.get('age'), face_info.get('gender')

    def get_face_best_shot(self) -> Optional[Dict]:
        face_processes = self.get_face_processes()
        if len(face_processes) == 0:
            return None

        face_info = self.get_process_info(face_processes[0])

        return face_info.get('$best_shot')

    def get_body_best_shot(self) -> Optional[Dict]:
        body_processes = self.get_body_processes()
        if len(body_processes) == 0:
            return None

        body_info = self.get_process_info(body_processes[0])

        return body_info.get('$best_shot')

    def get_template(self, template_version: str) -> Optional[Dict]:
        face_processes = self.get_face_processes()
        if len(face_processes) == 0:
            return None

        face_info = self.get_process_info(face_processes[0])

        return face_info.get(f'${template_version}')

    def get_human_process(self) -> Dict:
        return self.get_human_processes()[0]

    def is_activity_finalized(self) -> bool:
        human = self.get_human_process()

        return self.is_process_finalized(human)

    def get_human_timeinterval(self) -> Tuple[Optional[str], Optional[str]]:
        return self.get_process_timeinterval(self.get_human_process())

    def get_person_id(self) -> Optional[str]:
        return self.get_human_process().get('object', {}).get('id')

    def get_activity_blob_ids(self) -> List[str]:
        blob_ids = []
        for process in self.processes:
            blob_ids += self._get_blob_ids(process)

        return blob_ids

    def get_template_ids(self):
        template_ids = []

        def function(key, value, element, result_value):
            if 'template' in key:
                result_value.append(value['id'])

        for process in self.processes:
            self._iterate_through_process(process, function, function_extra={'result_value': template_ids},
                                          only_bsm=True)

        return template_ids


class RealtimeImageCacheManager:
    rlt_face_key_format = "rlt_face_image_{}"
    rlt_body_key_format = "rlt_body_image_{}"

    @classmethod
    def get_profile_id_from_key(cls, rlt_key: str) -> str:
        split_mass = rlt_key.split('_')
        return split_mass[len(split_mass) - 1]

    @classmethod
    def get_realtime_keys(cls, profile_id: Union[str, uuid.UUID]) -> Tuple[str, str]:
        return cls.rlt_face_key_format.format(profile_id), cls.rlt_body_key_format.format(profile_id)

    @classmethod
    def set_realtime_image_cache(cls, profile_id: Union[str, uuid.UUID], face_image, body_image):
        cls.__set_image_in_cache(cls.rlt_face_key_format.format(profile_id), face_image)
        cls.__set_image_in_cache(cls.rlt_body_key_format.format(profile_id), body_image)

    @staticmethod
    def __set_image_in_cache(key: str, image: bytes) -> None:
        cache.set(key, image)

    @staticmethod
    def get_image_from_cache(key: str) -> bytes:
        return cache.get(key)


class CacheManager(ABC):
    timeout = 10

    @classmethod
    def cache_set(cls, key: str, value: Any) -> None:
        cache.set(key, value, timeout=cls.timeout)

    @staticmethod
    @abstractmethod
    def _build_cache_key(*args, **kwargs) -> str:
        pass


class GetImageCacheManager:
    timeout = 43200
    cache_prefix = 'get-image'

    @classmethod
    def set(cls, key, value: bytes):
        return cache.set(f'{cls.cache_prefix}-{key}', value, timeout=cls.timeout)

    @classmethod
    def get(cls, key) -> Optional[bytes]:
        return cache.get(f'{cls.cache_prefix}-{key}')
