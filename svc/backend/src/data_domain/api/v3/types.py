import datetime
from typing import Optional, List, TypeVar, Generic

import strawberry
from strawberry import ID
from strawberry.file_uploads import Upload

from data_domain.api.v2.types import SampleOutput, MatchResult
from data_domain.models import Sample
from person_domain.api.v3.types import ProfileOutput
from platform_lib.exceptions import BadInputDataException
from platform_lib.types import JSON, CustomBinaryType
from django.apps import apps

profile_model = apps.get_model('person_domain', 'Profile')
person_model = apps.get_model('person_domain', 'Person')
T = TypeVar('T')


@strawberry.type(description="Information about emotions estimation")
class Emotions:
    @strawberry.field(description="Numerical value of manifestation of each estimated emotion")
    def confidence(self) -> float:
        return self.get('confidence')

    @strawberry.field(description="Emotion type: angry, disgusted, scared, happy, neutral, sad, surprised")
    def emotion(self) -> str:
        return self.get('emotion')


@strawberry.type(description="Estimation of face mask presence")
class MaskConfidenceValue:
    @strawberry.field(description="Numerical value of confidence that a person in the image is/isn’t wearing a mask")
    def confidence(self) -> float:
        return self.get('confidence')

    @strawberry.field(description="Verdict: true - masked person, false - unmasked person")
    def value(self) -> bool:
        return self.get('value')


@strawberry.type(description="Information about liveness estimation")
class LivenessConfidenceValue:
    @strawberry.field(description="Numerical value of confidence that the image belongs to a real person")
    def confidence(self) -> float:
        return self.get('confidence')

    @strawberry.field(description="Verdict: REAL - the face image belongs to a real person,"
                                  " FAKE - the face image doesn’t belong to a real person")
    def value(self) -> str:
        return self.get('value')

    @strawberry.field(description="Error message")
    def message(self) -> str:
        return self.get('message')


@strawberry.type(description="Information about saved BSM")
class BaseBSM:
    @strawberry.field(description="Format of a saved BSM", name="format")
    def format(self) -> Optional[str]:
        return self.get('format')

    @strawberry.field(description="Dtype of a raw BSM", name="dtype")
    def dtype(self) -> Optional[str]:
        return self.get('dtype')

    @strawberry.field(description="Shape of a raw BSM", name="shape")
    def shape(self) -> Optional[List[int]]:
        return self.get('shape')


@strawberry.type(description="Information about saved BSM")
class SavedBSM(BaseBSM):
    @strawberry.field(description="ID of a saved BSM", name="id")
    def id(self) -> str:
        return self.get('id')


@strawberry.type(description="Information about raw BSM")
class RawBSM(BaseBSM):
    @strawberry.field(description="Base64 data of BSM", name="blob")
    def blob(self) -> str:
        return self.get('blob')


T = TypeVar("T")


@strawberry.type(description="Information about biometric templates")
class Templates(Generic[T]):
    @strawberry.field(description="Template version 12v1000", name="_face_template_extractor_1000_12")
    def face_template_extractor_1000_12(self) -> T:
        return self.get('_face_template_extractor_1000_12')


@strawberry.type(description="Head rotation angles in degrees")
class Pose:
    @strawberry.field(description="Rotation around the vertical axis Y")
    def yaw(self) -> float:
        return self.get('yaw')

    @strawberry.field(description="Rotation around the horizontal axis X")
    def roll(self) -> float:
        return self.get('roll')

    @strawberry.field(description="Rotation around the horizontal axis Z")
    def pitch(self) -> float:
        return self.get('pitch')


@strawberry.type(description="Quality assessment algorithm operates with the following data:"
                             " Qaa Data related to operation of quality assessment algorithm")
