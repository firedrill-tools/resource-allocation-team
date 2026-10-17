"""Whole original complete Gemini function/text evidence before SDK normalization."""

from .strict import identifier, integer, keys, parse_json, text

MAX_RESPONSE_BYTES = 1024 * 1024
TOOLS = {
    "availability": {"inspect_request"},
    "reservation": {"inspect_request", "hold_resource", "commit_allocation"},
    "compensation": {"inspect_request", "release_holds"},
}


def usage_of(value):
    usage = value.get("usageMetadata") if type(value) is dict else None
    output = {
        "source": "original Gemini JSON counters; no estimate or billing proof",
        "cost_usd": None,
    }
    for name in (
        "promptTokenCount",
        "candidatesTokenCount",
        "totalTokenCount",
        "cachedContentTokenCount",
        "thoughtsTokenCount",
    ):
        status, number = "unknown", None
        if type(usage) is dict and name in usage:
            try:
                integer(usage[name])
                status, number = "observed", usage[name]
            except ValueError:
                status = "invalid"
        output[name] = {"status": status, "value": number}
    return output


def arguments(name, value):
    keys(
        value,
        {"resource_id", "expected_revision"} if name == "hold_resource" else set(),
    )
    if name == "hold_resource":
        identifier(value["resource_id"])
        integer(value["expected_revision"], 1)
    return value


def completed(raw, role):
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("original Gemini response exceeds1MiB")
    value = parse_json(raw)
    if type(value) is not dict or value.get("error") is not None:
        raise ValueError("whole original Gemini object without error required")
    text(value.get("responseId"), 1, 240)
    text(value.get("modelVersion"), 1, 240)
    feedback = value.get("promptFeedback")
    if feedback is not None and (
        type(feedback) is not dict or feedback.get("blockReason") is not None
    ):
        raise ValueError("original Gemini prompt blocked or unsupported")
    candidates = value.get("candidates")
    if (
        type(candidates) is not list
        or len(candidates) != 1
        or type(candidates[0]) is not dict
    ):
        raise ValueError("one complete original Gemini candidate required")
    candidate = candidates[0]
    if (
        type(candidate.get("index")) is not int
        or candidate["index"] != 0
        or candidate.get("finishReason") != "STOP"
    ):
        raise ValueError("explicit complete original Gemini STOP required")
    safety = candidate.get("safetyRatings", [])
    if type(safety) is not list or any(
        type(r) is not dict
        or (r.get("blocked") is not None and r.get("blocked") is not False)
        for r in safety
    ):
        raise ValueError("original Gemini candidate blocked or unsupported")
    content = candidate.get("content")
    if (
        type(content) is not dict
        or content.get("role") != "model"
        or type(content.get("parts")) is not list
        or len(content["parts"]) != 1
        or type(content["parts"][0]) is not dict
    ):
        raise ValueError("one complete original Gemini model part required")
    part = content["parts"][0]
    if (part.get("thought") is not None and part.get("thought") is not False) or set(
        part
    ) - {
        "text",
        "functionCall",
        "thought",
        "thoughtSignature",
    }:
        raise ValueError("thought/executable/unknown original Gemini part refused")
    if "functionCall" in part:
        if part.get("text") is not None:
            raise ValueError("mixed original Gemini function/text refused")
        call = part["functionCall"]
        if (
            type(call) is not dict
            or set(call) - {"name", "args", "id"}
            or call.get("name") not in TOOLS[role]
        ):
            raise ValueError("exact registered original Gemini role function required")
        if call.get("id") is not None:
            text(call["id"], 1, 240)
        args = arguments(call["name"], call.get("args"))
        core = {"name": call["name"], "args": args, "id": call.get("id")}
        final = None
    else:
        text(part.get("text"), 1, 16384)
        if not part["text"].strip():
            raise ValueError("nonblank completed original specialist text required")
        core = None
        final = part["text"]
    return value, {
        "response_id": value["responseId"],
        "reported_model": value["modelVersion"],
        "role": role,
        "finish_reason": "STOP",
        "function": core,
        "final_text": final,
        "content": content,
    }
