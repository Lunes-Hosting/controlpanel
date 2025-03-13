from flask_caching import Cache

cache_config = {
        'CACHE_TYPE': 'simple',
        'CACHE_DEFAULT_TIMEOUT': 3000
    }

cache = Cache(config=cache_config)