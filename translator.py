"""
翻译模块 - 调用外部 LLM API 将文本翻译到目标语种
兼容 OpenAI Chat Completions API 格式
"""

import httpx
from astrbot.api import logger


class Translator:
    """翻译器，通过外部 LLM API 将文本翻译为指定语种"""

    def __init__(
        self,
        api_base_url: str,
        api_key: str,
        model_name: str,
        prompt: str,
    ):
        self.api_base_url = (api_base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model_name = model_name or ""
        self.prompt = prompt or ""

    @property
    def enabled(self) -> bool:
        return bool(self.api_base_url and self.prompt and self.model_name)

    def _chat_completions_url(self) -> str:
        if self.api_base_url.endswith("/chat/completions"):
            return self.api_base_url
        return f"{self.api_base_url}/chat/completions"

    async def translate(self, text: str) -> str:
        if not self.enabled or not text or len(text.strip()) < 2:
            return text

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {"Content-Type": "application/json"}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"

                payload = {
                    "model": self.model_name,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                f"你是一个翻译专家。请将用户输入的文本{self.prompt}，"
                                "只输出翻译结果，不要输出其他任何内容。"
                            ),
                        },
                        {"role": "user", "content": text[:1024]},
                    ],
                    "max_tokens": 1024,
                    "temperature": 0.1,
                }

                resp = await client.post(
                    self._chat_completions_url(), headers=headers, json=payload
                )
                resp.raise_for_status()
                data = resp.json()

                translated = data["choices"][0]["message"]["content"].strip()
                logger.info(f"[豆包语音插件] [翻译] {len(text)} -> {len(translated)} 字符")
                return translated

        except Exception as e:
            logger.error(f"[豆包语音插件] [翻译] 翻译失败，回退原文: {e}")
            return text
