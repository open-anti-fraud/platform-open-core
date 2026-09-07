import os
from celery import Celery
from main import settings

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'main.settings')

app = Celery('main')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()


app.conf.task_routes = {
    'collector_domain.tasks.*': {'queue': settings.AGENT_EVENTS_QUEUE},
    'data_domain.tasks.finalize_dangling_activites': {'queue': settings.ACTIVITY_EVENTS_QUEUE},
    'person_domain.tasks.duplicate_persons': {'queue': settings.SERVICE_QUEUE},
    'person_domain.tasks.cache_update': {'queue': settings.LICENSING_EVENTS_QUEUE},
    'data_domain.tasks.add_to_activity_index': {'queue': settings.ACTIVITY_MATCHER_EVENTS_QUEUE},
    'data_domain.tasks.activity_retention_policy': {'queue': settings.RETENTION_POLICY_QUEUE},
    'data_domain.tasks.sample_retention_policy': {'queue': settings.RETENTION_POLICY_QUEUE},
    'person_domain.tasks.deferred_delete': {'queue': settings.DEFERRED_PROFILE_DELETION},
    'person_domain.tasks.deferred_remove_profiles_from_groups':
        {'queue': settings.DEFERRED_REMOVE_PROFILES_FROM_GROUPS_QUEUE},
    'person_domain.tasks.deferred_remove_profiles_field': {'queue': settings.DEFERRED_REMOVE_PROFILES_FIELD_QUEUE},
    'person_domain.tasks.create_deferred_remove_tasks': {'queue': settings.CREATE_DEFERRED_REMOVE_TASKS_QUEUE},
}


# if not settings.IS_ON_PREMISE:
#     app.conf.beat_schedule.update({
#         'send_usage_records': {
#             'task': 'licensing.tasks.send_usage_records',
#             'schedule': crontab(minute=30, hour=23),
#             'args': ()
#         },
#         'clean-deactivated-workspaces': {
#             'task': 'data_domain.tasks.clean_deactivated_workspaces',
#             'schedule': timedelta(minutes=10),
#             'args': ()
#         }
#     })

#     app.conf.task_routes.update({
#         'data_domain.tasks.clean_deactivated_workspaces': {'queue': settings.RETENTION_POLICY_QUEUE},
#         'licensing.tasks.*': {'queue': settings.LICENSING_EVENTS_QUEUE},
#     })

app.conf.timezone = 'UTC'
