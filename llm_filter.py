"""
大模型文本过滤模块 - 调用外部 LLM API 对文本进行智能过滤
兼容 OpenAI Chat Completions API 格式
"""

import httpx
from astrbot.api import logger

DEFAULT_LLM_FILTER_SYSTEM_PROMPT = "请把下面的文本中的动作描写全部删掉，不要说多余的话"

_HARD_CONSTRAINT_SUFFIX = (
    "\n\n【输出约束 - 必须严格遵守】\n"
    '1. 你只能从原文中"删除"指定内容，绝对不允许添加、改写、润色、翻译或纠正任何字词。\n'
    "2. 不得改变原文的标点、语气、语序和措辞。\n"
    "3. 输出必须完全是原文删除部分后剩余的文字，不得包含任何解释、说明、前后缀或多余内容。\n"
    "4. 如果没有需要删除的内容，原样输出全文，一个字都不要动。\n"
    "5. 禁止输出原文以外的任何字符。"
)


class LLMFilter:
    """大模型文本过滤器"""

    def __init__(
        self,
        api_base_url: str,
        api_key: str,
        model_name: str,
        system_prompt: str = "",
    ):
        self.api_base_url = (api_base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model_name = model_name or ""
        self.system_prompt = system_prompt.strip() if system_prompt else ""

    @property
    def enabled(self) -> bool:
        return bool(self.api_base_url and self.model_name and self.api_key)

    @property
    def effective_system_prompt(self) -> str:
        base = self.system_prompt or DEFAULT_LLM_FILTER_SYSTEM_PROMPT
        return base + _HARD_CONSTRAINT_SUFFIX

    def _chat_completions_url(self) -> str:
        if self.api_base_url.endswith("/chat/completions"):
            return self.api_base_url
        return f"{self.api_base_url}/chat/completions"

    @staticmethod
    def _validate_output(original: str, filtered: str) -> bool:
        if not filtered:
            return False
        if len(filtered) > len(original) * 1.1:
            return False
        original_chars = set(original)
        new_char_count = sum(
            1 for c in filtered if c not in original_chars and not c.isspace()
        )
        if len(filtered) > 0 and new_char_count / len(filtered) > 0.15:
            return False
        return True

    async def filter(self, text: str) -> str:
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
                        {"role": "system", "content": self.effective_system_prompt},
                        {"role": "user", "content": text[:4096]},
                    ],
                    "max_tokens": min(max(len(text) * 3, 1024), 8192),
                    "temperature": 0,
                }

                resp = await client.post(
                    self._chat_completions_url(), headers=headers, json=payload
                )
                resp.raise_for_status()
                data = resp.json()

                filtered = data["choices"][0]["message"]["content"].strip()
                if not filtered:
                    logger.warning("[豆包语音插件] [大模型过滤] 模型返回空内容，回退原文")
                    return text

                if not self._validate_output(text, filtered):
                    logger.warning(
                        "[豆包语音插件] [大模型过滤] 过滤结果校验未通过，回退原文"
                    )
                    return text

                logger.info(f"[豆包语音插件] [大模型过滤] {len(text)} -> {len(filtered)} 字符")
                return filtered

        except Exception as e:
            logger.error(f"[豆包语音插件] [大模型过滤] API 调用失败，回退原文: {e}")
            return text
