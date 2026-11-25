"""PIPELINE_MODE 설정으로 파이프라인 구현을 주입 (CLAUDE.md 불변원칙 1).

서비스 코드는 get_pipeline()이 돌려주는 AnalysisPipeline 추상에만 의존한다.
"""
from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.pipeline.interface import AnalysisPipeline


@lru_cache
def get_judge_v2():  # noqa: ANN201 - judge_v2.service.JudgeV2 | None (꺼져 있으면 None, 모듈도 읽지 않는다)
    """학습 judge v2 판정기(설정 JUDGE_V2_ENABLED). 꺼져 있거나 모델 폴더가 없으면 None — 검수 출력은 그대로다."""
    if not settings.judge_v2_enabled or not settings.judge_v2_model_dir:
        return None
    from app.pipeline.judge_v2.hf import HFScorer, read_meta
    from app.pipeline.judge_v2.service import JudgeV2

    meta = read_meta(settings.judge_v2_model_dir)
    thr = settings.judge_v2_threshold if settings.judge_v2_threshold is not None else meta.get("threshold")
    if thr is None:
        raise ValueError("JUDGE_V2 기준값이 없다(JUDGE_V2_THRESHOLD 또는 모델 v2_meta.json threshold)")
    scorer = HFScorer(settings.judge_v2_model_dir, device=settings.judge_v2_device,
                      temperature=settings.judge_v2_temperature)
    return JudgeV2(scorer, float(thr), max_items=settings.judge_v2_max_items)


@lru_cache
def get_pipeline() -> AnalysisPipeline:
    mode = settings.pipeline_mode.lower()
    if mode == "fake":
        from app.pipeline.fake import FakePipeline

        return FakePipeline()
    if mode == "weave":
        from app.pipeline.weave import WeavePipeline

        return WeavePipeline()
    if mode == "remote":
        from app.pipeline.remote import RemotePipeline

        return RemotePipeline(
            settings.pipeline_remote_url,
            timeout_seconds=settings.pipeline_timeout_seconds,
            allow_echo=settings.pipeline_allow_echo,
            token=settings.pipeline_remote_token,
        )
    if mode == "local":
        from app.pipeline.local import LocalPipeline

        return LocalPipeline()
    if mode == "replay":
        from app.pipeline.replay import ReplayPipeline

        return ReplayPipeline(delay_seconds=settings.replay_delay_seconds, story=settings.replay_story)
    raise ValueError(f"알 수 없는 PIPELINE_MODE: {settings.pipeline_mode}")