class Quality:
    @strawberry.field(description="Numerical value that represents the score of overall image quality from 0 to 100")
    def total_score(self) -> float:
        return self.get('total_score')

    @strawberry.field(description="Boolean value that represents the image sharpness")
    def is_sharp(self) -> bool:
        return self.get('is_sharp')

    @strawberry.field(description="Numerical value that represents the sharpness score from 0 to 100")
    def sharpness_score(self) -> float:
        return self.get('sharpness_score')

    @strawberry.field(description="Boolean value that represents the illumination uniformity in the image")
    def is_evenly_illuminated(self) -> bool:
        return self.get('is_evenly_illuminated')

    @strawberry.field(description="Numerical value that represents the illumination uniformity score from 0 to 100")
    def illumination_score(self) -> float:
        return self.get('illumination_score')

    @strawberry.field(description="Boolean value that represents presence/absence of image flares")
    def no_flare(self) -> bool:
        return self.get('no_flare')

    @strawberry.field(description="Boolean value that represents the position of left eye (open/closed)")
    def is_left_eye_opened(self) -> bool:
        return self.get('is_left_eye_opened')

    @strawberry.field(description="Numerical value that represents"
                                  " the degree of left eye openness in points from 0 to 100")
    def left_eye_openness_score(self) -> float:
        return self.get('left_eye_openness_score')

    @strawberry.field(description="Boolean value that represents the position of right eye (open/closed)")
    def is_right_eye_opened(self) -> bool:
        return self.get('is_right_eye_opened')

    @strawberry.field(description="Numerical value that"
                                  " represents the degree of right eye openness in points from 0 to 100")
    def right_eye_openness_score(self) -> float:
        return self.get('right_eye_openness_score')

    @strawberry.field(description="Boolean value for acceptable/unacceptable values of yaw, pitch and roll angles")
    def is_rotation_acceptable(self) -> bool:
        return self.get('is_rotation_acceptable')

    @strawberry.field(description="Numerical value that represents"
                                  " the maximum degree of deviation for yaw, pitch and roll angles")
    def max_rotation_deviation(self) -> int:
        return self.get('max_rotation_deviation')

    @strawberry.field(description="Boolean value for presence/absence of face mask")
    def not_masked(self) -> bool:
        return self.get('not_masked')

    @strawberry.field(description="Numerical value of confidence"
                                  " that a person in the image isn’t wearing a mask in points from 0 to 100")
    def not_masked_score(self) -> float:
        return self.get('not_masked_score')

    @strawberry.field(description="Boolean value for presence/absence of neutral emotions")
    def is_neutral_emotion(self) -> bool:
        return self.get('is_neutral_emotion')

    @strawberry.field(description="Numerical value for score of neutral emotions in points from 0 to 100")
    def neutral_emotion_score(self) -> float:
        return self.get('neutral_emotion_score')

    @strawberry.field(description="Boolean value that represents the allowable/unallowable distance between eyes")
    def is_eyes_distance_acceptable(self) -> bool:
        return self.get('is_eyes_distance_acceptable')

    @strawberry.field(description="Numerical value that represents the distance between eyes in pixels")
    def eyes_distance(self) -> int:
        return self.get('eyes_distance')

    @strawberry.field(description="Boolean value that represents allowable/unallowable margins")
    def is_margins_acceptable(self) -> bool:
        return self.get('is_margins_acceptable')

    @strawberry.field(description="Numerical value of outer deviation in pixels")
    def margin_outer_deviation(self) -> int:
        return self.get('margin_outer_deviation')

    @strawberry.field(description="Numerical value of inner deviation in pixels")
    def margin_inner_deviation(self) -> int:
        return self.get('margin_inner_deviation')

    @strawberry.field(description="Boolean value that indicates presence/absence of noise in the image")
    def is_not_noisy(self) -> bool:
        return self.get('is_not_noisy')

    @strawberry.field(description="Numerical value that represents the score of image noise in points from 0 to 100")
    def noise_score(self) -> float:
        return self.get('noise_score')

    @strawberry.field(description="Numerical value of confidence that the image"
                                  " contains a watermark in points from 0 to 100")
    def watermark_score(self) -> float:
        return self.get('watermark_score')

    @strawberry.field(description="Boolean value for presence/absence of watermark in the image")
    def has_watermark(self) -> bool:
        return self.get('has_watermark')

    @strawberry.field(description="Numerical value that represents the score of"
                                  " dynamic range of intensity in points from 0 to 100")
    def dynamic_range_score(self) -> float:
        return self.get('dynamic_range_score')

    @strawberry.field(description="Boolean value that represents that the dynamic range"
                                  " of image intensity in the face area exceeds/doesn’t exceed the value of 128")
    def is_dynamic_range_acceptable(self) -> bool:
        return self.get('is_dynamic_range_acceptable')

    @strawberry.field(description="Boolean value for background uniformity")
    def is_background_uniform(self) -> bool:
        return self.get('is_background_uniform')

    @strawberry.field(description="Numerical value for background uniformity score in points from 0 to 100")
    def background_uniformity_score(self) -> float:
        return self.get('background_uniformity_score')


