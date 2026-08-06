from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

# 트래킹 파라미터 (B8 버그: URL 완전일치 중복제거 → utm 등 변형에 무력)
_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAM_NAMES = {
    "gclid", "fbclid", "ref", "ref_src", "spm", "mc_cid", "mc_eid",
    "igshid", "s", "cmpid",
}


def _is_tracking_param(key: str) -> bool:
    key_lower = key.lower()
    return key_lower in _TRACKING_PARAM_NAMES or key_lower.startswith(_TRACKING_PARAM_PREFIXES)


def normalize_url(url: str) -> str:
    """URL을 정규화해 모바일/AMP/utm 변형을 같은 값으로 수렴시킨다 (B8 대응)."""
    parts = urlsplit(url.strip())

    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[len("www."):]
    if netloc.startswith("amp."):
        netloc = netloc[len("amp."):]
    if netloc.startswith("m."):
        netloc = netloc[len("m."):]

    path = parts.path
    if path.endswith("/amp"):
        path = path[: -len("/amp")]
    elif path.endswith("/amp/"):
        path = path[: -len("/amp/")]
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]

    kept_params = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_param(k)
    )
    query = urlencode(kept_params)

    return urlunsplit((scheme, netloc, path, query, ""))
