"""Translate Qoder's observed BYOK/chat wire format without forwarding account credentials."""
import base64
import json

STANDARD = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
QODER = "_doRTgHZBKcGVjlvpC,@aFSx#DPuNJme&i*MzLOEn)sUrthbf%Y^w.(kIQyXqWA!$"
DECODE_TABLE = str.maketrans(QODER, STANDARD)


def decode_body(body, encoded=False):
    if encoded:
        text = body.decode("ascii").translate(DECODE_TABLE)
        third = len(text) // 3
        if third:
            text = text[-third:] + text[third:-third] + text[:third]
        body = base64.b64decode(text, validate=True)
    result = json.loads(body)
    if not isinstance(result, dict):
        raise ValueError("Qoder 请求体必须是对象。")
    return result


def source_model(payload):
    config = payload.get("model_config")
    if not isinstance(config, dict):
        config = {}
    custom = config.get("custom_model") or payload.get("custom_model")
    if isinstance(custom, dict):
        config = custom
    packed = config.get("model")
    if config.get("key") == "custom_model" and isinstance(packed, str) and packed.startswith("{"):
        parsed = json.loads(packed)
        if isinstance(parsed, dict) and isinstance(parsed.get("model"), str):
            config = parsed
    provider = config.get("provider") or payload.get("provider")
    model = config.get("model") or payload.get("model") or config.get("key")
    if not isinstance(model, str) or not model:
        raise ValueError("Qoder 请求没有可识别的模型 ID。")
    return provider, model


def mapped_model(payload, mapping):
    provider, source = source_model(payload)
    candidates = [source]
    config = payload.get("model_config")
    if isinstance(config, dict) and isinstance(config.get("key"), str):
        key = config["key"]
        if key.startswith("openrouter/pg/"):
            candidates.extend([key, key[len("openrouter/pg/"):]])
    if source.startswith("openrouter/pg/"):
        candidates.append(source[len("openrouter/pg/"):])
    if provider == "openrouter":
        candidates.append("openrouter/pg/" + source)
    for candidate in candidates:
        if candidate in mapping:
            return source, mapping[candidate]
    raise ValueError("未配置这个 Qoder 模型的内网映射。")


def chat_message(message):
    result = {key: message[key] for key in ("role", "tool_calls", "tool_call_id", "name", "reasoning_content") if key in message}
    content = message.get("contents") or message.get("content")
    if isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict):
                raise ValueError("Qoder 消息内容块格式无效。")
            kind = part.get("type")
            if kind == "text" and isinstance(part.get("text"), str):
                parts.append({"type": "text", "text": part["text"]})
            elif kind == "image_url" and isinstance(part.get("image_url"), dict):
                parts.append({"type": "image_url", "image_url": part["image_url"]})
            else:
                raise ValueError("尚未支持这种 Qoder 消息内容块，未向上游发送。")
        content = "\n".join(part["text"] for part in parts) if all(part["type"] == "text" for part in parts) else parts
    if content is not None and not isinstance(content, (str, list)):
        raise ValueError("Qoder 消息正文格式无效。")
    if content in (None, "") and not result.get("tool_calls") and message.get("role") != "tool":
        raise ValueError("Qoder 消息正文为空，未向上游发送。")
    result["content"] = content
    return result


def chat_request(payload, target):
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("当前 Qoder 请求没有标准 messages，未向上游发送。")
    if not all(isinstance(item, dict) and isinstance(item.get("role"), str) for item in messages):
        raise ValueError("Qoder messages 格式无效。")
    stream = payload.get("stream", True)
    if not isinstance(stream, bool):
        raise ValueError("Qoder stream 必须为布尔值。")
    result = {"model": target, "messages": [chat_message(message) for message in messages], "stream": stream}
    system = payload.get("system") or payload.get("system_role_content")
    if isinstance(system, str) and system:
        result["messages"].insert(0, {"role": "system", "content": system})
    elif system not in (None, ""):
        raise ValueError("尚未支持这种 Qoder system 格式，未向上游发送。")
    tools = payload.get("tools")
    if tools is not None:
        if not isinstance(tools, list):
            raise ValueError("Qoder tools 格式无效。")
        result["tools"] = tools
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}
    for key in ("temperature", "top_p", "max_tokens", "max_completion_tokens", "stop",
                "frequency_penalty", "presence_penalty", "seed", "response_format",
                "tool_choice", "parallel_tool_calls", "reasoning_effort"):
        value = parameters.get(key, payload.get(key))
        if value is not None:
            result[key] = value
    if stream:
        result["stream_options"] = {"include_usage": True}
    return result


