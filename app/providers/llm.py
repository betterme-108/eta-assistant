"""统一 LLM 客户端（OpenAI 兼容直连，对齐 ContractPilot 的 direct 通道实现）。

设计要点（参考 ContractPilot src/backend/engine/algorithms/llm/llama_index.py）：
  - 单一入口 chat() / chat_json() / chat_vision()，供归因引擎、练习生成、OCR 复用；
  - deepseek* 前缀模型自动路由 DeepSeek 官方 API（config.llm_endpoint）；
  - deepseek V4 默认开启思考链且与正文共享 max_tokens 预算，本项目需要
    确定性 JSON 输出，故请求体固定附加 thinking={"type": "disabled"}；
  - 共享 httpx.Client 连接池 + 分层超时（connect/write 短、read 长）；
  - JSON 解析三层容错：剥离 ```json 围栏 → json.loads → 正则提取首个
    JSON 对象 → 仍失败返回 None（由调用方降级，保证管道可用）；
  - 网络失败自动重试（指数退避），最终失败抛 LLMError 由调用方降级。

伦理约束：调用方传入的 payload 只含题目文本，任何学生标识不得进入本模块。
"""
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

import httpx

from ..core import config, trace

logger = logging.getLogger("eta.llm")


class LLMError(Exception):
    """LLM 调用失败（网络/鉴权/限流/响应异常）。"""


# ---------------------------------------------------------------- 连接池（进程级共享）

_client: Optional[httpx.Client] = None


def _get_client() -> httpx.Client:
    """共享连接池：TCP/TLS 复用降低批量调用耗时。"""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.Client(
            timeout=httpx.Timeout(config.LLM_TIMEOUT,
                                  connect=config.LLM_CONNECT_TIMEOUT,
                                  write=60.0),
        )
    return _client


def reset_client() -> None:
    """重建连接池（长连接粘到异常节点时换节点重试）。"""
    global _client
    if _client is not None and not _client.is_closed:
        _client.close()
    _client = None


# ---------------------------------------------------------------- 请求构建

def _build_payload(messages: List[Dict[str, Any]], model: str,
                   temperature: Optional[float], max_tokens: Optional[int]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature if temperature is not None else config.LLM_TEMPERATURE,
        "max_tokens": max_tokens or config.LLM_MAX_TOKENS,
        "stream": False,
    }
    if config.is_thinking_chain_model(model):
        # V4/豆包 seed 系思考链与正文共享 max_tokens 预算，长思考可致正文 0 token；
        # 本系统需要确定性 JSON，统一关闭思考模式
        payload["thinking"] = {"type": "disabled"}
    return payload


# ---------------------------------------------------------------- 对话入口

def chat(messages: List[Dict[str, Any]],
         model: str = "",
         temperature: Optional[float] = None,
         max_tokens: Optional[int] = None,
         retries: Optional[int] = None,
         base_url: Optional[str] = None,
         api_key: Optional[str] = None) -> str:
    """OpenAI 兼容对话补全，返回首个 choice 的文本内容。

    base_url/api_key 可选（视觉模型等非全局端点直连时传入；缺省走
    config.llm_endpoint 按模型名路由，与既有行为完全一致）。
    失败重试（指数退避）；最终失败抛 LLMError，由调用方降级。
    """
    model = model or config.LLM_MODEL
    base_url, api_key = _resolve_endpoint(model, base_url, api_key)
    return _request(messages, model=model, temperature=temperature,
                    max_tokens=max_tokens, retries=retries,
                    base_url=base_url, api_key=api_key,
                    extract=_extract_content)


def _resolve_endpoint(model: str, base_url: Optional[str],
                      api_key: Optional[str]) -> tuple:
    """端点解析：显式传入优先（视觉模型独立端点），缺省按模型名路由。"""
    if not base_url or not api_key:
        base_url, api_key = config.llm_endpoint(model)
    if not base_url or not api_key:
        raise LLMError("LLM 未配置（缺少 .env 中的 DEEPSEEK_API_KEY / LLM_API_KEY）")
    return base_url, api_key


