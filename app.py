import csv
import io
import json
import os
import re
import time
from typing import Any, Dict, List, Tuple

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

load_dotenv()

app = Flask(__name__)

ALLOWED_RCM_ATTRIBUTES = {"group", "hostGroup", "hostProperty", "hostTag", "networkZone"}


def get_config() -> Dict[str, str]:
    env = (os.getenv("DT_ENV") or "").strip().rstrip("/")
    token = (os.getenv("DT_TOKEN") or "").strip()
    return {"dt_env": env, "dt_token": token}


def mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 12:
        return token[:3] + "..." + token[-2:]
    return token[:7] + "..." + token[-6:]


def api_base(dt_env: str) -> str:
    return dt_env.rstrip("/") + "/api/v2"


def dynatrace_headers(token: str) -> Dict[str, str]:
    return {
        "accept": "application/json; charset=utf-8",
        "Authorization": f"Api-Token {token}",
        "Content-Type": "application/json; charset=utf-8",
    }


def require_dt_config() -> Tuple[Dict[str, str], Any]:
    cfg = get_config()
    if not cfg["dt_env"] or not cfg["dt_token"]:
        return cfg, (jsonify({"ok": False, "errors": ["DT_ENV and DT_TOKEN must be configured in .env."]}), 400)
    return cfg, None


def dt_request(method: str, path: str, *, json_body=None, params=None, timeout=60):
    cfg, error = require_dt_config()
    if error:
        return None, error

    url = api_base(cfg["dt_env"]) + path
    try:
        resp = requests.request(
            method,
            url,
            headers=dynatrace_headers(cfg["dt_token"]),
            json=json_body,
            params=params or {},
            timeout=timeout,
        )
        try:
            body = resp.json() if resp.content else None
        except Exception:
            body = resp.text
        return {
            "ok": 200 <= resp.status_code < 300,
            "endpoint": url,
            "params": params or {},
            "statusCode": resp.status_code,
            "response": body,
        }, None
    except requests.RequestException as exc:
        return {
            "ok": False,
            "endpoint": url,
            "params": params or {},
            "statusCode": None,
            "response": str(exc),
        }, None


def ndjson_event(event_type: str, message: str = "", **extra) -> str:
    """Serialize one live-progress event for the browser."""
    payload = {
        "type": event_type,
        "message": message,
        "ts": time.strftime("%H:%M:%S"),
    }
    payload.update(extra)
    return json.dumps(payload, default=str) + "\n"


