import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from label_domain.models import Label
from user_domain.models import Workspace


class Person(models.Model):
    id = models.UUIDField(primary_key=True, unique=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, related_name='persons', on_delete=models.CASCADE, null=False)
    samples = models.ManyToManyField(to='data_domain.Sample', related_name="persons", blank=True)
    info = models.JSONField(default=dict, help_text="Person's info")

    last_modified = models.DateTimeField(auto_now=True, null=True, blank=True)
    creation_date = models.DateTimeField(auto_now_add=True, null=True, blank=True, db_index=True)

    class Meta:
        db_table = 'person_domain_person'
        verbose_name_plural = 'Persons'


class Profile(models.Model):
    id = models.UUIDField(primary_key=True, unique=True, default=uuid.uuid4, editable=False)
    info = models.JSONField(default=dict, help_text="Profile's info")

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='profiles', null=False)
    person = models.OneToOneField(Person, related_name='profile', blank=True, null=True, on_delete=models.CASCADE)
    samples = models.ManyToManyField(to='data_domain.Sample', related_name="profile", blank=True)
    profile_groups = models.ManyToManyField(Label, related_name="profiles", through="ProfileGroup")

    last_modified = models.DateTimeField(auto_now=True, null=True, blank=True)
    creation_date = models.DateTimeField(auto_now_add=True, null=True, blank=True, db_index=True)

    class Meta:
        db_table = 'person_domain_profile'
        verbose_name_plural = 'Profiles'


class ProfileGroup(models.Model):
    id = models.UUIDField(primary_key=True, unique=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey(Profile, related_name='link_to_label', on_delete=models.CASCADE)
    label = models.ForeignKey(Label, related_name='link_to_profile', on_delete=models.CASCADE)

    class Meta:
        unique_together = ("profile", "label")


class ProfileSettings(models.Model):

    id = models.UUIDField(primary_key=True, unique=True, default=uuid.uuid4, editable=False)
    workspace_id = models.UUIDField(null=False, editable=False)

    extra_fields = ArrayField(models.CharField(max_length=32), default=list)

    last_modified = models.DateTimeField(auto_now=True, null=True, blank=True)
    creation_date = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    objects = models.Manager()

    class Meta:
        db_table = 'profile_settings'
        verbose_name_plural = 'ProfileSettings'


# @receiver(pre_delete, sender=Profile)
# def pre_delete_profile(sender, instance, *args, **kwargs):
#     with transaction.atomic():
#         if instance.samples:
#             for sample in instance.samples.all():
#                 if sample.profile.vaqlues_list('id') == 1:
#                     print(f"Was deleted {sample} related with {instance}")
#                     sample.delete()
#                 else:
#                     print(f"Was not deleted {sample} related with {instance}, because he has more than one profile")


@receiver(post_save, sender=Profile)
def post_save_profile(sender, instance, created, *args, **kwargs):
    # circular imports
    from person_domain.managers import ProfileCountManager

    if not created:
        return

    try:
        ProfileCountManager.incr()
    except ValueError:
        count = sender.objects.count()
        ProfileCountManager.set_count(count)


@receiver(post_delete, sender=Profile)
def post_delete_profile(sender, instance, *args, **kwargs):
    # circular imports
    from person_domain.managers import ProfileCountManager

    try:
        ProfileCountManager.decr()
    except ValueError:
        ProfileCountManager.set_count(sender.objects.count())
