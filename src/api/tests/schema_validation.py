"""Validate API responses against the generated OpenAPI schema."""

import re
from functools import cache

from django.urls import resolve
from drf_spectacular.generators import SchemaGenerator
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

COMPONENTS_URI = "urn:openapi-components"


def _to_json_schema(node):
    """Convert an OpenAPI 3.0 schema node to JSON Schema.

    OpenAPI 3.0 marks nullable values with `nullable: true`, which JSON Schema
    doesn't support, so nullable nodes are converted to accept null values.
    References to components are rewritten to the components resource.
    """
    if isinstance(node, list):
        return [_to_json_schema(child) for child in node]
    if not isinstance(node, dict):
        return node

    converted = {key: _to_json_schema(value) for key, value in node.items()}

    ref = converted.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/"):
        converted["$ref"] = COMPONENTS_URI + ref

    if not converted.pop("nullable", False):
        return converted

    if "enum" in converted and None not in converted["enum"]:
        converted["enum"] = [*converted["enum"], None]
    if isinstance(converted.get("type"), str):
        converted["type"] = [converted["type"], "null"]
        return converted
    return {"anyOf": [converted, {"type": "null"}]}


@cache
def _get_schema():
    """Return the OpenAPI schema, the components registry and the path matchers."""
    schema = SchemaGenerator().get_schema(request=None, public=True)

    components = _to_json_schema({"components": schema.get("components", {})})
    registry = Registry().with_resource(
        COMPONENTS_URI,
        Resource(contents=components, specification=DRAFT202012),
    )

    matchers = []
    for path, operations in schema["paths"].items():
        pattern = re.sub(r"\{[^/]+\}", "[^/]+", path.rstrip("/"))
        matchers.append(
            (path.count("{"), re.compile(f"^{pattern}/?$"), path, operations),
        )
    matchers.sort(key=lambda matcher: matcher[0])

    return registry, matchers


def _find_operation(path, method):
    """Return the schema path and operation matching a request."""
    _, matchers = _get_schema()
    for _, pattern, schema_path, operations in matchers:
        if pattern.match(path) and method in operations:
            return schema_path, operations[method]
    return None, None


def assert_response_matches_schema(testcase, response):
    """Assert that the response is documented and matches its OpenAPI schema."""
    request = response.wsgi_request
    method = request.method.lower()

    view_class = getattr(resolve(request.path).func, "view_class", None)
    if view_class is not None and not hasattr(view_class, method):
        return

    schema_path, operation = _find_operation(request.path, method)
    testcase.assertIsNotNone(
        operation,
        f"{method.upper()} {request.path} is not documented in the OpenAPI schema",
    )

    status = str(response.status_code)
    responses = operation.get("responses", {})
    documented = responses.get(status) or responses.get("default")
    testcase.assertIsNotNone(
        documented,
        f"{status} isn't documented for {method.upper()} {schema_path}",
    )

    content_schema = (
        documented.get("content", {}).get("application/json", {}).get("schema")
    )
    if content_schema is None:
        return

    registry, _ = _get_schema()
    validator = Draft202012Validator(_to_json_schema(content_schema), registry=registry)
    errors = sorted(validator.iter_errors(response.json()), key=lambda e: e.path)
    if errors:
        details = "\n".join(
            f"- {'/'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
            for error in errors[:10]
        )
        testcase.fail(
            f"{method.upper()} {schema_path} {status} response doesn't match the "
            f"OpenAPI schema:\n{details}",
        )
