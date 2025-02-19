"""
Product Configuration for Lunes Hosting

This file defines all available products/plans in the system. Each product is a dictionary
with the following structure:

Required Fields:
    enabled (bool): Whether the product is currently available for purchase
    id (int): Unique identifier for the product
    name (str): Display name of the product
    price (int): Cost in credits per month
    price_link (str|None): Stripe price ID for payment processing, None if not purchasable
    
    limits (dict): Resource limits for the server
        memory (int): RAM in MB
        swap (int): Swap space in MB
        disk (int): Storage space in MB
        io (int): I/O limit (500 default)
        cpu (int): CPU limit percentage (0 for unmetered)
    
    product_limits (dict): Additional feature limits
        databases (int): Number of databases allowed
        backups (int): Number of backups allowed
        allocations (int): Number of port allocations allowed

Optional Fields:
    is_addon (bool): If True, product is purchasable but not available during server creation.
                    Used for credit packs and other non-server products.

Notes:
    - CPU value of 0 means unmetered (fair use policy applies)
    - Free tier (id: 0) has price_link set to None
    - Custom plans should have enabled set to False
"""

products = [
    {
        "enabled": True,
        "price_link": None,
        "id": 0,
        "name": "Free Tier",
        "price": 0,
        "limits": {
            "memory": 128,
            "swap": 0,
            "disk": 512,
            "io": 500,
            "cpu": 15
        },
        "product_limits": {
            "databases": 0,
            "backups": 0,
            "allocations": 1
        }
    },
    {
        "enabled": True,
        "price_link": None,
        "id": 1,
        "name": "Ls 0.5",
        "price": 50,
        "limits": {
            "memory": 512,
            "swap": 0,
            "disk": 3000,
            "io": 500,
            "cpu": 100
        },
        "product_limits": {
            "databases": 0,
            "backups": 0,
            "allocations": 1
        }
    },
    {
        "enabled": True,
        "price_link": "price_1NWnBkGc0pLG1vqRCNy4PJVZ",
        "id": 2,
        "name": "Ls 1",
        "price": 100,
        "limits": {
            "memory": 1024,
            "swap": 0,
            "disk": 7168,
            "io": 500,
            "cpu": 0
        },
        "product_limits": {
            "databases": 1,
            "backups": 1,
            "allocations": 1
        }
    },
    {
        "enabled": True,
        "price_link": "price_1NaWkTGc0pLG1vqRRtg2FJVm",
        "id": 3,
        "name": "Ls 2",
        "price": 200,
        "limits": {
            "memory": 2048,
            "swap": 0,
            "disk": 14336,
            "io": 500,
            "cpu": 0
        },
        "product_limits": {
            "databases": 1,
            "backups": 2,
            "allocations": 1
        }
    },
    {
        "enabled": True,
        "price_link": "price_1NaWkdGc0pLG1vqRKpp7J1CD",
        "id": 4,
        "name": "Ls 3",
        "price": 300,
        "limits": {
            "memory": 3072,
            "swap": 0,
            "disk": 21504,
            "io": 500,
            "cpu": 0
        },
        "product_limits": {
            "databases": 2,
            "backups": 2,
            "allocations": 1
        }
    },
    {
        "enabled": True,
        "price_link": "price_1NaWkhGc0pLG1vqR4HPoxo3q",
        "id": 5,
        "name": "Ls 4",
        "price": 400,
        "limits": {
            "memory": 4096,
            "swap": 0,
            "disk": 28672,
            "io": 500,
            "cpu": 0
        },
        "product_limits": {
            "databases": 2,
            "backups": 3,
            "allocations": 1
        }
    },
    {
        "enabled": True,
        "price_link": "price_1NaWklGc0pLG1vqR9hI9OVPO",
        "id": 6,
        "name": "Ls 5",
        "price": 500,
        "limits": {
            "memory": 5120,
            "swap": 0,
            "disk": 35840,
            "io": 500,
            "cpu": 0
        },
        "product_limits": {
            "databases": 2,
            "backups": 3,
            "allocations": 1
        }
    },
        {
        "enabled": True,
        "price_link": "price_1NaWkpGc0pLG1vqRMBodHszM",
        "id": 7,
        "name": "Ls 6",
        "price": 600,
        "limits": {
            "memory": 6144,
            "swap": 0,
            "disk": 43008,
            "io": 500,
            "cpu": 0
        },
        "product_limits": {
            "databases": 2,
            "backups": 3,
            "allocations": 1
        }
    },
    {
        "enabled": True,
        "price_link": "price_1NnkF6Gc0pLG1vqRSj3llmXi",
        "id": 8,
        "name": "Ls 10",
        "price": 1000,
        "limits": {
            "memory": 10240,
            "swap": 0,
            "disk": 71680,
            "io": 500,
            "cpu": 0
        },
        "product_limits": {
            "databases": 4,
            "backups": 8,
            "allocations": 2
        }
    },
    {
        "enabled": True,
        "price_link": "price_1PwZiXGc0pLG1vqRy9Gx7RlV",
        "id": 9,
        "name": "Ls 16",
        "price": 1600,
        "limits": {
            "memory": 16384,
            "swap": 0,
            "disk": 114688,
            "io": 500,
            "cpu": 0
        },
        "product_limits": {
            "databases": 4,
            "backups": 8,
            "allocations": 3
        }
    },
        {
        "enabled": False,
        "price_link": None,
        "id": 10,
        "name": "16GB custom",
        "price": 1,
        "limits": {
            "memory": 16000,
            "swap": 0,
            "disk": 12000,
            "io": 500,
            "cpu": 0
        },
        "product_limits": {
            "databases": 4,
            "backups": 8,
            "allocations": 3
        }
    },
    {
        "enabled": True,
        "price_link": "price_1Qu0qlGc0pLG1vqRqyi89rfY",
        "id": 11,
        "name": "Credits Bundle",
        "price": 2500,
        "is_addon": True,
        "limits": {
            "memory": 0,
            "swap": 0,
            "disk": 0,
            "io": 0,
            "cpu": 0
        },
        "product_limits": {
            "databases": 0,
            "backups": 0,
            "allocations": 0
        }
    }
]