def summarize_remote_results(results: List[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "rows": len(results),
        "operations": sum(int(r.get("operationCount", 0)) for r in results),
        "successfulRows": sum(1 for r in results if r.get("success")),
        "failedRows": sum(1 for r in results if not r.get("success")),
        "totalWarnings": sum(len(r.get("warnings", [])) for r in results),
    }


def split_values(cell: str) -> List[str]:
    """
    Parse a CSV cell like:
      keyOne=value,keyTwo=value
    into:
      ["keyOne=value", "keyTwo=value"]

    Warns if any split part looks like it may have been an embedded comma
    inside a value (i.e. the part has no '=' and is not a bare tag).
    The cell is already unquoted by DictReader; commas here are always
    treated as separators between key=value pairs.
    """
    if cell is None:
        return []
    raw = str(cell).strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def detect_split_value_warnings(cell: str, values: List[str]) -> List[str]:
    """
    Return warning strings for parts that look suspicious after splitting —
    specifically a part with no '=' sign following a part that had one,
    which is a strong signal that a value contained an embedded comma.
    """
    warnings = []
    prev_had_eq = False
    for part in values:
        has_eq = "=" in part
        if prev_had_eq and not has_eq:
            warnings.append(
                f"Value part '{part}' has no '=' and may be the tail of an embedded comma "
                f"in the previous value. Wrap cell values containing commas in quotes in your CSV."
            )
        prev_had_eq = has_eq
    return warnings


def parse_exception_keys(raw: str) -> set:
    if not raw:
        return set()
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def replace_whitespace(value: str) -> str:
    return re.sub(r"\s+", "_", value.strip())


def transform_key_value(value: str, *, lowercase: bool, whitespace_to_underscore: bool, exception_keys: set) -> str:
    """
    Applies UI-selected normalization to key=value strings.

    - lowercase=True lowercases key and value
    - whitespace_to_underscore=True replaces whitespace with underscores in key and value
    - exception_keys preserves the VALUE for matching keys, based on the original key

    The key is still normalized so CSV output stays consistent. Only the value is preserved
    when the key is listed as an exception.
    """
    if "=" not in value:
        result = value
        if whitespace_to_underscore:
            result = replace_whitespace(result)
        if lowercase:
            result = result.lower()
        return result

    raw_key, raw_val = value.split("=", 1)
    original_key_lookup = raw_key.strip().lower()
    key = raw_key.strip()
    val = raw_val.strip()

    if whitespace_to_underscore:
        key = replace_whitespace(key)
    if lowercase:
        key = key.lower()

    preserve_value = original_key_lookup in exception_keys
    if not preserve_value:
        if whitespace_to_underscore:
            val = replace_whitespace(val)
        if lowercase:
            val = val.lower()

    return f"{key}={val}"


def parse_bool(value: str, default: bool = False) -> bool:
    """
    Parse a truthy string. Default is False — callers must opt in explicitly.
    Accepts: '1', 'true', 'yes', 'y', 'on' (case-insensitive).
    """
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_rconfig_csv(
    file_storage,
    operation_mode: str = "set",
    *,
    lowercase_key_values: bool = False,
    whitespace_to_underscore: bool = False,
    exception_keys: set | None = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    operation_mode = (operation_mode or "set").strip().lower()
    if operation_mode not in {"set", "clear"}:
        return [], ["operation must be either set or clear."]
    exception_keys = exception_keys or set()

    raw = file_storage.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw), skipinitialspace=True)

    if not reader.fieldnames:
        return [], ["CSV has no header row."]

    headers = [h.strip() for h in reader.fieldnames if h and h.strip()]
    required = {"entityName", "entityID"}
    missing = sorted(required - set(headers))
    if missing:
        return [], [f"Missing required column(s): {', '.join(missing)}"]

    attribute_headers = [h for h in headers if h not in {"entityName", "entityID"}]
    invalid_attrs = [h for h in attribute_headers if h not in ALLOWED_RCM_ATTRIBUTES]
    if invalid_attrs:
        return [], [
            "Unsupported attribute column(s): "
            + ", ".join(invalid_attrs)
            + ". Allowed: "
            + ", ".join(sorted(ALLOWED_RCM_ATTRIBUTES))
        ]

    parsed_rows = []
    errors = []

    for idx, row in enumerate(reader, start=2):
        entity_name = (row.get("entityName") or "").strip()
        entity_id = (row.get("entityID") or "").strip()

        original_csv_row = {h: (row.get(h) or "").strip() for h in headers}
        final_csv_row = {"entityName": entity_name, "entityID": entity_id}

        if not entity_id:
            errors.append(f"Row {idx}: entityID is required.")
            continue

        operations = []
        changes = []
        row_warnings = []

        for attr in attribute_headers:
            original_cell = (row.get(attr, "") or "").strip()
            original_values = split_values(original_cell)
            values = list(original_values)

            # Warn about likely embedded-comma splits for set operations
            if operation_mode == "set" and values:
                row_warnings.extend(
                    f"Row {idx}, {attr}: {w}"
                    for w in detect_split_value_warnings(original_cell, values)
                )

            if operation_mode == "set":
                values = [
                    transform_key_value(
                        value,
                        lowercase=lowercase_key_values,
                        whitespace_to_underscore=whitespace_to_underscore,
                        exception_keys=exception_keys,
                    )
                    for value in values
                ]

            final_cell = ",".join(values) if values else ""
            final_csv_row[attr] = final_cell
            changes.append({
                "attribute": attr,
                "originalCell": original_cell,
                "finalCell": final_cell,
                "changed": original_cell != final_cell,
            })

            # clear with a blank cell → one bare clear op for the whole attribute
            if operation_mode == "clear" and not values:
                operations.append({
                    "attribute": attr,
                    "operation": "clear",
                })
                continue

            for value in values:
                op = {
                    "attribute": attr,
                    "operation": operation_mode,
                }
                if value:
                    op["value"] = value
                operations.append(op)

        # For set mode, a row with no operations means all cells were blank → error.
        # For clear mode, bare clear ops are always added so this only fires if
        # there are no attribute columns at all (caught at header validation).
        if not operations:
            if operation_mode == "set":
                errors.append(
                    f"Row {idx}: no values found. "
                    "For 'set' operations every attribute cell must have at least one value. "
                    "Use 'clear' if you intend to remove values."
                )
            else:
                errors.append(f"Row {idx}: no operation values found.")
            continue

        parsed_rows.append({
            "row": idx,
            "entityName": entity_name,
            "entityID": entity_id,
            "originalCsvRow": original_csv_row,
            "finalCsvRow": final_csv_row,
            "changes": changes,
            "warnings": row_warnings,
            "operations": operations,
            "payload": {
                "entities": [entity_id],
                "operations": operations,
            },
        })

    return parsed_rows, errors


