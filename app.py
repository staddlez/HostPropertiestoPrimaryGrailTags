
import csv
import io
import os
import re
from typing import Any, Dict, List, Tuple

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

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


def split_values(cell: str) -> List[str]:
    """
    Parse a CSV cell like:
      keyOne=value,keyTwo=value
    into:
      ["keyOne=value", "keyTwo=value"]

    Quote the cell when it contains commas:
      "keyOne=value,keyTwo=value"
    """
    if cell is None:
        return []
    raw = str(cell).strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


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


def parse_bool(value: str, default: bool = True) -> bool:
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
        for attr in attribute_headers:
            original_cell = (row.get(attr, "") or "").strip()
            original_values = split_values(original_cell)
            values = list(original_values)

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

            if operation_mode == "clear" and not values:
                # Clear can omit value. This clears the selected attribute for the entity.
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
                # For set, value is required. For clear, value is optional but useful when
                # you want to clear a specific hostProperty/hostTag key or key=value entry.
                if value:
                    op["value"] = value
                operations.append(op)

        if not operations:
            errors.append(f"Row {idx}: no operation values found.")
            continue

        parsed_rows.append({
            "row": idx,
            "entityName": entity_name,
            "entityID": entity_id,
            "originalCsvRow": original_csv_row,
            "finalCsvRow": final_csv_row,
            "changes": changes,
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


def run_payload_endpoint(rows: List[Dict[str, Any]], endpoint_path: str, *, params=None, success_empty_204=True):
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
    return jsonify({
        "ok": len(errors) == 0,
        "errors": errors,
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

    restart = parse_bool(request.form.get("restart"), default=True)
    return run_payload_endpoint(
        rows,
        "/oneagents/remoteConfigurationManagement",
        params={"restart": "true" if restart else "false"},
    )


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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=True)
