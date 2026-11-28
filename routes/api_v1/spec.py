"""The generation API's OpenAPI 3.1 description (GET /api/v1/openapi.json; docs/API.md), for people and for agents
that read a spec. Kept by hand next to the routes (routes/api_v1/__init__.py): change both together."""


def _ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


def _json(schema, description="OK"):
    return {"description": description, "content": {"application/json": {"schema": schema}}}


ERRORS = {
    "400": _json(_ref("Error"), "The request is wrong (error.code says how)"),
    "401": _json(_ref("Error"), "No key, or a wrong, revoked or expired one"),
    "403": _json(_ref("Error"), "The key may not do this (scope_missing, destination_not_allowed)"),
    "404": _json(_ref("Error"), "Not found for this key"),
    "429": _json(_ref("Error"), "Over a limit (rate_limited, too_many_running, quota_exceeded): see Retry-After"),
}


def spec(base):
    gen_path = {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
    wait = {"name": "wait", "in": "query", "schema": {"type": "number", "minimum": 0, "maximum": 60},
            "description": "Hold the answer up to this many seconds while the generation is still going."}
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "screen-machine generation API",
            "version": "1",
            "description": (
                "Make pictures and videos with this deployment's generation workflows. Every call but this spec and "
                "the index needs an API key (Settings › Apps & API keys), sent as `Authorization: Bearer smk_…`. "
                "A generation is asynchronous: POST answers 202 with its id, then GET it (with `?wait=` to long-poll) "
                "until `status` is succeeded, failed or cancelled. Send an `Idempotency-Key` header to make retries "
                "safe. Errors are `{\"error\": {\"code\", \"message\"}}`."),
        },
        "servers": [{"url": f"{base}/api/v1"}],
        "security": [{"key": []}],
        "paths": {
            "/me": {"get": {"summary": "The calling key: its scopes, screens, limits and use",
                            "operationId": "getMe", "responses": {"200": _json(_ref("Key")), **ERRORS}}},
            "/workflows": {"get": {
                "summary": "The workflows this key may use, with their parameters", "operationId": "listWorkflows",
                "responses": {"200": _json({"type": "object", "properties": {"data": {"type": "array", "items": _ref("Workflow")}}}),
                              **ERRORS}}},
            "/refiners": {"get": {
                "summary": "The prompt refiners (an LLM rewrites the prompt for the workflow first)",
                "operationId": "listRefiners",
                "responses": {"200": _json({"type": "object", "properties": {"data": {"type": "array", "items": {
                    "type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}}}}}}), **ERRORS}}},
            "/destinations": {"get": {
                "summary": "The screens this key may publish to (empty without the publish scope)",
                "operationId": "listDestinations",
                "responses": {"200": _json({"type": "object", "properties": {"data": {"type": "array", "items": {
                    "type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}}}}}}), **ERRORS}}},
            "/generations": {
                "post": {
                    "summary": "Start a generation", "operationId": "createGeneration",
                    "parameters": [{"name": "Idempotency-Key", "in": "header", "schema": {"type": "string", "maxLength": 200},
                                    "description": "The same key and body again returns the first generation."}],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": _ref("GenerationRequest")}}},
                    "responses": {"202": _json(_ref("Generation"), "Started (Location: the generation)"),
                                  "200": _json(_ref("Generation"), "Finished within `wait`, or an idempotent replay"),
                                  "409": _json(_ref("Error"), "The Idempotency-Key was used for another body"),
                                  "503": _json(_ref("Error"), "Generation is off in this deployment"), **ERRORS}},
                "get": {
                    "summary": "This key's generations, newest first", "operationId": "listGenerations",
                    "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100}},
                                   {"name": "before", "in": "query", "schema": {"type": "number"},
                                    "description": "next_before from the previous page"}],
                    "responses": {"200": _json({"type": "object", "properties": {
                        "data": {"type": "array", "items": _ref("Generation")},
                        "next_before": {"type": ["number", "null"]}}}), **ERRORS}},
            },
            "/generations/{id}": {
                "get": {"summary": "A generation's state and outputs", "operationId": "getGeneration",
                        "parameters": [gen_path, wait], "responses": {"200": _json(_ref("Generation")), **ERRORS}},
                "delete": {"summary": "Cancel a generation", "operationId": "cancelGeneration",
                           "parameters": [gen_path], "responses": {"200": _json(_ref("Generation")), **ERRORS}},
            },
            "/generations/{id}/outputs/{index}": {"get": {
                "summary": "An output's file (JPEG or MP4)", "operationId": "getOutput",
                "parameters": [gen_path, {"name": "index", "in": "path", "required": True, "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "The file", "content": {"image/jpeg": {}, "video/mp4": {}}}, **ERRORS}}},
        },
        "components": {
            "securitySchemes": {"key": {"type": "http", "scheme": "bearer", "bearerFormat": "smk_<id>_<secret>"}},
            "schemas": {
                "Error": {"type": "object", "properties": {"error": {"type": "object", "properties": {
                    "code": {"type": "string"}, "message": {"type": "string"}}}}},
                "Image": {
                    "description": "A picture to work from: base64 (`data`, or a data: URI), a public web address "
                                   "(`url`; LAN addresses are refused, redirects are not followed), or an output of "
                                   "one of this key's generations. A bare string is a url or base64.",
                    "oneOf": [
                        {"type": "object", "required": ["data"], "properties": {"data": {"type": "string"}, "name": {"type": "string"}}},
                        {"type": "object", "required": ["url"], "properties": {"url": {"type": "string", "format": "uri"}}},
                        {"type": "object", "required": ["generation"], "properties": {
                            "generation": {"type": "string"}, "output": {"type": "integer", "default": 0}}},
                        {"type": "string"}]},
                "GenerationRequest": {"type": "object", "additionalProperties": False, "properties": {
                    "prompt": {"type": "string", "maxLength": 4000, "description": "What to make (or how to change the images)"},
                    "workflow": {"type": "string", "default": "auto",
                                 "description": "A workflow id from /workflows, or auto (the router picks from the prompt and images)"},
                    "refiner": {"type": "string", "default": "auto",
                                "description": "A refiner id from /refiners, none to send the prompt as it is, or auto"},
                    "n": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1},
                    "params": {"type": "object", "description": "The workflow's own parameters (see /workflows: shape, "
                               "width, height, seed, video_seconds …), checked against it"},
                    "images": {"type": "array", "maxItems": 4, "items": _ref("Image")},
                    "publish_to": {"type": "string", "description": "Also put the result on this screen (needs the "
                                   "publish scope and the screen on the key)"},
                    "wait": {"type": "number", "minimum": 0, "maximum": 60, "default": 0,
                             "description": "Hold the answer up to this many seconds for the result"}}},
                "Output": {"type": "object", "properties": {
                    "index": {"type": "integer"}, "kind": {"enum": ["image", "video"]},
                    "url": {"type": "string", "description": "The file on this server (open to anyone with the address)"},
                    "download_url": {"type": "string", "description": "The file, with this key"},
                    "seed": {}, "prompt": {"type": "string", "description": "The prompt the model got"},
                    "workflow": {"type": "string"}, "seconds": {"type": ["number", "null"]},
                    "cost_gbp": {"type": ["number", "null"]}}},
                "Generation": {"type": "object", "properties": {
                    "id": {"type": "string"}, "object": {"const": "generation"},
                    "status": {"enum": ["queued", "running", "succeeded", "failed", "cancelled"]},
                    "created_at": {"type": "number"}, "finished_at": {"type": ["number", "null"]},
                    "prompt": {"type": "string"}, "workflow": {"type": "string"}, "n": {"type": "integer"},
                    "publish_to": {"type": ["string", "null"]},
                    "outputs": {"type": "array", "items": _ref("Output")},
                    "progress": {"type": "array", "items": {"type": "object"},
                                 "description": "While going: each job's status, stage and percent"},
                    "error": {"type": "object"}, "links": {"type": "object"}}},
                "Workflow": {"type": "object", "properties": {
                    "id": {"type": "string"}, "name": {"type": "string"}, "summary": {"type": ["string", "null"]},
                    "output": {"enum": ["image", "video"]}, "images": {"type": "integer", "description": "Images it needs"},
                    "prompt": {"type": "boolean"}, "default": {"type": "boolean"},
                    "params": {"type": "array", "items": {"type": "object"}}}},
                "Key": {"type": "object", "properties": {
                    "id": {"type": "string"}, "name": {"type": "string"}, "hint": {"type": "string"},
                    "scopes": {"type": "array", "items": {"enum": ["generate", "publish"]}},
                    "destinations": {"type": "array", "items": {"type": "string"}},
                    "per_day": {"type": "integer", "description": "Pictures in any 24 hours (0 = no limit)"},
                    "concurrent": {"type": "integer", "description": "Generations at once (0 = no limit)"},
                    "expires_at": {"type": ["number", "null"]}, "usage": {"type": "object"}}},
            },
        },
    }