def rows_from_uploaded_csv():
    uploaded = request.files.get("file")
    if not uploaded:
        return None, ["No CSV file uploaded."]
    operation_mode = (request.form.get("operation") or "set").strip().lower()
    # parse_bool defaults to False — normalization options are always opt-in
    lowercase_key_values = parse_bool(request.form.get("lowercaseKeyValues"), default=False)
    whitespace_to_underscore = parse_bool(request.form.get("whitespaceToUnderscore"), default=False)
    exception_keys = parse_exception_keys(request.form.get("lowercaseExceptionKeys") or "")
    return parse_rconfig_csv(
        uploaded,
        operation_mode=operation_mode,
        lowercase_key_values=lowercase_key_values,
        whitespace_to_underscore=whitespace_to_underscore,
        exception_keys=exception_keys,
    )


def run_payload_endpoint(rows: List[Dict[str, Any]], endpoint_path: str, *, params=None):
    results = []
    for item in rows:
        data, error = dt_request("POST", endpoint_path, json_body=item["payload"], params=params or {})
        if error:
            return error
        results.append({
            "row": item["row"],
            "entityName": item["entityName"],
            "entityID": item["entityID"],
            "operationCount": len(item["operations"]),
            "warnings": item.get("warnings", []),
            "success": data["ok"],
            "statusCode": data["statusCode"],
            "payload": item["payload"],
            "response": data["response"],
        })
    return jsonify({
        "ok": all(r["success"] for r in results),
        "endpointPath": endpoint_path,
        "results": results,
        "summary": {
            "rows": len(rows),
            "operations": sum(len(r["operations"]) for r in rows),
            "successfulRows": sum(1 for r in results if r["success"]),
            "failedRows": sum(1 for r in results if not r["success"]),
            "totalWarnings": sum(len(r.get("warnings", [])) for r in results),
        },
    })


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config")
def config():
    cfg = get_config()
    return jsonify({
        "dt_env": cfg["dt_env"],
        "api_base": api_base(cfg["dt_env"]) if cfg["dt_env"] else "",
        "token_masked": mask_token(cfg["dt_token"]),
        "auth_header_type": "Api-Token",
        "configured": bool(cfg["dt_env"] and cfg["dt_token"]),
        "allowed_remote_config_attributes": sorted(ALLOWED_RCM_ATTRIBUTES),
        "remote_config_endpoints": {
            "createJob": "POST /api/v2/oneagents/remoteConfigurationManagement?restart=true|false",
            "validate": "POST /api/v2/oneagents/remoteConfigurationManagement/validator",
            "preview": "POST /api/v2/oneagents/remoteConfigurationManagement/preview",
            "listFinished": "GET /api/v2/oneagents/remoteConfigurationManagement",
            "current": "GET /api/v2/oneagents/remoteConfigurationManagement/current",
            "getJob": "GET /api/v2/oneagents/remoteConfigurationManagement/{id}",
        },
    })


