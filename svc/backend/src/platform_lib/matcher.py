from typing import List, Dict


class SingletonMeta(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            instance = super().__call__(*args, **kwargs)
            cls._instances[cls] = instance
        return cls._instances[cls]


class MatcherAdapter(metaclass=SingletonMeta):
    """Stub matcher client — arbiter / matcher services are not part of open-core."""

    def init_index(self, index_id, template_version, kind):
        return None

    def add_vectors_to_indexes(
        self,
        index_ids: List[str],
        vector_ids: List[str],
        vectors: List[bytes],
        old_state: Dict[str, str],
        new_state: str
    ):
        return None

    def delete_vectors_from_indexes(
        self,
        index_ids: List[str],
        vector_ids: List[str],
        old_state: Dict[str, str],
        new_state: str
    ):
        return None

    @staticmethod
    def to_arbiter_format(vector_ids, vectors):
        return [{"id": vector_id, "blob": None} for vector_id in vector_ids]

    def delete_index(self, index_id):
        return None

    def search_index(self, index_id, blobs, knn):
        return {"matches": [[] for _ in blobs]}

    def init_workspace(self, workspace_id: str, template_version: str):
        return self.init_index(workspace_id, template_version, 'workspace')

    def init_watchlist(self, watchlist_id: str, template_version: str):
        return self.init_index(watchlist_id, template_version, 'watchlist')
