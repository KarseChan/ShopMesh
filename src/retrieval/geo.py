"""Geo utilities for instant-delivery (秒送) retrieval.

Mock-only, no map API (deliberate — see reports/instant-order-agent-plan.md 七).
Distances are straight-line haversine, good enough for demo LBS constraints.
"""

import math

# Demo user location (深圳·福田中心). No geocoding: 方案明说不接地图 API,
# 就近约束用一个固定 demo 坐标,mock 门店围绕它分布。verify 脚本可用参数覆盖。
DEMO_USER_LOCATION = {"lat": 22.5431, "lon": 114.0579}

_EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two (lat, lon) points, in kilometers."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2)
    return _EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))