@app.route("/api/remote-config/build-payload-csv", methods=["POST"])
def build_remote_config_csv():
    rows, errors = rows_from_uploaded_csv()
    if rows is None:
        return jsonify({"ok": False, "errors": errors}), 400
    all_warnings = [w for r in rows for w in r.get("warnings", [])]
    return jsonify({
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": all_warnings,
        "rows": rows,
        "row_count": len(rows),
        "operation_count": sum(len(r["operations"]) for r in rows),
    })


@app.route("/api/remote-config/validate-csv", methods=["POST"])
def validate_remote_config_csv():
    rows, errors = rows_from_uploaded_csv()
    if rows is None:
        return jsonify({"ok": False, "errors": errors}), 400
    if errors:
        return jsonify({"ok": False, "errors": errors, "rows": rows}), 400
    return run_payload_endpoint(rows, "/oneagents/remoteConfigurationManagement/validator")


@app.route("/api/remote-config/preview-csv", methods=["POST"])
def preview_remote_config_csv():
    rows, errors = rows_from_uploaded_csv()
    if rows is None:
        return jsonify({"ok": False, "errors": errors}), 400
    if errors:
        return jsonify({"ok": False, "errors": errors, "rows": rows}), 400
    return run_payload_endpoint(rows, "/oneagents/remoteConfigurationManagement/preview")


@app.route("/api/remote-config/run-csv", methods=["POST"])
def run_remote_config_csv():
    rows, errors = rows_from_uploaded_csv()
    if rows is None:
        return jsonify({"ok": False, "errors": errors}), 400
    if errors:
        return jsonify({"ok": False, "errors": errors, "rows": rows}), 400

    restart = parse_bool(request.form.get("restart"), default=False)
    return run_payload_endpoint(
        rows,
        "/oneagents/remoteConfigurationManagement",
        params={"restart": "true" if restart else "false"},
    )



