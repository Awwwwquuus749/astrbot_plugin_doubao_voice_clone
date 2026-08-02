"""
文本过滤器 - 纯本地处理，替换/删除指定字符，支持括号内容过滤
无需调用任何 API，零延迟
"""

import re
from astrbot.api import logger

# 括号正则：匹配中英文圆括号（）() 和方括号【】[] 的内容
_BRACKET_RE = re.compile(
    r"[（(][^）)]*?[）)]"       # （）和 ()
    r"|"
    r"[【\[][^】\]]*?[】\]]"     # 【】和 []
)


class TextFilter:
    """文本过滤器，根据用户配置的过滤词列表对文本进行替换"""

    def __init__(
        self,
        filter_chars: str,
        filter_replacement: str = "",
        remove_brackets: bool = False,
    ):
        self.replacement = filter_replacement or ""
        self._patterns = self._parse_filters(filter_chars)
        self._remove_brackets = remove_brackets

    def _parse_filters(self, filter_chars: str) -> list[str]:
        """解析过滤词配置为列表"""
        if not filter_chars or not filter_chars.strip():
            return []

        raw_list = re.split(r"[，,、\s\n]+", filter_chars.strip())
        patterns = list(dict.fromkeys([p.strip() for p in raw_list if p.strip()]))
        if patterns:
            logger.info(f"[豆包语音插件] [过滤器] 已加载 {len(patterns)} 个过滤词: {patterns}")
        return patterns

    @property
    def enabled(self) -> bool:
        """过滤器是否启用"""
        return len(self._patterns) > 0 or self._remove_brackets

    @property
    def brackets_enabled(self) -> bool:
        """括号过滤是否启用"""
        return self._remove_brackets

    def filter(self, text: str) -> str:
        """对文本进行过滤处理"""
        if not text:
            return text

        # 步骤 1：去除括号内容
        if self._remove_brackets:
            before_len = len(text)
            text = _BRACKET_RE.sub("", text)
            removed = before_len - len(text)
            if removed > 0:
                text = re.sub(r"\s{2,}", " ", text)
                text = re.sub(r"\s+([，。！？；：、])", r"\1", text)
                text = text.strip()
                logger.info(f"[豆包语音插件] [过滤器] 括号过滤: 移除 {removed} 个字符")

        # 步骤 2：过滤词替换
        if not self._patterns or not text:
            return text

        total_replaced = 0
        for pattern in self._patterns:
            count = text.count(pattern)
            if count > 0:
                total_replaced += count
                text = text.replace(pattern, self.replacement)

        text = re.sub(r"\s+", " ", text).strip()

        if total_replaced > 0:
            logger.info(f"[豆包语音插件] [过滤器] 过滤词替换: {total_replaced} 处")

        return text
