"""학습 judge v2 (설계 문서 §10, main acaf169) — 주장(새 구간의 원문 문장) ↔ 검색 맥락(앞선 원문 조각) 단위 판정.

v1(Weave 추출 가설 × 원장 전제 쌍)과 독립이다. Weave 추출·원장을 쓰지 않는다.
- text:     문장 분리(따옴표 대사 포함)·문단 조각(약 300자)
- retrieve: 문자 2-gram BM25 + 인명 겹침 가산(상수 고정)
- render:   (맥락=전제, 주장=가설) 쌍 입력(k ≤ 4, 512토큰, truncation="only_first")
- scene:    장면 항목 구성·장면 점수(문장 max)
"""
from app.pipeline.judge_v2.render import CONTEXT_FIRST, MAX_LENGTH, render_ids, render_text
from app.pipeline.judge_v2.retrieve import Retriever, guess_names
from app.pipeline.judge_v2.scene import SceneItem, build_scene, scene_score
from app.pipeline.judge_v2.text import chunk_prefix, split_paragraphs, split_sentences

__all__ = ["CONTEXT_FIRST", "MAX_LENGTH", "Retriever", "SceneItem", "build_scene", "chunk_prefix", "guess_names",
           "render_ids", "render_text", "scene_score", "split_paragraphs", "split_sentences"]
