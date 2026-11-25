"""build_titanic_corpus 테스트 — 합성 픽스처만 쓴다(실제 각본 텍스트 없음)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.eval import build_titanic_corpus as B

SEP = "=" * 65


def synth_source(n: int = 72) -> str:
    parts = []
    for i in range(n):
        page = i + 1
        body = [
            f"[PDF {page}페이지]",
            "7 실내. 가상의 방 - 낮" if i % 2 == 0 else "야외. 가상의 마당",
            "가상의 인물이 문을 열고 들어온다. 그는 오래된 상자를 책상 위에 놓는다. 그리고 한참을 아무 말 없이 서",   # 줄바꿈 연속
            "있다.",
            "가나",
            f"이것은 {i}번째 합성 사건의 대사다. 아직 끝나지 않았다...",
            "장면 전환:",
            str(page),
        ]
        if i == 0:
            body.insert(1, "타이타닉")
            body.insert(2, "제임스 카메론 각본")
        if i == 3:
            body.insert(2, f"타이타닉 {page - 2} 머리말 뒤의 본문이다.")
        parts.append(f"{SEP}\n1_EVENT_{i:03d} | 합성 제목 {i}\nPDF 페이지: {page}\n{SEP}\n\n" + "\n".join(body) + "\n\n")
    return "".join(parts)


@pytest.fixture()
def events():
    return B.parse_source(synth_source())


def test_parse_counts_and_cleaning(events):
    assert len(events) == 72 and B.check_event_ids(events) == []
    e0 = events[0]
    assert e0.stats["cover"] == 2 and e0.stats["pdf_marks"] == 1 and e0.stats["page_numbers"] == 1
    assert all("[PDF" not in p for e in events for p in e.paragraphs)
    assert events[3].stats["page_heads"] == 1
    assert any("머리말 뒤의 본문이다." in p for p in events[3].paragraphs)
    assert all("타이타닉" not in p for p in events[0].paragraphs)


def test_wrap_join_no_space_and_comma_space():
    paras = B.build_paragraphs(["가" * 44, "나다.", "라" * 20 + ",", "마바."])
    assert paras[0] == "가" * 44 + "나다."
    assert paras[1] == "라" * 20 + ", 마바."


def test_split_sentences_ellipsis_and_numbers():
    s = B.split_sentences('그는 6.7미터를 걸었다... 그리고 멈췄다. "정말이에요?" 그녀가 물었다.')
    assert s == ['그는 6.7미터를 걸었다... 그리고 멈췄다.', '"정말이에요?"', "그녀가 물었다."]


def test_unit_kinds(events):
    kinds = [u.kind for u in events[0].units]
    assert kinds[0] == "heading" and "label" in kinds and "transition" in kinds
    nums = [u.num for u in events[0].units if u.num is not None]
    assert nums == list(range(1, len(nums) + 1))


def test_episode_grouping(events):
    ep = B.episode_texts(events)
    assert sorted(ep) == [1, 2, 3, 4]
    assert ep[1].count("\n\n") + 1 == sum(len(e.paragraphs) for e in events[:12])   # 문단마다 빈 줄
    assert B.episode_of(0) == 1 and B.episode_of(11) == 1 and B.episode_of(12) == 2 and B.episode_of(47) == 4


def test_scene_plan_is_seeded_and_complete():
    p1, p2 = B.build_scene_plan(), B.build_scene_plan()
    assert p1 == p2 and len(p1) == 48
    assert {(e, k) for e in range(48, 72) for k in "PN"} == set(p1)
    assert p1 != sorted(p1)   # 섞였다


def test_registry_has_names_only(events):
    reg = B.registry_draft(events)
    assert reg["characters"] and reg["locations"]
    assert all("note" not in c for c in reg["characters"] + reg["locations"])


def _sheet_for(events, ok: bool = True) -> str:
    out = []
    for e in events[B.N_CANON:]:
        nums = [u for u in e.numbered() if u.kind == "sentence"]
        quote = events[0].units[3].text if ok else "없는 인용문입니다"
        out += [f"## {e.name} | t", "### [P] 모순 장면", f"- 바꿀 문장 번호: {nums[0].num}", f"- 바꾼 문장: 바뀐 합성 문장 {e.eid}.",
                "- 충돌 근거:", f"  - EVENT_000 | {quote}", "- 유형: 상태", "- 단일·다중: 단일", "- 한 줄 설명: 합성",
                "### [N] 정상 장면", f"- 바꿀 문장 번호: {nums[1].num}", f"- 바꾼 문장: 다른 합성 문장 {e.eid}.", ""]
    return "\n".join(out)


def test_validate_passes_on_wellformed(events):
    sheet = B.parse_sheet(_sheet_for(events))
    errs, warns, stats = B.validate_sheet(sheet, events)
    assert errs == [], errs
    assert stats["premise"]["단일"] == 24 and stats["type"]["상태"] == 24


def test_validate_accepts_escaped_pipe(events):
    sheet = B.parse_sheet(_sheet_for(events).replace("EVENT_000 |", "EVENT_000 \\|"))
    errs, _, _ = B.validate_sheet(sheet, events)
    assert errs == [], errs


def test_validate_catches_format_errors(events):
    sheet = B.parse_sheet(_sheet_for(events, ok=False))
    errs, _, _ = B.validate_sheet(sheet, events)
    assert any("글자 그대로 찾지 못했다" in x for x in errs)

    md = (_sheet_for(events).replace("- 유형: 상태", "- 유형: 엉뚱", 1)
          .replace("- 바꾼 문장: 다른 합성 문장 48.", "- 바꾼 문장: 두 문장이다. 정말로.", 1))
    errs, _, _ = B.validate_sheet(B.parse_sheet(md), events)
    assert any("유형 '엉뚱'" in x for x in errs) and any("한 문장이 아니다" in x for x in errs)


def test_validate_rejects_evidence_outside_canon(events):
    md = _sheet_for(events).replace("EVENT_000 |", "EVENT_050 |", 1)
    errs, _, _ = B.validate_sheet(B.parse_sheet(md), events)
    assert any("000–047 밖" in x for x in errs)


def test_scene_text_replaces_exactly_one_unit(events):
    e = events[60]
    u = [x for x in e.numbered() if x.kind == "sentence"][0]
    out = B.scene_text(e, u.num, "대체 문장이다.")
    assert out.count("대체 문장이다.") == 1 and u.text not in out
    assert out.replace("대체 문장이다.", u.text) == e.text() + "\n"


def test_prepare_and_check_end_to_end(tmp_path: Path):
    src = tmp_path / "in.txt"
    src.write_text(synth_source(), encoding="utf-8")
    root = tmp_path / "M3"
    assert B.main(["prepare", "--src", str(src), "--root", str(root)]) == 0
    assert (root / "source" / (B.SRC_NAME + ".sha256")).exists()
    assert len(list((root / "titanic" / "episodes_src").glob("episode_0*.txt"))) == 4
    sheet = root / "work" / "seed_sheet.md"
    assert "## EVENT_071" in sheet.read_text(encoding="utf-8")
    # 사용자 작성분 보호: 다시 prepare 해도 덮어쓰지 않는다
    sheet.write_text("사용자 작성", encoding="utf-8")
    assert B.main(["prepare", "--src", str(src), "--root", str(root)]) == 0
    assert sheet.read_text(encoding="utf-8") == "사용자 작성"
    # 빈 워크시트는 점검 실패
    sheet.write_text(B.render_seed_sheet(B.parse_source(synth_source())), encoding="utf-8")
    assert B.main(["check", "--root", str(root)]) == 1
    assert "오류" in (root / "work" / "validation_report.md").read_text(encoding="utf-8")
    # 채운 워크시트는 통과 → scenes·labels
    sheet.write_text(_sheet_for(B.parse_source(synth_source())), encoding="utf-8")
    assert B.main(["check", "--root", str(root), "--authored-by", "llm:test"]) == 0
    labels = json.loads((root / "titanic" / "labels.json").read_text(encoding="utf-8"))["labels"]
    assert len(labels) == 48 and sum(v["has_contradiction"] for v in labels.values()) == 24
    assert all(v["authored_by"] == "llm:test" for v in labels.values())
    assert len(list((root / "titanic" / "scenes").glob("scene_*.txt"))) == 48
    assert B.main(["seal", "--root", str(root), "--commit", "abc1234"]) == 0
    seal = json.loads((root / "titanic" / "SEAL.json").read_text(encoding="utf-8"))
    assert seal["pre_commit"] == "abc1234" and "labels.json" in seal["files"]
