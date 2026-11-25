"""승격 후보 순위 — priority ∈ {high, mid, low}. 결정적. 사전 확정 규칙(docs/t3_measurement.md v3 절, 측정 전에 기록).

보는 글은 후보의 구조값(assumes 또는 선언 description·trait)뿐이다(인용은 보지 않는다).
  1. low  — 일시성 단어가 있거나, 운반 안 된 수치가 전부 인원수(명·사람)·'한 번' 관용(값 1, 번·회)일 때
  2. high — 그 밖에 지속성 단서: 금액 단위 '원', 지속성 단어, 매체·사물 위치 이유
  3. mid  — 나머지
low 가 high 를 이긴다(남은 시간·경과는 정의상 일시적).
거르지 못하는 것: 단어 목록 밖의 지속성 표현(mid 로 남음), '규정'이 들어간 일시 지시 등 단어 겹침에 의한 오분류.
"""
from __future__ import annotations

from app.pipeline.extract_verifier.kor import mentions

LOW_WORDS = ("잔여", "남은", "남아", "경과", "만에", "동안", "걸려", "걸린", "소요", "오늘", "당일", "지금", "방금", "어젯밤",
             "오늘밤", "이번")
HIGH_WORDS = ("요금", "가격", "비용", "값", "채무", "빚", "계약금", "월급", "규정", "규칙", "운영", "개장", "폐장", "휴무", "영업",
              "매일", "매달", "매주", "정기", "용량", "사양", "지속", "버팀", "버틴", "한계", "정격", "보유", "소지", "재고", "수량")


def priority(cand: dict) -> str:
    text = str(cand.get("value") or "")
    lost = set(cand.get("numbers") or [])
    ms = [m for m in mentions(text) if m.surface in lost] if lost else []
    if any(w in text for w in LOW_WORDS):
        return "low"
    if ms and all(m.unit == "명" or (m.value == "1" and m.unit == "번") for m in ms) and not cand.get("words"):
        return "low"
    why = set(cand.get("why") or [])
    if any(m.unit == "원" for m in ms) or any(w in text for w in HIGH_WORDS) or why & {"medium", "object_location"}:
        return "high"
    return "mid"