def check_result(success, code="", message=""):
    return json.dumps({"success": bool(success), "errorCode": str(code), "errorMsg": message},
                      ensure_ascii=False).encode("utf-8")


def envelope(body, status=200):
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    value = {"statusCodeValue": status, "statusCode": "OK" if status == 200 else "ERROR", "body": text}
    return ("data: " + json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n\n").encode("utf-8")


def stream_error(status, message):
    return envelope({"error": {"code": status, "message": message}}, status) + envelope("[DONE]")


def complete_response(body, request_id=""):
    """BYOK helpers consume OpenAI delta events followed by an explicit finish event."""
    value = json.loads(body)
    choices = value.get("choices") if isinstance(value, dict) else None
    if (not isinstance(choices, list) or not choices
            or not isinstance(choices[0], dict) or not isinstance(choices[0].get("message"), dict)):
        raise ValueError("内网模型没有返回完整的聊天结果。")
    result = dict(value)
    result["object"] = "chat.completion.chunk"
    result["choices"] = []
    for index, choice in enumerate(choices):
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise ValueError("内网模型返回了无效的完整聊天结果。")
        result["choices"].append({"index": choice.get("index", index), "delta": choice["message"],
                                  "finish_reason": choice.get("finish_reason")})
    if not result.get("id") and isinstance(request_id, str) and request_id:
        result["id"] = request_id
    # Qoder's helper callback treats a JSON [DONE] body as data; finish ends the RPC.
    return envelope(result) + b"event: finish\ndata: [DONE]\n\n"


class QoderSSE:
    """Frame byte chunks incrementally; content, tool deltas and usage remain intact."""
    def __init__(self):
        self.pending = bytearray()
        self.done = False
        self.finished = False

    def frame(self, frame):
        data = b"\n".join(line[5:].lstrip(b" ") for line in frame.splitlines() if line.startswith(b"data:"))
        if not data:
            return b""
        text = data.decode("utf-8")
        if text == "[DONE]":
            self.done = True
            return envelope("[DONE]")
        value = json.loads(text)
        if not isinstance(value, dict) or not isinstance(value.get("choices"), list):
            raise ValueError("Invalid upstream chat chunk")
        for choice in value["choices"]:
            if not isinstance(choice, dict):
                raise ValueError("Invalid upstream chat choice")
            if choice.get("finish_reason"):
                self.finished = True
                delta = choice.setdefault("delta", {})
                if isinstance(delta, dict):
                    delta.setdefault("finish_reason", choice["finish_reason"])
        return envelope(value)

    def __call__(self, chunk):
        if self.done:
            return b""
        self.pending.extend(chunk)
        output = bytearray()
        try:
            while True:
                data = bytes(self.pending)
                a, b = data.find(b"\n\n"), data.find(b"\r\n\r\n")
                candidates = [(i, n) for i, n in ((a, 2), (b, 4)) if i >= 0]
                if not candidates:
                    break
                index, length = min(candidates)
                frame = bytes(self.pending[:index])
                del self.pending[:index + length]
                output.extend(self.frame(frame))
                if self.done:
                    self.pending.clear()
                    break
            if not chunk and not self.done:
                if self.pending.strip():
                    output.extend(self.frame(bytes(self.pending)))
                    self.pending.clear()
                if not self.done:
                    output.extend(envelope("[DONE]") if self.finished else stream_error(502, "内网模型流提前结束。"))
                    self.done = True
        except (ValueError, UnicodeError, TypeError):
            output.extend(stream_error(502, "内网模型返回了无法解析的流式响应。"))
            self.done = True
            self.pending.clear()
        return bytes(output)
