"""Synchronous local LM Studio client. GPU ownership belongs to the coordinator.

Discovery is read-only. Load/unload are explicit methods; inference refuses a
model that is not loaded rather than intentionally triggering JIT loading.
No method contacts ComfyUI, downloads a model or changes global settings.
"""
from __future__ import annotations

import base64
import binascii
import copy
import io
import json
import math
import re
import time
import uuid
from urllib import error, parse, request

from jsonschema import Draft202012Validator
from PIL import Image

from . import prompts


class LMStudioError(RuntimeError):
    def __init__(self, message, *, code="lmstudio_error", status_code=None, detail=""):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.detail = detail


RESIDENT_PREFIX = "h3-studio-resident-"
RESIDENT_MAX_BYTES = 1_500_000_000


def resident_cpu_profile():
    """A fresh explicit SDK load profile; ordinary REST loads remain unchanged."""
    return {"gpu": {"ratio": 0, "disabledGpus": [0]}, "contextLength": 4096,
            "offloadKVCacheToGpu": False, "evalBatchSize": 128,
            "flashAttention": True, "gpuStrictVramCap": True}


def validate_resident_cpu_config(config):
    """Reject unknown placement; a small filename alone never proves residency."""
    gpu = config.get("gpu", {}) if isinstance(config, dict) else {}
    ratio = gpu.get("ratio") if isinstance(gpu, dict) else None
    zero = ratio == "off" or (type(ratio) in (int, float) and ratio == 0)
    if not (isinstance(config, dict) and zero and gpu.get("disabledGpus") == [0]
            and type(config.get("contextLength")) is int and config["contextLength"] == 4096
            and config.get("offloadKVCacheToGpu") is False
            and type(config.get("evalBatchSize")) is int and config["evalBatchSize"] == 128
            and config.get("flashAttention") is True and config.get("gpuStrictVramCap") is True):
        raise LMStudioError("The small model's CPU placement could not be verified. Unload that LM Studio instance and prepare the resident model again; H3 was not unloaded.", code="resident_config_unverified")
    return copy.deepcopy(config)


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise LMStudioError("LM Studio returned an unexpected redirect", code="redirect_rejected")


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON contains duplicate keys")
        result[key] = value
    return result


def _strict_json(text):
    def reject_constant(value):
        raise ValueError("Non-finite JSON numbers are not accepted")
    return json.loads(text, object_pairs_hook=_no_duplicate_keys, parse_constant=reject_constant)


def validate_data_url(data_url):
    if not isinstance(data_url, str) or len(data_url) > 12_000_000:
        raise LMStudioError("Image input exceeds the supported size", code="invalid_image")
    match = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)", data_url)
    if not match:
        raise LMStudioError("Use an actual PNG, JPEG or WebP base64 image data URL", code="invalid_image")
    try:
        data = base64.b64decode(match.group(2), validate=True)
        if len(data) > 8 * 1024 * 1024:
            raise ValueError("Decoded image exceeds 8 MiB")
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 16_000_000 or min(image.size) < 1:
                raise ValueError("Image exceeds the pixel limit")
            if {"PNG": "png", "JPEG": "jpeg", "WEBP": "webp"}.get(image.format) != match.group(1):
                raise ValueError("Image MIME type does not match its bytes")
            image.verify()
    except (ValueError, OSError, binascii.Error, Image.DecompressionBombError) as exc:
        raise LMStudioError("The image data is invalid or too large", code="invalid_image") from exc
    return data_url


def _validated_content(content):
    if isinstance(content, str):
        if not content.strip() or len(content) > 48_000:
            raise LMStudioError("Prompt text must contain 1–48000 characters", code="invalid_request")
        return content, False
    if not isinstance(content, list) or not content or len(content) > 20:
        raise LMStudioError("Content must be text or a bounded text/image array", code="invalid_request")
    clean, image_count, text_length = [], 0, 0
    for part in content:
        if not isinstance(part, dict):
            raise LMStudioError("Invalid multimodal content part", code="invalid_request")
        if part.get("type") == "text" and set(part) == {"type", "text"} and isinstance(part["text"], str):
            text_length += len(part["text"])
            clean.append(copy.deepcopy(part))
        elif part.get("type") == "image_url" and set(part) == {"type", "image_url"}:
            item = part["image_url"]
            if not isinstance(item, dict) or set(item) - {"url", "detail"} or item.get("detail", "auto") not in ("auto", "low", "high"):
                raise LMStudioError("Invalid image content fields", code="invalid_image")
            validate_data_url(item.get("url"))
            image_count += 1
            clean.append(copy.deepcopy(part))
        else:
            raise LMStudioError("Only text and image_url content parts are supported", code="invalid_request")
    if image_count > 9 or text_length > 48_000:
        raise LMStudioError("Request exceeds the image/text limit", code="invalid_request")
    return clean, image_count > 0


