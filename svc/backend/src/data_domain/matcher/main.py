import json
import logging
import requests
from typing import List

from plib.tracing.utils import get_current_tracing_context

from user_domain.models import Workspace
from django.conf import settings

logger = logging.getLogger(__name__)


class ActivityMatcherAPI:
    @classmethod
    def set_base(cls, index_key: str, template_version: str):
        query = '''
        mutation($indexKey: String!, $templateVersion: String!)
            {
                setBase(indexKey: $indexKey, templateVersion: $templateVersion)
                    {ok, templatesCount}
            }'''
        variables = {'indexKey': index_key, 'templateVersion': template_version}
        operation = {'query': query, 'variables': variables}

        response = cls.__graph_request(operation)
        errors = response.get('errors')

        if errors is not None:
            logger.error(msg=f'ActivityMatcher returned an error. {errors}')
            return {}

        search_data = response.get('data', {}).get('setBase', {}).get('templatesCount', {})

        return search_data if search_data is not None else {}

    @classmethod
    def set_base_add(cls, index_key: str, template_version: str, templates_info: list):
        query = '''
        mutation($indexKey: String!, $templateVersion: String!, $templatesInfo: [TemplateInfoType!]!)
            {
                setBaseAdd(indexKey: $indexKey, templateVersion: $templateVersion, templatesInfo: $templatesInfo)
                    {ok, templatesCount}
            }'''
        variables = {
            'indexKey': index_key,
            'templateVersion': template_version,
            'templatesInfo': templates_info
        }
        operation = {'query': query, 'variables': variables}

        response = cls.__graph_request(operation)
        errors = response.get('errors')

        if errors is not None:
            logger.error(msg=f'ActivityMatcher returned an error. {errors}')
            return {}

        search_data = response.get('data', {}).get('setBaseUpdate', {}).get('templatesCount', {})

        return search_data if search_data is not None else {}

    @classmethod
    def set_base_remove(cls, index_key: str, template_version: str, activity_ids: list):
        activity_ids = list(map(str, activity_ids))
        query = '''
        mutation($indexKey: String!, $templateVersion: String!, $activityIds: [String!]!){
            setBaseRemove(indexKey: $indexKey, templateVersion: $templateVersion, activityIds: $activityIds)
                {ok, templatesCount}
        }
        '''
        variables = {
            'indexKey': index_key,
            'templateVersion': template_version,
            'activityIds': activity_ids
        }
        operation = {'query': query, 'variables': variables}

        response = cls.__graph_request(operation)
        errors = response.get('errors')

        if errors is not None:
            logger.error(msg=f'ActivityMatcher returned an error. {errors}')
            return {}

        search_data = response.get('data', {}).get('setBaseUpdate', {}).get('templatesCount', {})

        return search_data if search_data is not None else {}

    @classmethod
    def search(cls,
               index_key: str,
               template_version: str,
               templates: List[str],
               nearest_count: int = 5,
               score: float = 0.0,
               far: float = 1.0,
               frr: float = 0.0) -> List[dict]:

        # TODO optimise network overhead
        query = '''
        query($indexKey: String!, $templateVersion: String!, $nearestCount: Int!, $templates: [Base64!]!,
                $score: Float!, $faR: Float! , $frR: Float!){
            search(
                indexKey: $indexKey,
                nearestCount: $nearestCount,
                templates: $templates,
                templateVersion: $templateVersion,
                score: $score,
                frR: $frR,
                faR: $faR
            ){
                template,
                searchResult{activityId, matchTemplateId, matchResult{distance, faR, frR, score}}
            }
        }
        '''
        variables = {
            'indexKey': index_key,
            'templateVersion': template_version,
            'templates': templates,
            'nearestCount': nearest_count,
            'score': score,
            'faR': far,
            'frR': frr
        }

        operation = {'query': query, 'variables': variables}
        response = cls.__graph_request(operation)
        errors = response.get('errors')

        if errors is not None:
            logger.error(msg=f'ActivityMatcher returned an error. {errors}')
            return []

        return response.get('data', {}).get('search', [])

    @classmethod
    def delete_index(cls, index_key: str, template_version: str):
        query = '''
            mutation($indexKey: String!, $templateVersion: String!)
                {
                    deleteIndex(indexKey: $indexKey, templateVersion: $templateVersion)
                        {ok}
                }'''
        variables = {'indexKey': index_key, 'templateVersion': template_version}
        operation = {'query': query, 'variables': variables}

        response = cls.__graph_request(operation)
        errors = response.get('errors')

        if errors is not None:
            logger.error(msg=f'ActivityMatcher returned an error. {errors}')
            return False

        return response.get('data', {}).get('deleteIndex', {}).get('ok', False)

    @classmethod
    def __graph_request(cls, operation: dict):
        matcher_url = settings.ACTIVITY_MATCHER_SERVICE_URL
        return requests.post(
            matcher_url + "/graphql",
            data=json.dumps(operation),
            headers={'Content-Type': 'application/json'},
            timeout=settings.ACTIVITY_MATCHER_SERVICE_TIMEOUT
        ).json()
