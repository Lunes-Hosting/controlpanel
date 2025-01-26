from flask_caching import Cache
import os

# Try to use Redis if available, otherwise fall back to simple cache
REDIS_URL = os.environ.get('REDIS_URL')
if REDIS_URL:
    cache_config = {
        'CACHE_TYPE': 'redis',
        'CACHE_REDIS_URL': REDIS_URL,
        'CACHE_DEFAULT_TIMEOUT': 300
    }
else:
    cache_config = {
        'CACHE_TYPE': 'simple',
        'CACHE_DEFAULT_TIMEOUT': 300
    }

cache = Cache(config=cache_config)