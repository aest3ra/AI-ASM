"""Group raw captures into deduplicated endpoints with parameter catalogs."""

from __future__ import annotations

from urllib.parse import urlparse

from ai_asm.crawler.types import CapturedRequest
from ai_asm.normalizer.params import extract_all, infer_type
from ai_asm.normalizer.types import NormalizedEndpoint, NormalizedParameter
from ai_asm.normalizer.url import templatize_path

MAX_SAMPLES_PER_PARAM = 5


def normalize(captures: list[CapturedRequest]) -> list[NormalizedEndpoint]:
    """Group `captures` by (method, host, path_template) and accumulate params."""
    by_key: dict[tuple[str, str, str], NormalizedEndpoint] = {}

    for req in captures:
        parsed = urlparse(req.url)
        host = parsed.hostname or ""
        path_template = templatize_path(parsed.path or "/")
        key = (req.method, host, path_template)

        ep = by_key.get(key)
        if ep is None:
            ep = NormalizedEndpoint(
                method=req.method,
                host=host,
                path_template=path_template,
                sample_url=req.url,
            )
            by_key[key] = ep
        ep.seen_count += 1
        ep.resource_types.add(req.resource_type)

        for location, name, value in extract_all(req):
            pkey = (location, name)
            param = ep.parameters.get(pkey)
            if param is None:
                param = NormalizedParameter(
                    location=location, name=name, type_inferred=infer_type(value),
                )
                ep.parameters[pkey] = param
            param.seen_count += 1
            if value not in param.sample_values and len(param.sample_values) < MAX_SAMPLES_PER_PARAM:
                param.sample_values.append(value)

    return list(by_key.values())
