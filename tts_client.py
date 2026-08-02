"""
豆包语音合成 TTS 模块 - 调用火山引擎声音复刻大模型 API 进行语音合成
使用 HTTP Chunked 单向流式接口
"""

import asyncio
import base64
import json
import uuid
from typing import Optional

import httpx
from astrbot.api import logger

# 火山引擎语音合成 API 默认基础地址（留空配置时使用）
DEFAULT_BASE_URL = "https://openspeech.bytedance.com"


class DoubaoTTSClient:
    """火山引擎豆包语音合成 TTS 客户端（HTTP Chunked 流式）"""

    def __init__(
        self,
        api_key: str,
        speaker_id: str,
        resource_id: str = "seed-icl-2.0",
        tts_model: str = "",
        audio_format: str = "mp3",
        sample_rate: int = 24000,
        speech_rate: int = 0,
        loudness_rate: int = 0,
        explicit_language: str = "",
        tone_fidelity: bool = False,
        api_base_url: str = "",
    ):
        self.api_key = api_key
        self.speaker_id = speaker_id
        self.resource_id = resource_id
        self.tts_model = tts_model
        self.audio_format = audio_format
        self.sample_rate = sample_rate
        self.speech_rate = speech_rate
        self.loudness_rate = loudness_rate
        self.explicit_language = explicit_language
        self.tone_fidelity = tone_fidelity
        # API 基础地址：留空使用内置默认，填写后替换（如代理地址）
        self.api_base_url = (api_base_url or DEFAULT_BASE_URL).rstrip("/")
        self.tts_api_url = f"{self.api_base_url}/api/v3/tts/unidirectional"

    def _build_headers(self) -> dict:
        """构建请求头"""
        return {
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        text: str,
        section_id: str = "",
        context_texts: str = "",
        use_tag_parser: bool = False,
    ) -> dict:
        """
        构建请求体

        Args:
            text: 待合成的文本（Cot 模式下是带 <cot> 标签的文本）
            section_id: 本次合成的多轮上下文 ID（由 main.py 从映射表查出）
            context_texts: QA 模式生成的语音指令句
            use_tag_parser: 是否启用 Cot 语音标签解析
        """
        # additions 中的 JSON 字符串
        additions_parts = []

        if self.explicit_language:
            additions_parts.append(f'"explicit_language":"{self.explicit_language}"')

        if use_tag_parser:
            additions_parts.append('"use_tag_parser":true')

        additions_str = ""
        if additions_parts:
            additions_str = "{" + ",".join(additions_parts) + "}"

        payload = {
            "user": {
                "uid": "astrbot_doubao_tts",
            },
            "req_params": {
                "text": text,
                "speaker": self.speaker_id,
                "audio_params": {
                    "format": self.audio_format,
                    "sample_rate": self.sample_rate,
                    "speech_rate": self.speech_rate,
                    "loudness_rate": self.loudness_rate,
                },
            },
        }

        # 声音复刻模型版本
        if self.tts_model and self.resource_id == "seed-icl-2.0":
            payload["req_params"]["model"] = self.tts_model

        # additions
        if additions_str:
            payload["req_params"]["additions"] = additions_str

        # 还原模式（仅声音复刻2.0）
        if self.tone_fidelity and self.resource_id == "seed-icl-2.0":
            payload["req_params"]["tone_fidelity"] = True

        # ── 语音指令 context_texts（QA 模式） ──
        # 可用性：TTS 2.0 音色直接支持；ICL 2.0 仅表现力增强版(seed-tts-2.0-expressive)支持。
        # 不支持的情况由 main.py 在调用前判断，不传 context_texts
        if context_texts:
            if self.resource_id == "seed-icl-2.0" and self.tts_model != "seed-tts-2.0-expressive":
                logger.warning(
                    "[豆包语音] 复刻音色标准版不支持 context_texts，已忽略"
                )
            else:
                payload["req_params"]["context_texts"] = [context_texts]

        # ── 多轮上下文 section_id ──
        # 官方文档：TTS 2.0 和声音复刻 2.0 音色都支持
        if section_id:
            payload["req_params"]["section_id"] = section_id

        return payload

    async def synthesize(
        self,
        text: str,
        section_id: str = "",
        context_texts: str = "",
        use_tag_parser: bool = False,
    ) -> Optional[bytes]:
        """
        语音合成（HTTP Chunked 流式，收集全部音频数据后返回）

        Args:
            text: 待合成的文本（Cot 模式下是带 <cot> 标签的文本）
            section_id: 本次合成的多轮上下文 ID（由 main.py 从映射表查出）
            context_texts: QA 模式生成的语音指令句
            use_tag_parser: 是否启用 Cot 语音标签解析（需配合带 <cot> 标签的 text）

        Returns:
            完整的音频字节数据，失败时返回 None
        """
        if not self.api_key:
            logger.error("[豆包语音] 火山引擎 API Key 未配置")
            return None

        if not self.speaker_id:
            logger.error("[豆包语音] 音色 ID (speaker_id) 未配置")
            return None

        if not text or not text.strip():
            logger.warning("[豆包语音] 文本为空，跳过合成")
            return None

        text = text.strip()[:3000]

        try:
            headers = self._build_headers()
            payload = self._build_payload(text, section_id, context_texts, use_tag_parser)

            logger.info(
                f"[豆包语音] 开始合成: resource={self.resource_id}, "
                f"speaker={self.speaker_id}, "
                f"format={self.audio_format}/{self.sample_rate}, "
                f"speech_rate={self.speech_rate}, "
                f"text_len={len(text)}, "
                f"tone_fidelity={self.tone_fidelity}"
            )

            audio_bytes = bytearray()

            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    self.tts_api_url,
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status_code != 200:
                        error_body = await response.aread()
                        logger.error(
                            f"[豆包语音] HTTP {response.status_code}: {error_body.decode('utf-8', errors='replace')[:500]}"
                        )
                        return None

                    # 使用 aiter_lines() 按行读取，避免 TCP chunk 边界切到 JSON 行的中间
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue

                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            # 非 JSON 行（如 chunked 编码的尾部空白），跳过
                            continue

                        code = data.get("code", -1)

                        # 合成结束
                        if code == 20000000:
                            usage = data.get("usage", {})
                            if usage:
                                logger.info(
                                    f"[豆包语音] 合成完成，计费字符: {usage.get('text_words', 'N/A')}"
                                )
                            else:
                                logger.info("[豆包语音] 合成完成")
                            continue

                        # 错误
                        if code != 0:
                            error_msg = data.get("message", "未知错误")
                            logger.error(
                                f"[豆包语音] 合成出错: code={code}, message={error_msg}"
                            )
                            if audio_bytes:
                                logger.warning(
                                    "[豆包语音] 已收到部分音频数据，返回已有数据"
                                )
                                return bytes(audio_bytes)
                            return None

                        # 音频数据
                        b64_data = data.get("data")
                        if b64_data:
                            audio_chunk = base64.b64decode(b64_data)
                            audio_bytes.extend(audio_chunk)

                        # 字幕/时间戳数据
                        sentence = data.get("sentence")
                        if sentence:
                            text_info = sentence.get("text", "")
                            logger.debug(f"[豆包语音] 字幕: {text_info}")

            if len(audio_bytes) > 0:
                logger.info(
                    f"[豆包语音] 合成成功，音频大小: {len(audio_bytes)} bytes ({len(audio_bytes)/1024:.1f} KB)"
                )
                return bytes(audio_bytes)

            logger.error("[豆包语音] 合成返回空数据")
            return None

        except httpx.TimeoutException:
            logger.error("[豆包语音] 请求超时")
            return None
        except Exception as e:
            logger.error(f"[豆包语音] 合成失败: {e}", exc_info=True)
            return None