@app.route("/api/remote-config/run-csv-stream", methods=["POST"])
def run_remote_config_csv_stream():
    """
    Create OneAgent remote configuration jobs one CSV row at a time.

    Dynatrace allows only one remote configuration management job at a time.
    This endpoint streams progress as NDJSON, waits for the current job to finish,
    creates the next row's job, then waits for that job to complete before moving on.
    """
    rows, errors = rows_from_uploaded_csv()
    if rows is None:
        return jsonify({"ok": False, "errors": errors}), 400
    if errors:
        return jsonify({"ok": False, "errors": errors, "rows": rows}), 400

    restart = parse_bool(request.form.get("restart"), default=False)
    poll_seconds = 5
    max_wait_seconds = 30 * 60

    def stream():
        results: List[Dict[str, Any]] = []
        yield ndjson_event(
            "start",
            f"Starting sequential remote config run for {len(rows)} row(s). Only one Dynatrace job will run at a time.",
            totalRows=len(rows),
            pollSeconds=poll_seconds,
        )

        def wait_until_idle(context: str) -> bool:
            waited = 0
            while True:
                data, error = dt_request("GET", "/oneagents/remoteConfigurationManagement/current", timeout=30)
                if error:
                    yield ndjson_event("error", "Dynatrace configuration error while checking current job.")
                    return False

                status = data.get("statusCode")
                yield ndjson_event(
                    "api",
                    f"{context}: checked current running job.",
                    method="GET",
                    path="/api/v2/oneagents/remoteConfigurationManagement/current",
                    statusCode=status,
                    ok=data.get("ok"),
                    response=data.get("response"),
                )

                if status == 204:
                    yield ndjson_event("idle", f"{context}: no running remote config job found. Continuing.")
                    return True

                if waited >= max_wait_seconds:
                    yield ndjson_event("error", f"Timed out after {max_wait_seconds} seconds waiting for current job to finish.")
                    return False

                yield ndjson_event("wait", f"{context}: another job is still running. Waiting {poll_seconds}s before re-checking.")
                time.sleep(poll_seconds)
                waited += poll_seconds

        # Prime the tenant: wait until there is no running job before starting row 1.
        for event in wait_until_idle("Before first row"):
            yield event
            try:
                parsed = json.loads(event)
                if parsed.get("type") == "error":
                    yield ndjson_event("done", "Stopped before creating any jobs.", ok=False, results=results, summary=summarize_remote_results(results))
                    return
            except Exception:
                pass

        for idx, item in enumerate(rows, start=1):
            row_label = f"Row {item['row']} ({item['entityID']})"
            yield ndjson_event(
                "row_start",
                f"Starting {row_label}: {len(item['operations'])} operation(s).",
                row=item["row"],
                entityID=item["entityID"],
                entityName=item.get("entityName"),
                operationCount=len(item["operations"]),
                payload=item["payload"],
            )

            # Handle races: if someone else started a job since our last check, wait again.
            create_data = None
            attempt = 0
            while True:
                attempt += 1
                yield ndjson_event(
                    "api_start",
                    f"{row_label}: creating remote config job, attempt {attempt}.",
                    method="POST",
                    path="/api/v2/oneagents/remoteConfigurationManagement",
                    params={"restart": "true" if restart else "false"},
                )
                create_data, error = dt_request(
                    "POST",
                    "/oneagents/remoteConfigurationManagement",
                    json_body=item["payload"],
                    params={"restart": "true" if restart else "false"},
                    timeout=60,
                )
                if error:
                    result = {
                        "row": item["row"],
                        "entityName": item["entityName"],
                        "entityID": item["entityID"],
                        "operationCount": len(item["operations"]),
                        "warnings": item.get("warnings", []),
                        "success": False,
                        "statusCode": None,
                        "payload": item["payload"],
                        "response": "DT_ENV and DT_TOKEN must be configured in .env.",
                        "changes": item.get("changes", []),
                    }
                    results.append(result)
                    yield ndjson_event("row_result", f"{row_label}: failed before request due to missing config.", result=result)
                    break

                yield ndjson_event(
                    "api",
                    f"{row_label}: create job HTTP {create_data.get('statusCode')}.",
                    method="POST",
                    path="/api/v2/oneagents/remoteConfigurationManagement",
                    statusCode=create_data.get("statusCode"),
                    ok=create_data.get("ok"),
                    response=create_data.get("response"),
                )

                if create_data.get("statusCode") == 409:
                    yield ndjson_event("wait", f"{row_label}: Dynatrace returned 409 because another job is running. Waiting before retry.")
                    for event in wait_until_idle(f"{row_label} retry wait"):
                        yield event
                    continue

                result = {
                    "row": item["row"],
                    "entityName": item["entityName"],
                    "entityID": item["entityID"],
                    "operationCount": len(item["operations"]),
                    "warnings": item.get("warnings", []),
                    "success": bool(create_data.get("ok")),
                    "statusCode": create_data.get("statusCode"),
                    "payload": item["payload"],
                    "response": create_data.get("response"),
                    "changes": item.get("changes", []),
                }
                results.append(result)
                yield ndjson_event("row_result", f"{row_label}: create job completed with HTTP {result['statusCode']}.", result=result)
                break

            if not create_data or not create_data.get("ok"):
                yield ndjson_event("row_done", f"{row_label}: not waiting for completion because create job failed.")
                continue

            yield ndjson_event("wait", f"{row_label}: job accepted. Waiting for it to finish before moving to the next row.")
            for event in wait_until_idle(f"After {row_label}"):
                yield event
                try:
                    parsed = json.loads(event)
                    if parsed.get("type") == "error":
                        yield ndjson_event("done", "Stopped while waiting for job completion.", ok=False, results=results, summary=summarize_remote_results(results))
                        return
                except Exception:
                    pass
            yield ndjson_event("row_done", f"{row_label}: job finished; next row may start.")

        summary = summarize_remote_results(results)
        yield ndjson_event(
            "done",
            f"Sequential remote config run finished. {summary['successfulRows']}/{summary['rows']} row(s) created successfully.",
            ok=all(r.get("success") for r in results) if results else False,
            endpointPath="/oneagents/remoteConfigurationManagement",
            results=results,
            summary=summary,
        )

    return Response(stream_with_context(stream()), mimetype="application/x-ndjson")