@strawberry.type(description="Information on face detection and processing")
class FaceProcessInfo(Generic[T]):
    @strawberry.field(description="Face identification number. Each face has a unique id within the list")
    def id(self) -> int:
        return self.get('id')

    @strawberry.field(name="class", description="Object type. The object for current API always has a face type")
    def class_(self) -> str:
        return self.get('class')

    @strawberry.field(description="Face detection confidence")
    def confidence(self) -> float:
        return self.get('confidence')

    @strawberry.field(
        description="Bounding Box. The rectangle that represents face bounds in the image."
                    " The bounds are calculated relative to the coordinates of the original image."
                    " The first two bbox coordinates are X and Y of the left top point,"
                    " the second two are X and Y of the right bottom point"
    )
    def bbox(self) -> List[float]:
        return self.get('bbox')

    @strawberry.field(description="Facial anthropometric points."
                                  " List of repeated X, Y and Z coordinates relative to the original image")
    def keypoints(self) -> JSON:
        return self.get('keypoints')

    @strawberry.field(description="Estimation of emotions from face image")
    def emotions(self) -> List[Emotions]:
        return self.get('emotions')

    @strawberry.field(description="Face mask presence", name='has_medical_mask')
    def has_medical_mask(self) -> MaskConfidenceValue:
        return self.get('has_medical_mask')

    @strawberry.field(description="Biometric template coded in base64 used for face comparison")
    def template(self) -> Templates[T]:
        return self.get('template')

    @strawberry.field(description="Gender estimation from face image")
    def gender(self) -> str:
        return self.get('gender')

    @strawberry.field(description="Age estimation from face image")
    def age(self) -> int:
        return self.get('age')

    @strawberry.field(description="Head rotation angles")
    def pose(self) -> Pose:
        return self.get('pose')

    @strawberry.field(description="Estimation that a person in the image is real or fake")
    def liveness(self) -> LivenessConfidenceValue:
        return self.get('liveness')

    @strawberry.field(description="Information about  image quality")
    def quality(self) -> Quality:
        return self.get('quality')


@strawberry.type(description="Result of image processing")
class ImageProcessInfo:
    @strawberry.field(description="Image in base64 format", name="_image")
    def image(self) -> RawBSM:
        return self.get('_image')

    @strawberry.field(description="Result of face detection and processing")
    def objects(self) -> List[FaceProcessInfo[RawBSM]]:
        return self.get('objects')

    @strawberry.field(description="Result of image processing in sample format")
    def sample(self) -> JSON:
        return self


@strawberry.type(description="Result of creating sample")
class CreateSampleInfo:
    id: ID = strawberry.field(description="Sample ID")

    creation_date: Optional[datetime.datetime] = strawberry.field(
        description="Sample creation date in ISO 8601 format with time zone", default=None
    )
    last_modified: Optional[datetime.datetime] = strawberry.field(
        description="Sample creation date in ISO 8601 format with time zone", default=None
    )

    @strawberry.field(description="Image in BSM format", name="_image")
    def image(self) -> Optional[SavedBSM]:
        return self.meta.get('_image')

    @strawberry.field(description="Result of face detection and processing")
    def objects(self) -> List[FaceProcessInfo[SavedBSM]]:
        return self.meta.get('objects')


@strawberry.type
class PersonSearchResult:
    @strawberry.field
    def sample(root) -> Optional[SampleOutput]:
        person_id = root.get('vector_id')
        person = profile_model.objects.filter(person_id=person_id).first()
        if person and (sample_id := person.info.get('main_sample_id')):
            return Sample.objects.get(id=sample_id)

    @strawberry.field
    def profile(root) -> Optional[ProfileOutput]:
        person_id = root.get('vector_id')
        return profile_model.objects.filter(person_id=person_id).first()

    @strawberry.field
    def match_result(root) -> MatchResult:
        return MatchResult(fa_r=root['fa_r'], fr_r=root['fr_r'], score=root['score'], distance=root['distance'])  # noqa


@strawberry.type
class SearchType:
    @strawberry.field
    def template(root) -> str:
        return root.get('template')

    @strawberry.field
    def search_result(root) -> List[PersonSearchResult]:
        return root.get('search_result')


@strawberry.input(description="Input type that represent image information in base64 or Upload format")
class ImageInput:
    upload: Optional[Upload] = None
    base64: Optional[CustomBinaryType] = None

    def __init__(self, upload: Optional[Upload] = None,
                 base64: Optional[CustomBinaryType] = None):
        if sum(map(bool, [upload, base64])) != 1:
            raise BadInputDataException("0x47255975")
        self.upload = upload
        self.base64 = base64


@strawberry.input(description="Input type that represent custom_input for sample or sample id")
class SampleInput(Generic[T]):
    custom_input: Optional[T] = None
    sample_id: Optional[ID] = None

    def __init__(self,
                 custom_input: Optional[T] = None,
                 sample_id: Optional[ID] = None):
        if sum(map(bool, [custom_input, sample_id])) != 1:
            raise BadInputDataException("0xd5e91ac0")
        self.custom_input = custom_input
        self.sample_id = sample_id