def _request(messages: List[Dict[str, Any]], model: str,
             temperature: Optional[float], max_tokens: Optional[int],
             retries: Optional[int], base_url: str, api_key: str,
             extract) -> Any:
    """统一请求循环（重试/退避/空响应换节点）：extract(response) 提取内容，
    返回 falsy 视为空响应按既有策略重试，最终失败抛 LLMError。
    每次调用的输入输出细节落运行留痕（trace，含重试后终态）。"""
    payload = _build_payload(messages, model, temperature, max_tokens)
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": "Bearer %s" % api_key,
               "Content-Type": "application/json"}

    attempts = (config.LLM_RETRIES if retries is None else retries) + 1
    last_err: Optional[Exception] = None
    t_all = time.time()
    for attempt in range(attempts):
        t0 = time.time()
        try:
            resp = _get_client().post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            content = extract(data)
            if content:
                # INFO：AI 调用是耗时且有意义的业务事件，终端可见；细节参数仅文件
                logger.info("AI 调用完成 | 模型=%s 返回 %d 字，耗时 %.0fms",
                            model, len(content), (time.time() - t0) * 1000)
                logger.debug("llm.chat ok | model=%s chars=%d %.0fms",
                             model, len(content), (time.time() - t0) * 1000)
                trace.record("llm", input=messages, output=content, meta={
                    "model": model, "endpoint": base_url,
                    "temperature": payload.get("temperature"),
                    "max_tokens": payload.get("max_tokens"),
                    "attempts": attempt + 1,
                    "latency_ms": int((time.time() - t0) * 1000),
                    "usage": data.get("usage") if isinstance(data, dict) else {},
                })
                return content
            # 空响应恢复（参考 ContractPilot P-A08）：思考链耗尽共享预算 /
            # 负载均衡粘到异常节点 → 重建连接池换节点重试
            logger.warning("LLM 返回空 content | body=%s", str(data)[:200])
            reset_client()
            last_err = LLMError("LLM 响应 content 为空：%s" % str(data)[:200])
        except Exception as exc:  # noqa: BLE001 —— 统一降级点
            last_err = exc
            logger.warning("llm.chat 失败（第 %d 次）: %s", attempt + 1, exc)
        if attempt < attempts - 1:
            time.sleep(1.0 * (attempt + 1))
    trace.record("llm", input=messages, error=last_err, meta={
        "model": model, "endpoint": base_url, "attempts": attempts,
        "latency_ms": int((time.time() - t_all) * 1000),
    })
    raise LLMError(str(last_err))


def chat_vision(image_base64: str, prompt: str, model: str = "",
                temperature: Optional[float] = None,
                mime_type: str = "image/jpeg",
                base_url: Optional[str] = None,
                api_key: Optional[str] = None,
                max_tokens: Optional[int] = None) -> str:
    """多模态（视觉）对话：OpenAI 兼容 content 数组格式。

    base_url/api_key 缺省走全局 LLM 端点；视觉模型独立端点（如百炼/火山的
    OCR 与作答流）显式传入，避免依赖全局 LLM_* 恰好指向同一服务。
    """
    messages: List[Dict[str, Any]] = [{
        "role": "user",
        "content": [
            {"type": "image_url",
             "image_url": {"url": "data:%s;base64,%s" % (mime_type, image_base64)}},
            {"type": "text", "text": prompt},
        ],
    }]
    return chat(messages, model=model, temperature=temperature,
                max_tokens=max_tokens, base_url=base_url, api_key=api_key)


def chat_vision_parts(image_base64: str, prompt: str, model: str = "",
                      temperature: Optional[float] = None,
                      mime_type: str = "image/jpeg",
                      base_url: Optional[str] = None,
                      api_key: Optional[str] = None) -> Any:
    """多模态对话，返回 message.content 的原始值（可能是数组，不拍平成字符串）。

    qwen-vl-ocr 的 advanced_recognition 坐标模式返回数组 content：
    [{type: "text", text: 全文, ocr_result: {words_info: [{text, box}…]}}]。
    chat()/chat_vision() 只取文本会把 ocr_result 丢掉，坐标转录专用本入口。
    """
    messages: List[Dict[str, Any]] = [{
        "role": "user",
        "content": [
            {"type": "image_url",
             "image_url": {"url": "data:%s;base64,%s" % (mime_type, image_base64)}},
            {"type": "text", "text": prompt},
        ],
    }]
    base_url, api_key = _resolve_endpoint(model, base_url, api_key)
    return _request(messages, model=model, temperature=temperature,
                    max_tokens=None, retries=None,
                    base_url=base_url, api_key=api_key,
                    extract=_extract_content_parts)


def chat_json(messages: List[Dict[str, Any]], model: str = "",
              temperature: Optional[float] = None,
              max_tokens: Optional[int] = None,
              retries: Optional[int] = None) -> Optional[Any]:
    """对话并解析 JSON（三层容错）；解析失败返回 None（不抛异常）。"""
    text = chat(messages, model=model, temperature=temperature,
                max_tokens=max_tokens, retries=retries)
    return parse_json(text)


# ---------------------------------------------------------------- 解析容错

def _extract_content(data: Dict[str, Any]) -> str:
    """从响应中提取文本：标准 OpenAI choices 嵌套结构。"""
    try:
        content = data["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        return ""
    return content if isinstance(content, str) else str(content)


def _extract_content_parts(data: Dict[str, Any]) -> Any:
    """提取 message.content 原始值（数组时保留 ocr_result 等结构字段）。"""
    try:
        return data["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        return ""


def strip_markdown_fence(text: str) -> str:
    """剥离 ```json ... ``` 围栏。"""
    stripped = (text or "").strip()
    if stripped.startswith("```json"):
        stripped = stripped[7:]
    elif stripped.startswith("```"):
        stripped = stripped[3:]
    if stripped.endswith("```"):
        stripped = stripped[:-3]
    return stripped.strip()


_JSON_OBJECT_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.DOTALL)
_JSON_ARRAY_RE = re.compile(r"\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\]", re.DOTALL)


def parse_json(text: str) -> Optional[Any]:
    """三层容错 JSON 解析：围栏剥离 → 直接 loads → 正则提取首个对象/数组。"""
    if not text:
        return None
    cleaned = strip_markdown_fence(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        for pattern in (_JSON_OBJECT_RE, _JSON_ARRAY_RE):
            match = pattern.search(cleaned)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    continue
    return None
