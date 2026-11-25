"""라우터 공통 업로드 처리: 상한까지만 읽기, 이미지 바이트 확인.

회차 원고(chapters.py)와 프로젝트 썸네일(projects.py)이 함께 쓴다.
HTTP 경계의 입력 검증이라 api 레이어에 둔다(비즈니스 로직 아님).
"""
from __future__ import annotations

from fastapi import UploadFile

from app.core.exceptions import DomainError


def _human_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n // (1024 * 1024)}MB"
    if n >= 1024:
        return f"{n // 1024}KB"
    return f"{n}B"


async def read_upload_limited(upload: UploadFile, limit: int) -> bytes:
    """업로드 파일을 limit 바이트까지만 읽는다 — 초과 시 400 (메모리 소진 방지)."""
    content = await upload.read(limit + 1)
    if len(content) > limit:
        raise DomainError(f"파일이 너무 큽니다. 최대 {_human_size(limit)}까지 올릴 수 있습니다.")
    return content


def image_ext(data: bytes) -> str | None:
    """실제 바이트가 PNG/JPEG/GIF/WebP 면 그 확장자, 아니면 None (Content-Type·파일명은 믿지 않는다).

    저장 파일명의 확장자는 반드시 이 값으로 정한다 — 사용자 파일명(x.html 등)을 쓰면
    /uploads 정적 서빙이 확장자로 text/html 을 붙여 같은 오리진에서 스크립트가 돈다.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None