@app.route("/api/remote-config/jobs", methods=["GET"])
def list_remote_config_jobs():
    params = {}
    from_value = (request.args.get("from") or "").strip()
    to_value = (request.args.get("to") or "").strip()
    if from_value:
        params["from"] = from_value
    if to_value:
        params["to"] = to_value
    data, error = dt_request("GET", "/oneagents/remoteConfigurationManagement", params=params)
    if error:
        return error
    return jsonify(data)


@app.route("/api/remote-config/current", methods=["GET"])
def current_remote_config_job():
    data, error = dt_request("GET", "/oneagents/remoteConfigurationManagement/current")
    if error:
        return error
    return jsonify(data)


@app.route("/api/remote-config/jobs/<job_id>", methods=["GET"])
def get_remote_config_job(job_id):
    data, error = dt_request("GET", f"/oneagents/remoteConfigurationManagement/{job_id}")
    if error:
        return error
    return jsonify(data)


# ─── Tag cleanup ─────────────────────────────────────────────────────────────

def parse_tag_csv(file_storage) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Parse a tag cleanup / add CSV.

    Expected columns (case-insensitive):
      hostname, id, tagstodelete   — for DELETE operations
      hostname, id, tagstoadd      — for POST operations (key or key=value)
      hostname, id, tagstodelete, tagstoadd — both in one CSV

    Tags cells are comma-separated inside the quoted cell:
      "key1,key2,key=value"

    Each tag string may be:
      - a bare key:    "mykey"      → DELETE by key, or POST {key: "mykey"}
      - key=value:     "env=prod"   → DELETE key+value, or POST {key:"env",value:"prod"}
    """
    raw = file_storage.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw), skipinitialspace=True)

    if not reader.fieldnames:
        return [], ["CSV has no header row."]

    # Normalise header names to lowercase for matching, keep a map to originals
    headers_lower = {h.strip().lower(): h.strip() for h in reader.fieldnames if h and h.strip()}

    required = {"hostname", "id"}
    missing = sorted(required - set(headers_lower))
    if missing:
        return [], [f"Missing required column(s): {', '.join(missing)}"]

    has_delete = "tagstodelete" in headers_lower
    has_add = "tagstoadd" in headers_lower
    if not has_delete and not has_add:
        return [], ["CSV must have at least one of: tagstodelete, tagstoadd"]

    def parse_tag_cell(cell: str) -> List[Dict[str, str]]:
        """Split comma-separated tag strings into {key, value?} dicts."""
        tags = []
        for part in [p.strip() for p in cell.split(",") if p.strip()]:
            if "=" in part:
                k, v = part.split("=", 1)
                tags.append({"key": k.strip(), "value": v.strip()})
            else:
                tags.append({"key": part})
        return tags

    parsed_rows = []
    errors = []

    for idx, row in enumerate(reader, start=2):
        # Normalise row keys to lowercase for lookup
        nrow = {k.strip().lower(): (v or "").strip() for k, v in row.items()}

        hostname = nrow.get("hostname", "")
        entity_id = nrow.get("id", "")

        if not entity_id:
            errors.append(f"Row {idx}: 'id' is required.")
            continue

        delete_tags = parse_tag_cell(nrow.get("tagstodelete", "")) if has_delete else []
        add_tags = parse_tag_cell(nrow.get("tagstoadd", "")) if has_add else []

        if not delete_tags and not add_tags:
            errors.append(f"Row {idx} ({hostname or entity_id}): no tags found in tagstodelete or tagstoadd.")
            continue

        parsed_rows.append({
            "row": idx,
            "hostname": hostname,
            "entityID": entity_id,
            "deleteTags": delete_tags,
            "addTags": add_tags,
        })

    return parsed_rows, errors


@app.route("/api/tags/preview-csv", methods=["POST"])
def preview_tag_csv():
    """Parse and return rows without calling Dynatrace."""
    uploaded = request.files.get("file")
    if not uploaded:
        return jsonify({"ok": False, "errors": ["No CSV file uploaded."]}), 400

    rows, errors = parse_tag_csv(uploaded)
    return jsonify({
        "ok": len(errors) == 0,
        "errors": errors,
        "rows": rows,
        "row_count": len(rows),
        "delete_count": sum(len(r["deleteTags"]) for r in rows),
        "add_count": sum(len(r["addTags"]) for r in rows),
    })


@app.route("/api/tags/run-csv", methods=["POST"])
def run_tag_csv():
    """Parse CSV and execute DELETE and/or POST tag operations against Dynatrace."""
    uploaded = request.files.get("file")
    if not uploaded:
        return jsonify({"ok": False, "errors": ["No CSV file uploaded."]}), 400

    rows, errors = parse_tag_csv(uploaded)
    if not rows:
        return jsonify({"ok": False, "errors": errors}), 400

    delete_all_with_key = parse_bool(request.form.get("deleteAllWithKey"), default=True)
    results = []

    for item in rows:
        entity_selector = f"entityId({item['entityID']})"
        row_ops = []

        # DELETE operations — one API call per tag key
        for tag in item["deleteTags"]:
            params: Dict[str, str] = {
                "key": tag["key"],
                "entitySelector": entity_selector,
                "deleteAllWithKey": "true" if delete_all_with_key else "false",
            }
            if "value" in tag and not delete_all_with_key:
                params["value"] = tag["value"]

            data, error = dt_request("DELETE", "/tags", params=params)
            if error:
                return error

            row_ops.append({
                "operation": "DELETE",
                "tag": tag,
                "params": params,
                "success": data["ok"],
                "statusCode": data["statusCode"],
                "response": data["response"],
            })

        # POST operations — batch all add-tags in one call per entity
        if item["addTags"]:
            payload = {"tags": item["addTags"]}
            params_post = {"entitySelector": entity_selector}
            data, error = dt_request("POST", "/tags", json_body=payload, params=params_post)
            if error:
                return error

            row_ops.append({
                "operation": "POST",
                "tags": item["addTags"],
                "params": params_post,
                "success": data["ok"],
                "statusCode": data["statusCode"],
                "response": data["response"],
            })

        all_ok = all(op["success"] for op in row_ops)
        results.append({
            "row": item["row"],
            "hostname": item["hostname"],
            "entityID": item["entityID"],
            "operations": row_ops,
            "success": all_ok,
        })

    return jsonify({
        "ok": all(r["success"] for r in results),
        "results": results,
        "summary": {
            "rows": len(results),
            "successfulRows": sum(1 for r in results if r["success"]),
            "failedRows": sum(1 for r in results if not r["success"]),
            "totalDeleteOps": sum(
                sum(1 for op in r["operations"] if op["operation"] == "DELETE") for r in results
            ),
            "totalAddOps": sum(
                sum(1 for op in r["operations"] if op["operation"] == "POST") for r in results
            ),
        },
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=True)
