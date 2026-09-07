"""
This class allows to cache values in memory instead reading from database.
"""

import random


def get_ttl(ttl: int, jitter: int) -> int:
    return ttl - int(jitter / 2) + random.randint(0, jitter)
