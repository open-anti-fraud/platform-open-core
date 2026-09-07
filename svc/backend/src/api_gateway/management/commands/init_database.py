from django.db import transaction
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ObjectDoesNotExist

from main import settings
import logging
import os
from glob import glob
from pathlib import Path

from user_domain.managers import EmailManager, WorkspaceManager, AccessManager
from user_domain.models import Workspace, Access, EmailTemplate

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    def handle(self, *args, **options):
        admin_email = os.environ['PLATFORM_ADMIN_EMAIL']
        default_email = os.environ['PLATFORM_DEFAULT_EMAIL']
        users = [
            {
                'username': admin_email,
                'password': os.environ['PLATFORM_ADMIN_PASSWORD'],
                'email': admin_email,
                'workspace_title': "Admin's workspace",
                'is_superuser': True
            },
            {
                'username': default_email,
                'password': os.environ['PLATFORM_DEFAULT_PASSWORD'],
                'email': default_email,
                'workspace_title': "Default user's workspace",
                'is_on_premise': True
            }
        ]

        with transaction.atomic():
            qa_group, _ = Group.objects.get_or_create(name=settings.QA_GROUP)
            standalone_group, _ = Group.objects.get_or_create(name=settings.STANDALONE_GROUP)

            for u_data in users:
                is_on_prem = bool(u_data.get('is_on_premise'))
                if not is_on_prem or (is_on_prem == bool(settings.IS_ON_PREMISE)):
                    created, user = self.__create_user(u_data)
                    if created:
                        self.__create_ws_for_user(u_data)
                        user.groups.add(standalone_group.id)
                        user.save()

        # Add email to the database if they are missing
        self.__create_email_templates()
        print('* done *')

    @classmethod
    def __create_user(cls, data: dict):
        print('* Creating user..')
        UserModel = get_user_model()
        try:
            user = UserModel.objects.get(username=data['username'])
            print('** User already exists \n')
            return False, user
        except ObjectDoesNotExist:
            create_func = UserModel.objects.create_superuser \
                if data.get('is_superuser') else \
                UserModel.objects.create_user

            user = create_func(data['username'], data['email'], data['password'])
            print(f'** User created \n')
            return True, user

    @classmethod
    def __create_ws_for_user(cls, data: dict):
        print('* Creating workspace for user...')

        workspace = WorkspaceManager.create_workspace(
            title=data['workspace_title'],
            username=data['email'],
            password=data['password']
        )
        locked_user = get_user_model().objects.select_for_update().get(username=data['username'])
        locked_workspace = Workspace.objects.select_for_update().get(id=workspace.id)
        # LabelManager.create_default_labels(str(workspace.id))
        AccessManager.create_access(user=locked_user, workspace=locked_workspace, permissions=Access.OWNER)
        print('** Workspace for user successfully created \n')

    @classmethod
    def __create_email_templates(cls):
        SUBJECT_MAP = {
            EmailManager.RESET_PASSWORD: f'Reset password for {settings.EMAIL_PRODUCT_NAME}',
            EmailManager.CONFIRM_EMAIL: f'Confirm registration in {settings.EMAIL_PRODUCT_NAME}',
        }

        default_subject = f'Message from {settings.EMAIL_PRODUCT_NAME}'

        for email_path in glob('*_domain/templates/*.html'):
            email_key = Path(email_path).stem

            if not EmailTemplate.objects.filter(template_key=email_key).exists():
                subject = SUBJECT_MAP.get(email_key, default_subject)
                with open(email_path, 'r', encoding='utf-8') as template_body:
                    body = template_body.read()

                EmailTemplate.objects.create(template_key=email_key, subject=subject, body=body)
