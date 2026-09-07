from django.db.models.signals import post_save
from django.dispatch import receiver

from collector_domain.managers import AgentIndexEventManager
from data_domain.managers import SampleManager
from person_domain.models import Profile, ProfileSettings
from platform_lib.utils import utcnow_with_tz, convert_datestr_to_date
from user_domain.models import Workspace
from django.conf import settings


def get_age_from_birthday(birthday: str) -> int:
    today = utcnow_with_tz()
    birthdate = convert_datestr_to_date(birthday)

    one_or_zero = ((today.month, today.day) < (birthdate.month, birthdate.day))

    year_difference = today.year - birthdate.year
    age = year_difference - one_or_zero

    return age


@receiver(post_save, sender=Workspace)
def create_profile_settings(sender, instance, **kwargs):
    """Creates ProfileSettings object after creating a Workspace object in database."""
    ProfileSettings.objects.create(workspace_id=str(instance.id), extra_fields=settings.REQUIRED_PROFILE_FIELDS)


class ProfileMutationEventManager:
    def __init__(self, profile: Profile):
        """The manager must be initialized before profile state is changing"""
        self.workspace = profile.workspace
        self.profile = profile
        self.workspace_id = str(self.workspace.id)
        self.profile_id = str(profile.id)
        self.person_id = str(profile.person_id)
        self.aiem = AgentIndexEventManager(self.workspace_id)
        self.prev_group_count = profile.profile_groups.count()
        self.template_version = self.workspace.config['template_version']
        self.__sample_meta = None

    @property
    def sample_meta(self):
        if not self.__sample_meta:
            self.__sample_meta = SampleManager.get_sample(self.workspace_id, self.profile.info['main_sample_id']).meta
        return self.__sample_meta

    def data(self, profile_group_ids):
        return {
            "profileGroups": profile_group_ids,
            "template": {
                "id": SampleManager.get_template_id(self.sample_meta, self.template_version),
                "type": self.template_version,
                "binaryData": SampleManager.get_raw_template(self.sample_meta, self.template_version)
            }
        }

    def add_profile(self, profile_group_ids):
        if profile_group_ids:
            self.aiem.add_profile(self.profile_id, self.person_id, self.data(profile_group_ids))

    def update_profile(self, profile_group_ids):
        if profile_group_ids is None:
            return

        if self.prev_group_count:
            if profile_group_ids:
                self.aiem.update_profile(self.profile_id, self.person_id, self.data(profile_group_ids))
            else:
                self.aiem.delete_profile(self.profile_id, self.person_id)
        else:
            self.add_profile(profile_group_ids)

    def delete_profile(self):
        if self.prev_group_count:
            self.aiem.delete_profile(self.profile_id, self.person_id)

    def forced_delete_profile(self):
        self.aiem.delete_profile(self.profile_id, self.person_id)
