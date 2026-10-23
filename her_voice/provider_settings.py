"""Local provider configuration; saved credentials never enter API responses."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path

import aiohttp

from .providers import PROVIDERS, build_provider_request, validate_provider_url
from .voices import atomic_json


FIELDS = {"provider", "url", "model", "api_key"}


def saved_environment(root: Path) -> dict[str, str]:
    path = root / ".runtime" / "provider-settings.json"
    if not path.exists():
        return {}
    try:
        if path.stat().st_size > 16384:
            raise ValueError
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) != FIELDS or not all(isinstance(v, str) for v in data.values()):
            raise ValueError
    except (OSError, ValueError, UnicodeError):
        raise ValueError("Saved provider settings are invalid; check the local provider-settings.json file.") from None
    return {"VOICE_PROVIDER": data["provider"], "VOICE_PROVIDER_URL": data["url"],
            "VOICE_PROVIDER_MODEL": data["model"], "DEEPSEEK_MODEL": data["model"],
            "VOICE_PROVIDER_API_KEY": data["api_key"], "DEEPSEEK_API_KEY": ""}


def public_configuration(settings) -> dict:
    return {"provider": settings.provider, "url": settings.provider_url,
            "model": settings.model, "api_key_configured": bool(settings.api_key)}


def candidate_settings(current, data):
    if not isinstance(data, dict) or set(data) != FIELDS or not all(isinstance(v, str) for v in data.values()):
        raise ValueError("请填写供应商、接口网址、模型和 API Key。")
    provider, url, model, key = (data[name].strip() for name in ("provider", "url", "model", "api_key"))
    if provider not in PROVIDERS or len(url) > 2048 or len(key) > 4096:
        raise ValueError("回答接口设置格式无效。")
    try:
        local = validate_provider_url(url)
    except ValueError:
        raise ValueError("请填写完整的 HTTPS Chat Completions 接口网址；本机接口可使用 HTTP。") from None
    if not key:
        # Never forward a saved credential to a newly entered destination.
        if provider == current.provider and url == current.provider_url:
            key = current.api_key
        elif provider == "deepseek" or not local:
            raise ValueError("更换供应商或接口网址时，请重新填写该接口的 API Key。")
    if not key and (provider == "deepseek" or not local):
        raise ValueError("请填写 API Key。")
    try:
        candidate = replace(current, provider=provider, provider_url=url,
                            provider_model=model, deepseek_model=model,
                            provider_api_key=key, deepseek_api_key="")
    except ValueError:
        raise ValueError("请检查模型名称与 API Key；内容需为单行，模型名称不可为空。") from None
    if not candidate.provider_ready:
        raise ValueError("请填写实际的 API Key，不能使用范例占位文字。")
    return candidate


def save_configuration(settings) -> None:
    atomic_json(settings.root / ".runtime" / "provider-settings.json",
                {"provider": settings.provider, "url": settings.provider_url,
                 "model": settings.model, "api_key": settings.api_key})


async def bounded_json(content, limit=16384):
    raw = bytearray()
    async for chunk in content.iter_chunked(4096):
        raw.extend(chunk)
        if len(raw) > limit:
            raise ValueError("回答接口设置资料过大。")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise ValueError("回答接口设置格式无效。") from None


async def test_configuration(settings, session) -> dict:
    outgoing = build_provider_request(settings, {
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "stream": False, "max_tokens": 8,
    })
    errors = {
        401: "API Key 无效或已失效（HTTP 401）。",
        402: "帐户余额不足或付款受限（HTTP 402），请检查此 API 帐户的帐务。",
        403: "此 API Key 没有接口或模型的使用权限（HTTP 403）。",
        404: "找不到接口或模型，请检查网址与模型名称（HTTP 404）。",
        429: "请求过于频繁或额度受限（HTTP 429），请稍后重试。",
    }
    try:
        async with session.post(outgoing.url, headers=outgoing.headers, json=outgoing.payload,
                                allow_redirects=False,
                                timeout=aiohttp.ClientTimeout(total=20, connect=5, sock_read=15)) as response:
            if response.status != 200:
                return {"ok": False, "status": response.status,
                        "message": errors.get(response.status, f"接口返回 HTTP {response.status}，请检查供应商设置。")}
            data = await bounded_json(response.content, 65536)
            choices = data.get("choices") if isinstance(data, dict) else None
            message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
            if not isinstance(message, dict) or not isinstance(message.get("content"), str) or not message["content"].strip():
                return {"ok": False, "status": 200, "message": "接口已回应，但未返回有效的模型文字。请检查模型名称。"}
            return {"ok": True, "status": 200, "message": "连接成功，模型已回应。此测试尚未保存设置。"}
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
        return {"ok": False, "message": "无法连接或回应超时，请检查接口网址与网络。"}

def catalog_candidate(current, data):
    # Model discovery needs a destination and credential, not a model ID.
    if not isinstance(data, dict) or set(data) != FIELDS - {"model"}:
        raise ValueError("请填写供应商、接口网址和 API Key。")
    return candidate_settings(current, {**data, "model": "catalog-discovery"})


async def list_configuration_models(settings, session) -> dict:
    url = settings.provider_url.rstrip("/")
    suffix = "/chat/completions"
    if not url.endswith(suffix):
        raise ValueError("此接口无法推导模型列表网址；请使用以 /chat/completions 结尾的接口，或手动填写模型。")
    url = url[:-len(suffix)] + "/models"
    headers = {"Authorization": "Bearer " + settings.api_key} if settings.api_key else {}
    errors = {
        401: "模型列表认证失败（HTTP 401），请检查 API Key。",
        403: "没有读取模型列表的权限（HTTP 403）。",
        404: "此接口没有提供模型列表（HTTP 404），可手动填写模型。",
        429: "模型列表请求受限（HTTP 429），请稍后再拉取。",
    }
    try:
        async with session.get(url, headers=headers, allow_redirects=False,
                               timeout=aiohttp.ClientTimeout(total=20, connect=5, sock_read=15)) as response:
            if response.status != 200:
                return {"ok": False, "status": response.status,
                        "message": errors.get(response.status, f"模型列表返回 HTTP {response.status}，可手动填写模型。")}
            data = await bounded_json(response.content, 1048576)
            rows = data.get("data") if isinstance(data, dict) else None
            if not isinstance(rows, list) or len(rows) > 5000:
                raise ValueError
            models = {}
            for row in rows:
                model_id = row.get("id") if isinstance(row, dict) else None
                if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 256:
                    raise ValueError
                if any(ord(char) < 32 or ord(char) == 127 for char in model_id):
                    raise ValueError
                name = row.get("name", model_id)
                if not isinstance(name, str) or not name.strip():
                    name = model_id
                name = "".join(char for char in name if ord(char) >= 32 and ord(char) != 127)[:160]
                if settings.api_key and (settings.api_key in model_id or settings.api_key in name):
                    raise ValueError
                models[model_id] = {"id": model_id, "name": name}
            if not models:
                return {"ok": False, "status": 200, "message": "接口返回的模型列表为空，可手动填写模型。"}
            return {"ok": True, "models": sorted(models.values(), key=lambda model: model["id"]),
                    "message": f"已拉取 {len(models)} 个模型。"}
    except (asyncio.TimeoutError, aiohttp.ClientConnectionError, OSError):
        return {"ok": False, "message": "模型列表连接失败或超时，请检查网址与网络后再拉取。"}
    except (aiohttp.ClientError, ValueError, UnicodeError):
        return {"ok": False, "message": "接口未返回有效的模型列表，可手动填写模型。"}
