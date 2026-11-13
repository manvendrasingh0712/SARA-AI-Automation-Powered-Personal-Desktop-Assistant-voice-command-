"""Calendar uses the online discovery endpoint; local API schemas are unused."""

# googleapiclient's standard hook copies about 75 MB of JSON discovery
# documents for every Google API. SARA calls build("calendar", "v3", ...) with
# cache_discovery=False, so none of those local documents are runtime inputs.
datas = []
