"""按需语音回复插件。

默认所有回复都是纯文字；只有当“这条消息”命中了触发词或正则时，
这一条回复才会带语音（dual_output 开启时文字 + 语音一起发）。

实现：在每次 LLM 请求前，按当前消息内容把本会话的 tts_enabled 置为 True/False，
ResultDecorateStage 里的 TTS 判定随后读取这个值。
"""

import re
from typing import Any, Mapping

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.session_llm_manager import SessionServiceManager

PLUGIN_NAME = "tts_on_demand"
PLUGIN_VERSION = "1.1.0"
PLUGIN_DESC = "平时纯文字，命中触发词的那条消息才回复语音"

DEFAULT_TRIGGER_WORDS = [
    "语音",
    "发语音",
    "说给我听",
    "读出来",
    "念出来",
    "朗读",
    "用声音",
]


@register(PLUGIN_NAME, "liyueovo", PLUGIN_DESC, PLUGIN_VERSION)
class TTSOnDemand(Star):
    def __init__(self, context: Context, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(context)
        self.config: Mapping[str, Any] = config if config is not None else {}

    # ------------------------------------------------------------ 配置读取

    def _enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    def _case_sensitive(self) -> bool:
        return bool(self.config.get("case_sensitive", False))

    def _trigger_words(self) -> list[str]:
        words = self.config.get("trigger_words")
        if not isinstance(words, list) or not words:
            words = DEFAULT_TRIGGER_WORDS
        out = []
        for w in words:
            s = str(w).strip()
            if s:
                out.append(s)
        return out

    def _regex_pattern(self) -> str:
        return str(self.config.get("regex_pattern") or "").strip()

    # ------------------------------------------------------------ 判定

    def match(self, text: str) -> tuple[bool, str]:
        """返回 (是否命中, 命中的规则描述)。"""
        if not text:
            return False, ""

        hay = text if self._case_sensitive() else text.lower()

        for word in self._trigger_words():
            needle = word if self._case_sensitive() else word.lower()
            if needle in hay:
                return True, f"关键词「{word}」"

        pattern = self._regex_pattern()
        if pattern:
            flags = 0 if self._case_sensitive() else re.IGNORECASE
            try:
                if re.search(pattern, text, flags):
                    return True, f"正则 /{pattern}/"
            except re.error as e:
                logger.warning(f"[{PLUGIN_NAME}] 正则无效，已忽略：{pattern}（{e}）")

        return False, ""

    @filter.on_llm_request()
    async def mark_tts(self, event: AstrMessageEvent, *_args, **_kwargs):
        text = (event.message_str or "").strip()

        if not self._enabled():
            await self._apply(event, False)
            return

        hit, reason = self.match(text)
        await self._apply(event, hit)

        if hit and bool(self.config.get("log_hit", True)):
            logger.info(f"[{PLUGIN_NAME}] 本条消息启用语音（{reason}）：{text[:40]}")

    async def _apply(self, event: AstrMessageEvent, enabled: bool) -> None:
        try:
            await SessionServiceManager.set_tts_status_for_session(
                event.unified_msg_origin, enabled
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] 设置 TTS 状态失败：{e}")
