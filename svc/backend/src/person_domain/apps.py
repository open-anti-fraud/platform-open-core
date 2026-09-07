from django.apps import AppConfig
from django.db.models.signals import post_migrate


def cache_update(sender, **kwargs):
    from person_domain.models import Profile
    from person_domain.managers import ProfileCountManager

    count = Profile.objects.count()
    ProfileCountManager.set_count(count)


class PersonDomainConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'person_domain'

    def ready(self):
        post_migrate.connect(cache_update, sender=self)