class LMStudioClient:
    def __init__(self, base_url="http://127.0.0.1:1234/v1", api_key="", timeout=180, *, sdk_factory=None):
        parsed = parse.urlsplit(base_url)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path.rstrip("/") not in ("", "/v1")):
            raise ValueError("LM Studio URL must be a loopback HTTP address, optionally ending in /v1")
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("Invalid LM Studio port") from exc
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 1 <= timeout <= 600:
            raise ValueError("LM Studio timeout must be 1–600 seconds")
        if not isinstance(api_key, str) or "\r" in api_key or "\n" in api_key:
            raise ValueError("Invalid API key")
        self.origin = f"{parsed.scheme}://{parsed.netloc}"
        self.base_url = self.origin + "/v1"
        self.api_key = api_key
        self.timeout = float(timeout)
        self.last_completion_info = None
        self._sdk_factory = sdk_factory
        self._opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())

    def _request(self, method, path, payload=None):
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        req = request.Request(self.origin + path, data=data, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                raw = response.read(8_000_001)
                if len(raw) > 8_000_000:
                    raise LMStudioError("LM Studio response exceeded the size limit", code="response_too_large")
                return _strict_json(raw.decode("utf-8"))
        except error.HTTPError as exc:
            raw = exc.read(4096).decode("utf-8", errors="replace")
            # Retain only a small redacted diagnostic; never log image bodies or
            # include the supplied API credential in exceptions.
            detail = re.sub(r"data:image/[^\s\"']+", "[image data]", raw)
            if self.api_key:
                detail = detail.replace(self.api_key, "[redacted]")
            code = "authentication_required" if exc.code in (401, 403) else "http_error"
            raise LMStudioError(f"LM Studio returned HTTP {exc.code}", code=code, status_code=exc.code, detail=detail[:1000]) from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise LMStudioError("Cannot reach LM Studio; check its local server and try again", code="connection_error") from exc
        except (ValueError, UnicodeError) as exc:
            raise LMStudioError("LM Studio returned invalid JSON", code="invalid_response") from exc

    def native_models(self):
        data = self._request("GET", "/api/v1/models")
        if not isinstance(data, dict) or not isinstance(data.get("models"), list):
            raise LMStudioError("Native model discovery returned an invalid shape", code="invalid_response")
        return [m for m in data["models"] if isinstance(m, dict) and isinstance(m.get("key"), str)]

    def models(self):
        try:
            models = self.native_models()
        except LMStudioError as exc:
            if exc.status_code != 404:
                raise
            data = self._request("GET", "/v1/models")
            if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                raise LMStudioError("Model discovery returned an invalid shape", code="invalid_response")
            return [{"id": m["id"], "key": m["id"], "name": m["id"], "display_name": m["id"],
                     "vision": None, "loaded": None, "loaded_instances": [], "capabilities": {}}
                    for m in data["data"] if isinstance(m, dict) and isinstance(m.get("id"), str)]
        return [{**m, "id": m["key"], "name": m.get("display_name") or m["key"],
                 "vision": m.get("capabilities", {}).get("vision", False),
                 "loaded": bool(m.get("loaded_instances")), "loaded_instances": m.get("loaded_instances", [])}
                for m in models if m.get("type") == "llm"]

    def loaded_instances(self):
        return [{"instance_id": instance["id"], "id": instance["id"], "model": m["key"], "key": m["key"],
                 "vision": m.get("capabilities", {}).get("vision", False), "config": instance.get("config", {})}
                for m in self.native_models() for instance in m.get("loaded_instances", [])
                if isinstance(instance, dict) and isinstance(instance.get("id"), str)]

    def health(self):
        try:
            models = self.models()
            return {"ok": True, "base_url": self.base_url, "model_count": len(models),
                    "vision_model_count": sum(m.get("vision") is True for m in models),
                    "loaded_model_count": sum(m.get("loaded") is True for m in models)}
        except LMStudioError as exc:
            return {"ok": False, "base_url": self.base_url, "error": str(exc), "code": exc.code}

    def load_model(self, model, context_length=8192):
        if not isinstance(model, str) or not model or len(model) > 512:
            raise LMStudioError("Select a valid local model", code="invalid_model")
        if isinstance(context_length, bool) or not isinstance(context_length, int) or not 1024 <= context_length <= 32768:
            raise LMStudioError("Context length must be between 1024 and 32768 tokens", code="invalid_request")
        if not any(m["key"] == model for m in self.native_models()):
            raise LMStudioError("The selected model is not in the local model inventory", code="invalid_model")
        data = self._request("POST", "/api/v1/models/load", {"model": model, "context_length": context_length,
                             "flash_attention": True, "echo_load_config": True})
        if not isinstance(data, dict) or not isinstance(data.get("instance_id"), str) or data.get("status") != "loaded":
            raise LMStudioError("Model load did not return a confirmed instance ID", code="invalid_response")
        return data

    def resident_model_info(self, model):
        """Read-only native inventory gate for the dedicated small-model mode."""
        if not isinstance(model, str) or not model or len(model) > 512:
            raise LMStudioError("Select an installed 0.8B vision model for resident mode.", code="invalid_model")
        matches = [entry for entry in self.native_models() if entry["key"] == model]
        if len(matches) != 1:
            raise LMStudioError("The resident model must match one exact local model key.", code="invalid_model")
        entry = matches[0]
        size = entry.get("size_bytes")
        if (entry.get("type") != "llm" or "0.8b" not in model.lower()
                or entry.get("capabilities", {}).get("vision") is not True
                or type(size) is not int or not 0 < size <= RESIDENT_MAX_BYTES):
            raise LMStudioError("Resident mode needs an installed 0.8B model with vision and a size of at most 1.5 GB. Select one in Connections, or use the normal memory mode.", code="resident_model_ineligible")
        return copy.deepcopy(entry)

    def _resident_sdk(self):
        if self._sdk_factory is not None:
            return self._sdk_factory()
        if self.api_key:
            raise LMStudioError("Resident CPU loading is unavailable with this SDK authentication setup; use the normal memory mode.", code="resident_sdk_unavailable")
        try:
            import lmstudio
        except ImportError as exc:
            raise LMStudioError("Install Prompt Studio's updated dependencies to use resident CPU mode.", code="resident_sdk_unavailable") from exc
        return lmstudio.Client(parse.urlsplit(self.origin).netloc)

    @staticmethod
    def _resident_inventory(sdk, native):
        matches = [model for model in sdk.llm.list_downloaded() if model.model_key == native["key"]]
        if len(matches) != 1:
            raise LMStudioError("The SDK could not resolve the selected small model to one exact local file.", code="resident_model_unverified")
        item = matches[0]
        info = item.info.to_dict()
        if (not isinstance(item.path, str) or not item.path or info.get("vision") is not True
                or info.get("sizeBytes") != native["size_bytes"]):
            raise LMStudioError("The small model's local file and vision capability could not be verified.", code="resident_model_unverified")
        return item.path

    def _verify_resident_handle(self, handle, model, instance_id, expected_path):
        info = handle.get_info().to_dict()
        if (handle.identifier != instance_id or info.get("identifier") != instance_id
                or info.get("modelKey") != model or info.get("path") != expected_path):
            raise LMStudioError("The loaded small model does not match its exact selected file and instance.", code="resident_model_unverified")
        config = validate_resident_cpu_config(handle.get_load_config().to_dict())
        return {"ready": True, "status": "loaded", "instance_id": instance_id, "model": model,
                "profile": "resident_small_cpu", "context_length": 4096, "load_config": config}

    def verify_resident_model(self, model, instance_id):
        """Read only: verify an exact Studio instance without calling SDK JIT APIs."""
        if not isinstance(instance_id, str) or not instance_id.startswith(RESIDENT_PREFIX):
            raise LMStudioError("Resident mode can only keep a verified h3-studio-resident instance. Unload the other LM Studio instance first.", code="resident_model_unverified")
        native = self.resident_model_info(model)
        if not any(item.get("id") == instance_id for item in native.get("loaded_instances", []) if isinstance(item, dict)):
            raise LMStudioError("The selected resident model instance is no longer loaded.", code="model_not_loaded")
        try:
            with self._resident_sdk() as sdk:
                path = self._resident_inventory(sdk, native)
                matches = [handle for handle in sdk.llm.list_loaded() if handle.identifier == instance_id]
                if len(matches) != 1:
                    raise LMStudioError("The resident model instance could not be verified through the SDK.", code="resident_model_unverified")
                return self._verify_resident_handle(matches[0], model, instance_id, path)
        except LMStudioError:
            raise
        except Exception as exc:
            raise LMStudioError("LM Studio could not verify the resident CPU configuration. H3 was not unloaded.", code="resident_sdk_error") from exc

    def load_resident_model(self, model):
        """Explicit CPU load; the caller must hold the coordinator lock and verify idle."""
        native = self.resident_model_info(model)
        if self.loaded_instances():
            raise LMStudioError("Unload the other LM Studio instance before preparing the resident small model.", code="resident_model_conflict")
        instance_id = RESIDENT_PREFIX + uuid.uuid4().hex
        try:
            with self._resident_sdk() as sdk:
                path = self._resident_inventory(sdk, native)
                if sdk.llm.list_loaded():
                    raise LMStudioError("An LM Studio model appeared during resident preparation; no additional model was loaded.", code="resident_model_conflict")
                handle = sdk.llm.load_new_instance(path, instance_id, config=resident_cpu_profile(), ttl=None)
                result = self._verify_resident_handle(handle, model, instance_id, path)
            # Native inference uses this identifier. Confirm the mapping through
            # its inventory too, rather than falling back to a model-key JIT load.
            return self.verify_resident_model(model, result["instance_id"])
        except LMStudioError:
            raise
        except Exception as exc:
            # Never retry an uncertain load or unload an unverified handle.
            raise LMStudioError("LM Studio could not finish the resident CPU load. Check the small model instance before retrying; H3 was not unloaded.", code="resident_sdk_error") from exc

    def unload_model(self, instance_id):
        if not isinstance(instance_id, str) or not instance_id or len(instance_id) > 512:
            raise LMStudioError("Select a valid loaded instance", code="invalid_model")
        data = self._request("POST", "/api/v1/models/unload", {"instance_id": instance_id})
        if not isinstance(data, dict) or data.get("instance_id") != instance_id:
            raise LMStudioError("Unload did not confirm the requested instance ID", code="invalid_response")
        return data

    def _loaded_model(self, model, require_vision=False):
        if not isinstance(model, str) or not model:
            raise LMStudioError("Select a model", code="invalid_model")
        for entry in self.native_models():
            instances = entry.get("loaded_instances", [])
            matching = [x for x in instances if isinstance(x, dict) and isinstance(x.get("id"), str)
                        and (entry["key"] == model or x["id"] == model)]
            if matching:
                if require_vision and entry.get("capabilities", {}).get("vision") is not True:
                    raise LMStudioError("The selected model does not support image input", code="vision_unsupported")
                if len(matching) != 1:
                    raise LMStudioError("Select one exact loaded instance; this model has multiple instances", code="ambiguous_model")
                return matching[0]["id"], entry.get("capabilities", {})
        raise LMStudioError("The selected model is not loaded. Use Prepare AI before requesting assistance", code="model_not_loaded")

    def complete_json(self, model, system, content, schema, max_tokens=1800, temperature=0.3):
        self.last_completion_info = None
        if not isinstance(system, str) or len(system) > 16_000:
            raise LMStudioError("System instructions exceed the supported limit", code="invalid_request")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 32 <= max_tokens <= 4096:
            raise LMStudioError("max_tokens must be 32–4096", code="invalid_request")
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not math.isfinite(temperature) or not 0 <= temperature <= 1:
            raise LMStudioError("temperature must be between 0 and 1", code="invalid_request")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise LMStudioError("A JSON object schema is required", code="invalid_schema")
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:
            raise LMStudioError("Invalid structured response schema", code="invalid_schema") from exc
        # Never resolve model-supplied or remote schema references.
        def reject_refs(value):
            if isinstance(value, dict):
                if "$ref" in value or "$dynamicRef" in value:
                    raise LMStudioError("Schema references are not supported", code="invalid_schema")
                for item in value.values(): reject_refs(item)
            elif isinstance(value, list):
                for item in value: reject_refs(item)
        reject_refs(schema)
        validator = Draft202012Validator(schema)
        content, has_images = _validated_content(content)
        instance_id, capabilities = self._loaded_model(model, has_images)
        payload = {"model": instance_id, "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
                   "response_format": {"type": "json_schema", "json_schema": {"name": "h3_authoring_response", "strict": True, "schema": copy.deepcopy(schema)}},
                   "max_tokens": max_tokens, "temperature": temperature, "stream": False}
        # Native discovery advertises off/on, but the compatible endpoint uses
        # none/medium. `none` was verified with this installed Qwen3.5-2B and
        # LM Studio 0.4.23+1: zero reasoning tokens, complete image JSON in 1.74s.
        # Default thinking otherwise exhausted the 700-token image budget.
        if "off" in capabilities.get("reasoning", {}).get("allowed_options", []):
            payload["reasoning_effort"] = "none"
        started = time.perf_counter()
        retry_reason = None
        for attempt in range(2):
            try:
                response = self._request("POST", "/v1/chat/completions", payload)
            except LMStudioError as exc:
                unsupported = exc.status_code in (400, 422, 500) and any(x in exc.detail.lower() for x in ("response_format", "json_schema", "structured output", "grammar"))
                if attempt == 0 and unsupported:
                    retry_reason = "Server rejected structured output; retried once with JSON instructions and local schema validation"
                    payload.pop("response_format", None)
                    payload["messages"][0]["content"] += "\nReturn only JSON conforming to this exact schema: " + json.dumps(schema, separators=(",", ":"))
                    continue
                raise
            try:
                choice = response["choices"][0]
                if choice.get("finish_reason") == "length":
                    raise LMStudioError("The model response was truncated; shorten the request or simplify the plan", code="response_truncated")
                raw = choice["message"]["content"]
                if not isinstance(raw, str) or len(raw) > 100_000:
                    raise ValueError("Response content must be bounded JSON text")
                raw = raw.strip()
                if raw.startswith("```"):
                    fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", raw, re.DOTALL)
                    if fenced:
                        raw = fenced.group(1)
                value = _strict_json(raw)
                validation_error = next(validator.iter_errors(value), None)
                if validation_error is not None:
                    raise ValueError("Response violates the requested schema")
            except LMStudioError:
                raise
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                if attempt == 0:
                    retry_reason = "Invalid model JSON; retried once with an explicit correction instruction"
                    payload["messages"].append({"role": "user", "content": "The previous response did not match the requested JSON schema. Return exactly the required object, without extra fields, commentary or markdown."})
                    continue
                raise LMStudioError("The model did not return valid structured data after one retry; nothing was applied", code="invalid_model_output") from exc
            self.last_completion_info = {"model": instance_id, "attempts": attempt + 1, "retry_reason": retry_reason,
                "response_format_used": "json_schema" if "response_format" in payload else "json_instructions",
                "locally_validated": True, "elapsed_seconds": time.perf_counter() - started,
                "reasoning_effort": payload.get("reasoning_effort"),
                "finish_reason": choice.get("finish_reason"), "usage": response.get("usage", {})}
            return value
        raise LMStudioError("Structured output request failed", code="invalid_model_output")

    def analyse_image(self, model, data_url, asset):
        if not isinstance(asset, dict):
            raise LMStudioError("Asset metadata must be an object", code="invalid_request")
        content = prompts.image_content(validate_data_url(data_url), asset)
        return self.complete_json(model, prompts.image_system(asset), content, prompts.IMAGE_SCHEMA, max_tokens=700, temperature=0.2)

    def propose_plan(self, model, project, instructions="", persona="universal"):
        if not isinstance(instructions, str) or len(instructions) > 8000:
            raise LMStudioError("Planning instructions exceed the supported limit", code="invalid_request")
        system, content, schema = prompts.plan_prompt(project, instructions, persona)
        proposal = self.complete_json(model, system, content, schema)
        duration = project.get("duration")
        total = sum(shot["duration"] for shot in proposal["shots"])
        if not isinstance(duration, (int, float)) or abs(total - duration) > 0.01:
            raise LMStudioError("Proposed shot durations do not match the project duration; nothing was applied", code="invalid_plan_timing")
        for shot in proposal["shots"]:
            if set(shot["visible_subject_ids"]) & set(shot["offscreen_subject_ids"]):
                raise LMStudioError("A proposed subject is both visible and offscreen in one shot", code="invalid_plan_subjects")
        return proposal

    def assist(self, model, project, shot_id, field, instructions="", persona="universal"):
        if not isinstance(instructions, str) or len(instructions) > 8000:
            raise LMStudioError("Assistance instructions exceed the supported limit", code="invalid_request")
        system, content, schema = prompts.assist_prompt(project, shot_id, field, instructions, persona)
        return self.complete_json(model, system, content, schema, max_tokens=700)
