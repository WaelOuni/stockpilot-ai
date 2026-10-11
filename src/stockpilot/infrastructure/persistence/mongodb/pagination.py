"""A single Mongo aggregation counts and pages the same filtered stream."""
def read_page(collection, query, sort, limit, offset, projection=None):
    stages = [{"$sort": dict(sort)}, {"$skip": offset}, {"$limit": limit}]
    if projection:
        stages.append({"$project": projection})
    result = next(iter(collection.aggregate([
        {"$match": query}, {"$facet": {"items": stages, "total": [{"$count": "value"}]}}
    ])), {})
    counts = result.get("total", [])
    return result.get("items", []), counts[0]["value"] if counts else 0
