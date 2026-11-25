"""메일 발송 경계. EMAIL_MODE로 구현 주입(지금은 콘솔 로깅).

진짜 SMTP/SES는 SmtpEmailSender를 추가하고 factory에 한 줄 더하면 된다.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailSender(Protocol):
    async def send(self, to: str, subject: str, body: str) -> None: ...


class ConsoleEmailSender:
    """개발용: 실제 발송 대신 로그로 출력.

    본문에는 비밀번호 재설정 링크(원문 토큰)가 들어가고 로그는 logs/api.log 에 남는다.
    그래서 show_body=False(APP_ENV=prod)면 수신자·제목·본문 길이만 남긴다 — 로그를 읽을 수
    있는 사람이 남의 계정 비밀번호를 바꾸지 못하게. dev 는 링크를 로그에서 바로 쓰도록 본문도 찍는다.
    """

    def __init__(self, show_body: bool) -> None:
        self.show_body = show_body

    async def send(self, to: str, subject: str, body: str) -> None:
        if self.show_body:
            logger.info("[EMAIL] to=%s subject=%s\n%s", to, subject, body)
        else:
            logger.info(
                "[EMAIL] to=%s subject=%s (본문 %d자 생략 — EMAIL_MODE=console 은 실제로 발송하지 않는다)",
                to, subject, len(body),
            )


@lru_cache
def get_email_sender() -> EmailSender:
    mode = settings.email_mode.lower()
    if mode == "console":
        return ConsoleEmailSender(show_body=settings.app_env == "dev")
    # if mode == "smtp": return SmtpEmailSender(...)
    raise ValueError(f"알 수 없는 EMAIL_MODE: {settings.email_mode}")
