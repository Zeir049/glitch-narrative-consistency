"""타이타닉 평가셋(M3) 구축 도구 — 파싱·워크시트·형식 점검·코퍼스 조립·봉인.

원문(저작권 있는 각본의 번역)과 산출물은 모두 저장소 밖(`--root`, 기본 C:\\judge_models\\M3)에만 둔다.
이 파일에는 원문 텍스트가 없다.

하위 명령:
  prepare   (A) 원문 복사+sha256, 사건 파싱, episodes_src, canon_index, seed_sheet, 레지스트리 초안
  check     (B) 워크시트 형식 점검(기계적으로만) → validation_report.md, 통과하면 scenes·labels 생성
  seal      (E) 모든 파일 sha256 + 봉인 시각 + 사전 확정 커밋 해시 → SEAL.json

Claude(어떤 세션도)는 모순·일관 수정의 내용을 쓰지 않는다. check 는 형식만 본다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import stat
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path(r"C:\judge_models\M3")
SRC_NAME = "TITANIC_사건ID별_원문.txt"
N_CANON = 48            # 사건 000–047 = 원장
N_TOTAL = 72            # 사건 000–071
EVENTS_PER_EPISODE = 12
SEED = "M3:titanic:20261108"
WRAP_MIN = 44           # 이 길이 이상이고 종결 부호가 없으면 PDF 줄바꿈으로 본다
TERM = set(".?!…\"”'’)」』-—–~:;")
CLOSERS = "\"”’')」』"

TYPES = ("상태", "지식", "설정")
PREMISES = ("단일", "다중")


# ───────────────────────── 파싱 ─────────────────────────

HEADER_RE = re.compile(
    r"^={10,}\n1_EVENT_(\d{3}) \| (.*)\nPDF 페이지: ([^\n]*)\n={10,}\n", re.M)
PDF_MARK_RE = re.compile(r"^\[PDF (\d+)페이지\]$")
PAGE_HEAD_RE = re.compile(r"^타이타닉\s+(\d+)(?:\s+(.*))?$")
LOC_HEAD_RE = re.compile(r"^(?:\d{1,3}[A-Z]{0,2}\s+)?(?:실내|야외|EXT\b|INT\b)")
NUM_HEAD_RE = re.compile(r"^\d{1,3}[A-Z]{0,2}\s+\S.{0,60}$")   # '번호 + 짧은 줄' (끝에 종결 부호 없음은 classify 에서 확인)


@dataclass
class Unit:
    kind: str          # heading | sentence | label | transition
    text: str
    para: int
    num: int | None = None


@dataclass
class Event:
    eid: int
    title: str
    pages: str
    paragraphs: list[str] = field(default_factory=list)
    units: list[Unit] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return f"EVENT_{self.eid:03d}"

    def text(self) -> str:
        return "\n".join(self.paragraphs)

    def numbered(self) -> list[Unit]:
        return [u for u in self.units if u.num is not None]


def split_events(raw: str) -> list[tuple[int, str, str, str]]:
    ms = list(HEADER_RE.finditer(raw))
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(raw)
        out.append((int(m.group(1)), m.group(2).strip(), m.group(3).strip(), raw[m.end():end]))
    return out


def clean_lines(body: str, first_event: bool) -> tuple[list[str], dict]:
    """제거 규칙: PDF 표지·`[PDF n페이지]`·쪽번호 줄(현재 쪽과 같은 정수)·`타이타닉 n` 각본 쪽번호 머리말(PDF 쪽보다 1~25 작을 때, 뒤에 붙은 본문은 남김)."""
    st = {"pdf_marks": 0, "page_numbers": 0, "page_heads": 0, "cover": 0, "page_head_mismatch": 0}
    page = None
    out: list[str] = []
    cover_pending = first_event
    for ln in body.split("\n"):
        s = ln.strip()
        if not s:
            continue
        m = PDF_MARK_RE.match(s)
        if m:
            page = int(m.group(1))
            st["pdf_marks"] += 1
            continue
        if cover_pending and page == 1 and s in ("타이타닉", "제임스 카메론 각본"):
            st["cover"] += 1
            continue
        if re.fullmatch(r"\d{1,3}", s) and page is not None and int(s) == page:
            st["page_numbers"] += 1
            continue
        m = PAGE_HEAD_RE.match(s)
        if m:
            if page is not None and 0 < page - int(m.group(1)) <= 25:   # 각본 자체의 쪽번호(PDF 쪽보다 10~17 작다)
                st["page_heads"] += 1
                rest = (m.group(2) or "").strip()
                if rest:
                    out.append(rest)
                continue
            st["page_head_mismatch"] += 1
        out.append(s)
    return out, st


def _continues(prev: str) -> bool:
    """prev 줄 뒤의 줄이 같은 문단의 PDF 줄바꿈 연속인가."""
    if prev.startswith("(") and prev.endswith(")"):
        return False
    if prev[-1] in TERM:
        return False
    return len(prev) >= WRAP_MIN or prev.endswith(",")


def build_paragraphs(lines: list[str]) -> list[str]:
    paras: list[str] = []
    cur = ""
    last = ""
    for s in lines:
        if cur and _continues(last):
            # 줄바꿈은 공백 없이 잇는다(PDF 자동 줄바꿈이 어절 중간에서도 끊기므로). 쉼표로 끝난 줄만 공백을 넣는다.
            cur += (" " if last.endswith(",") else "") + s
        else:
            if cur:
                paras.append(cur)
            cur = s
        last = s
    if cur:
        paras.append(cur)
    return [re.sub(r"\s+", " ", p).strip() for p in paras]


def split_sentences(p: str) -> list[str]:
    """. ? ! 뒤 공백에서 끊는다. 말줄임표(연속 마침표)·숫자 속 마침표·약어 속 마침표는 끊지 않는다."""
    out, start, i, n = [], 0, 0, len(p)
    while i < n:
        c = p[i]
        if c in ".?!":
            if c == "." and ((i > 0 and p[i - 1] == ".") or (i + 1 < n and p[i + 1] == ".")):
                i += 1
                continue
            j = i + 1
            while j < n and p[j] in "?!":
                j += 1
            while j < n and p[j] in CLOSERS:
                j += 1
            if j >= n or p[j].isspace():
                seg = p[start:j].strip()
                if seg:
                    out.append(seg)
                start = j
                i = j
                continue
        i += 1
    tail = p[start:].strip()
    if tail:
        out.append(tail)
    return out


def classify_paragraph(p: str) -> str:
    if LOC_HEAD_RE.match(p) and len(p) <= 90:
        return "heading"
    if NUM_HEAD_RE.match(p) and p[-1] not in TERM:
        return "heading"
    if p.endswith(":") and len(p) <= 15:
        return "transition"
    if len(p) <= 24 and p[-1] not in TERM and not p.startswith("("):
        return "label"
    return "sentence"


def units_for(paragraphs: list[str]) -> list[Unit]:
    units: list[Unit] = []
    n = 0
    for pi, p in enumerate(paragraphs):
        kind = classify_paragraph(p)
        if kind in ("label", "transition"):
            units.append(Unit(kind, p, pi))
        elif kind == "heading":
            n += 1
            units.append(Unit("heading", p, pi, n))
        else:
            for s in split_sentences(p):
                n += 1
                units.append(Unit("sentence", s, pi, n))
    return units


def parse_source(raw: str) -> list[Event]:
    evs = []
    for k, (eid, title, pages, body) in enumerate(split_events(raw)):
        lines, st = clean_lines(body, first_event=(k == 0))
        paras = build_paragraphs(lines)
        ev = Event(eid, title, pages, paras, units_for(paras), st)
        evs.append(ev)
    return evs


def check_event_ids(evs: list[Event]) -> list[str]:
    errs = []
    if [e.eid for e in evs] != list(range(N_TOTAL)):
        errs.append(f"사건 id 가 000–{N_TOTAL - 1:03d} 연속이 아니다: {[e.eid for e in evs][:5]}…(총 {len(evs)})")
    return errs


def episode_of(eid: int) -> int:
    return eid // EVENTS_PER_EPISODE + 1


def episode_texts(evs: list[Event]) -> dict[int, str]:
    """화 원문. 문단 사이를 빈 줄로 띄운다 — 추출 청크 분할기(chunk_sources, C=2500)가 빈 줄 경계로만 자르기 때문."""
    out: dict[int, list[str]] = {}
    for e in evs[:N_CANON]:
        out.setdefault(episode_of(e.eid), []).extend(e.paragraphs)
    return {k: "\n\n".join(v) + "\n" for k, v in out.items()}

# ───────────────────────── 레지스트리 초안 ─────────────────────────

def _label_name(label: str) -> str:
    s = re.sub(r"\([^)]*\)", "", label)
    s = re.sub(r"#\s*\d+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _loc_names(heading: str) -> list[str]:
    s = re.sub(r"^\d{1,3}[A-Z]{0,2}\s+", "", heading)
    s = re.sub(r"^(?:(?:실내|야외|EXT\.?|INT\.?)\s*/?\s*)+\.?\s*", "", s)
    if " - " in s:
        head, tail = s.rsplit(" - ", 1)
        if len(tail) <= 8:
            s = head
    names = []
    for part in re.split(r"[/,]", s):
        part = part.strip(" .")
        if 1 < len(part) <= 30:
            names.append(part)
    return names


def registry_draft(evs: list[Event]) -> dict:
    """원장 사건(000–047)에서 이름만 뽑는다. 사실 서술(note)은 넣지 않는다."""
    chars: dict[str, int] = {}
    locs: dict[str, int] = {}
    for e in evs[:N_CANON]:
        ep = episode_of(e.eid)
        for u in e.units:
            if u.kind == "label":
                for nm in (x.strip() for x in _label_name(u.text).split("/")):
                    if len(nm) >= 2 and not re.search(r"[\d,.:]", nm) and nm not in ("장면 전환", "화면 전환") and nm not in chars:
                        chars[nm] = ep
            elif u.kind == "heading":
                for nm in _loc_names(u.text):
                    locs.setdefault(nm, ep)
    return {
        "description": "타이타닉(M3) 추출용 엔티티 레지스트리 — 초안. 인물은 사건 000–047의 화자 표기, 장소는 장면 제목에서 이름만 자동 추출했다. "
                       "사실 서술(note)은 넣지 않았다. 사람이 별칭·중복을 정리한다.",
        "schema_version": 2,
        "work_id": "titanic_m3",
        "last_episode": 0,
        "characters": [{"id": f"CHAR_{i:03d}", "name": n, "entity_type": "person", "first_episode": ep}
                       for i, (n, ep) in enumerate(chars.items(), 1)],
        "locations": [{"id": f"LOC_{i:03d}", "name": n, "first_episode": ep}
                      for i, (n, ep) in enumerate(locs.items(), 1)],
        "objects": [],
        "times": [],
    }


# ───────────────────────── 산출물 쓰기 ─────────────────────────

def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def render_units(e: Event, with_nums: bool = True) -> str:
    lines = []
    for u in e.units:
        if u.num is not None:
            lines.append(f"[{u.num}] {u.text}")
        elif u.kind == "label":
            lines.append(f"      ({u.text})")
        else:
            lines.append(f"      · {u.text}")
    return "\n".join(lines)


def render_canon_index(evs: list[Event]) -> str:
    out = ["# 원장 색인 — 사건 000–047 (사용자가 충돌 근거를 찾는 용도)", "",
           "화 구성: 화당 12사건 — 1화 000–011, 2화 012–023, 3화 024–035, 4화 036–047.",
           "문장 번호는 사건마다 1부터. `(괄호)`·`·` 줄은 화자 표기·장면 전환 같은 구조 줄이라 번호가 없다.",
           "**충돌 근거 인용은 이 사건 안의 글자 그대로 복사한다.** (점검은 공백 차이를 무시한다.)", ""]
    for e in evs[:N_CANON]:
        out += [f"## {e.name} | {e.title}   (화{episode_of(e.eid)}, 문장 {len(e.numbered())}개)", "",
                render_units(e), ""]
    return "\n".join(out)


GUIDE = """# 시드 워크시트 — 대상 사건 048–071 (사용자가 직접 채운다)

> Claude 는 이 칸의 내용을 쓰거나 제안하거나 고치지 않는다. 점검은 형식만 본다.

## 작성 안내

사건 048–071 각각에 장면 2개를 만든다. 사건마다 **[P]** 하나, **[N]** 하나. 합계 48장면(P 24 + N 24).

- **[P] 모순 장면:** 그 사건의 문장 **하나**를 바꿔서, **사건 000–047의 사실과 충돌**하게 만든다.
- **[N] 정상 장면:** 같은 사건의 문장 **하나**를 **모순 없이** 바꾼다. P와 비슷한 크기의 수정으로 한다.
  (고친 흔적만으로 P/N을 가리지 못하게 하려는 것이다.)
- 충돌의 근거는 **반드시 사건 000–047 안**에 있어야 하고, 000–047만 보고 판단할 수 있어야 한다.
  048–071 안의 다른 문장과의 충돌은 근거로 쓰지 않는다.
- 부정어만 붙이는 변형("~했다" → "~하지 않았다")은 피한다.
- 분포 목표: 유형은 대략 **상태 8 · 지식 8 · 설정 8**, **단일 전제형 16개 이상**(P 24개 중).
  - 상태 = 인물·사물·장소의 위치·소지·관계·몸 상태가 바뀌어 충돌.
  - 지식 = 누가 무엇을 아는지·모르는지·언제 알았는지가 충돌.
  - 설정 = 이름·나이·직업·시기·규칙처럼 고정된 설정이 충돌.
  - 단일 = 근거 인용 하나로 충돌이 성립. 다중 = 둘 이상의 근거를 합쳐야 성립.
  - (정의는 점검에 쓰지 않는다. 점검은 칸이 허용값인지만 본다.)

### 칸 형식 (기계가 읽는다 — 줄 머리 `- 항목:` 을 지운 채 쓰지 말 것)

| 칸 | 값 |
|---|---|
| 바꿀 문장 번호 | 위 문장 목록의 번호 하나(정수). 화자·전환 줄은 번호가 없어 고를 수 없다 |
| 바꾼 문장 | 그 번호의 문장을 **통째로** 대체할 한 문장. 한 줄로 쓴다 |
| 충돌 근거 | 줄마다 `EVENT_번호`, 세로선, 인용 순서(아래 예시)로 쓰고 여러 줄 가능. 사건 id 는 000–047, 인용은 그 사건에서 글자 그대로 |
| 유형 | `상태` / `지식` / `설정` 중 하나 |
| 단일·다중 | `단일` / `다중` 중 하나 |
| 한 줄 설명 | 왜 충돌인지 한 줄 |

### 형식 예시 (가상의 이야기 — 이 작품과 무관)

```
- 바꿀 문장 번호: 4
- 바꾼 문장: 한나는 우체국 앞에서 오랜 친구를 만났다.
- 충돌 근거:
  - EVENT_007 | 가상의 인용 한 줄
- 유형: 상태
- 단일·다중: 단일
- 한 줄 설명: 가상의 설명 한 줄
```

---
"""

FIELDS_P = ["바꿀 문장 번호", "바꾼 문장", "충돌 근거", "유형", "단일·다중", "한 줄 설명"]
FIELDS_N = ["바꿀 문장 번호", "바꾼 문장"]


def render_seed_sheet(evs: list[Event]) -> str:
    out = [GUIDE]
    for e in evs[N_CANON:]:
        out += [f"## {e.name} | {e.title}", "", "### 문장 목록", "", "```", render_units(e), "```", "",
                "### [P] 모순 장면", "",
                "- 바꿀 문장 번호:", "- 바꾼 문장:", "- 충돌 근거:", "  - ", "  - ",
                "- 유형:", "- 단일·다중:", "- 한 줄 설명:", "",
                "### [N] 정상 장면", "",
                "- 바꿀 문장 번호:", "- 바꾼 문장:", "", "---", ""]
    return "\n".join(out)


def cmd_prepare(a: argparse.Namespace) -> int:
    root: Path = a.root
    src_dir, work, tit = root / "source", root / "work", root / "titanic"
    for d in (src_dir, work, tit / "episodes_src", tit / "scenes", tit / "ledger_nemotron_ultra" / "scene_events"):
        d.mkdir(parents=True, exist_ok=True)

    dst = src_dir / SRC_NAME
    if not dst.exists():
        shutil.copyfile(a.src, dst)
        os.chmod(dst, stat.S_IREAD)
    digest = sha256_file(dst)
    if digest != sha256_file(a.src):
        print("원본과 복사본 sha256 불일치", file=sys.stderr)
        return 2
    (src_dir / (SRC_NAME + ".sha256")).write_text(f"{digest}  {SRC_NAME}\n", encoding="utf-8")

    raw = dst.read_text(encoding="utf-8")
    evs = parse_source(raw)
    errs = check_event_ids(evs)
    if errs:
        print("\n".join(errs), file=sys.stderr)
        return 2

    ep = episode_texts(evs)
    for k, t in ep.items():
        (tit / "episodes_src" / f"episode_{k:02d}.txt").write_text(t, encoding="utf-8")
    (work / "canon_index.md").write_text(render_canon_index(evs), encoding="utf-8")

    sheet = work / "seed_sheet.md"
    if sheet.exists() and sheet.read_text(encoding="utf-8").strip() and not a.force_sheet:
        print(f"seed_sheet.md 가 이미 있어 덮어쓰지 않았다(사용자 작성분 보호). --force-sheet 로만 덮어쓴다.")
    else:
        sheet.write_text(render_seed_sheet(evs), encoding="utf-8")

    parsed = {"source_sha256": digest, "events": [
        {"id": e.eid, "name": e.name, "title": e.title, "pages": e.pages, "episode": episode_of(e.eid) if e.eid < N_CANON else None,
         "paragraphs": e.paragraphs,
         "units": [{"kind": u.kind, "num": u.num, "para": u.para, "text": u.text} for u in e.units],
         "clean_stats": e.stats} for e in evs]}
    (work / "events_parsed.json").write_text(json.dumps(parsed, ensure_ascii=False, indent=1), encoding="utf-8")

    reg = registry_draft(evs)
    (tit / "entity_registry_titanic.json").write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")

    tot = {k: sum(e.stats[k] for e in evs) for k in evs[0].stats}
    print(json.dumps({"sha256": digest, "events": len(evs), "clean_totals": tot,
                      "units": {k: sum(1 for e in evs for u in e.units if u.kind == k) for k in ("heading", "sentence", "label", "transition")},
                      "target_sentences": sum(len(e.numbered()) for e in evs[N_CANON:]),
                      "registry": {"characters": len(reg["characters"]), "locations": len(reg["locations"])}},
                     ensure_ascii=False))
    return 0


# ───────────────────────── 워크시트 파싱·형식 점검 (B) ─────────────────────────

EVENT_HEAD_RE = re.compile(r"^## EVENT_(\d{3}) \|")
SECTION_RE = re.compile(r"^### \[([PN])\]")
FIELD_RE = re.compile(r"^- ([^:]+):\s*(.*)$")
QUOTE_RE = re.compile(r"^\s+-\s*(?:EVENT_)?(\d{1,3})\s*\\?\|\s*(.+?)\s*$")


def _nospace(s: str) -> str:
    return re.sub(r"\s+", "", s)


def parse_sheet(md: str) -> dict[int, dict[str, dict]]:
    """{event_id: {"P": {칸: 값, "근거": [(id, quote)]}, "N": {...}}}"""
    res: dict[int, dict[str, dict]] = {}
    eid, sec, field_name, in_code = None, None, None, False
    for ln in md.split("\n"):
        if ln.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = EVENT_HEAD_RE.match(ln)
        if m:
            eid, sec, field_name = int(m.group(1)), None, None
            res[eid] = {}
            continue
        m = SECTION_RE.match(ln)
        if m and eid is not None:
            sec, field_name = m.group(1), None
            res[eid][sec] = {"근거": []}
            continue
        if eid is None or sec is None:
            continue
        m = FIELD_RE.match(ln)
        if m:
            field_name = m.group(1).strip()
            res[eid][sec][field_name] = m.group(2).strip()
            continue
        if field_name == "충돌 근거":
            q = QUOTE_RE.match(ln)
            if q:
                res[eid][sec]["근거"].append((int(q.group(1)), q.group(2)))
            elif ln.strip() not in ("", "-"):
                res[eid][sec].setdefault("_unparsed", []).append(ln.strip())
    return res


def scene_text(e: Event, num: int, replacement: str) -> str:
    """번호 num 의 단위를 replacement 로 바꾼 사건 본문(문단은 줄바꿈, 문단 안 문장은 공백)."""
    by_para: dict[int, list[str]] = {}
    for u in e.units:
        t = replacement if u.num == num else u.text
        by_para.setdefault(u.para, []).append(t)
    return "\n".join(" ".join(v) for _, v in sorted(by_para.items())) + "\n"


def validate_sheet(sheet: dict, evs: list[Event]) -> tuple[list[str], list[str], dict]:
    errs: list[str] = []
    warns: list[str] = []
    canon_nospace = {e.eid: _nospace(e.text()) for e in evs[:N_CANON]}
    stats = {"type": {t: 0 for t in TYPES}, "premise": {p: 0 for p in PREMISES}}

    for e in evs[N_CANON:]:
        if e.eid not in sheet:
            errs.append(f"{e.name}: 워크시트에 사건 항목이 없다")
            continue
        secs = sheet[e.eid]
        nums = {u.num: u for u in e.numbered()}
        repl: dict[str, str] = {}
        for sec, fields in (("P", FIELDS_P), ("N", FIELDS_N)):
            tag = f"{e.name} [{sec}]"
            d = secs.get(sec)
            if d is None:
                errs.append(f"{tag}: 구역이 없다")
                continue
            for f in fields:
                if f == "충돌 근거":
                    continue
                if not d.get(f):
                    errs.append(f"{tag}: '{f}' 칸이 비었다")
            n_raw = d.get("바꿀 문장 번호", "")
            num = None
            if n_raw:
                if re.fullmatch(r"\d+", n_raw) and int(n_raw) in nums:
                    num = int(n_raw)
                else:
                    errs.append(f"{tag}: 문장 번호 '{n_raw}' 가 1–{len(nums)} 정수가 아니다")
            new = d.get("바꾼 문장", "")
            if new:
                if "\n" in new:
                    errs.append(f"{tag}: 바꾼 문장이 여러 줄이다")
                if num is not None:
                    orig = nums[num]
                    if _nospace(new) == _nospace(orig.text):
                        errs.append(f"{tag}: 바꾼 문장이 원문과 같다")
                    if orig.kind == "heading":
                        pass  # 장면 제목 단위는 통째로 대체
                    elif len(split_sentences(new)) != 1:
                        errs.append(f"{tag}: 바꾼 문장이 한 문장이 아니다(문장 {len(split_sentences(new))}개로 나뉜다)")
                repl[sec] = new
            if sec == "P":
                ty, pr = d.get("유형", ""), d.get("단일·다중", "")
                if ty and ty not in TYPES:
                    errs.append(f"{tag}: 유형 '{ty}' 는 {'/'.join(TYPES)} 중 하나여야 한다")
                if pr and pr not in PREMISES:
                    errs.append(f"{tag}: 단일·다중 '{pr}' 는 {'/'.join(PREMISES)} 중 하나여야 한다")
                if d.get("_unparsed"):
                    errs.append(f"{tag}: 충돌 근거 줄 형식 오류 → 'EVENT_012 | 인용' 이어야 한다: {d['_unparsed'][:2]}")
                qs = d.get("근거", [])
                if not qs:
                    errs.append(f"{tag}: 충돌 근거가 없다")
                for qid, quote in qs:
                    if qid >= N_CANON:
                        errs.append(f"{tag}: 근거 사건 EVENT_{qid:03d} 은 000–047 밖이다")
                        continue
                    if _nospace(quote) in canon_nospace[qid]:
                        continue
                    where = [f"EVENT_{k:03d}" for k, t in canon_nospace.items() if _nospace(quote) in t]
                    hint = f" (대신 {', '.join(where)} 에는 있다)" if where else " (000–047 어디에도 없다)"
                    errs.append(f"{tag}: EVENT_{qid:03d} 에서 인용을 글자 그대로 찾지 못했다{hint}: «{quote[:30]}…»")
                if ty in TYPES:
                    stats["type"][ty] += 1
                if pr in PREMISES:
                    stats["premise"][pr] += 1
                if pr == "단일" and len(qs) > 1:
                    warns.append(f"{tag}: 단일인데 인용이 {len(qs)}개다(참고)")
                if pr == "다중" and len(qs) < 2:
                    warns.append(f"{tag}: 다중인데 인용이 {len(qs)}개다(참고)")
        if "P" in repl and "N" in repl and _nospace(repl["P"]) == _nospace(repl["N"]):
            errs.append(f"{e.name}: P와 N의 바꾼 문장이 같다")
    for k in sheet:
        if k < N_CANON or k >= N_TOTAL:
            warns.append(f"EVENT_{k:03d}: 대상 사건(048–071) 밖 항목은 무시한다")

    if not errs:
        if stats["premise"]["단일"] < 16:
            warns.append(f"단일 전제 {stats['premise']['단일']}개 — 목표 16개 이상")
        t = stats["type"]
        if max(t.values()) - min(t.values()) > 2:
            warns.append(f"유형 분포 {t} — 목표는 대략 8·8·8")
    return errs, warns, stats


def render_report(errs: list[str], warns: list[str], stats: dict, n_filled: int) -> str:
    out = ["# 형식 점검 결과", "", "기계 점검만 했다(문장 하나 수정인지·인용 글자 일치·필수 칸). 모순이 성립하는지, 내용이 좋은지는 보지 않았다.", "",
           f"- 오류 {len(errs)}건 · 참고 {len(warns)}건 · 점검한 사건 {n_filled}개",
           f"- 유형 분포(P): {stats['type']} · 단일/다중: {stats['premise']}", ""]
    out += ["## 오류 (고쳐서 다시 점검)", ""] + ([f"- {x}" for x in errs] or ["- 없음"]) + [""]
    out += ["## 참고", ""] + ([f"- {x}" for x in warns] or ["- 없음"]) + [""]
    return "\n".join(out)


def build_scene_plan() -> list[tuple[int, str]]:
    """(사건 id, P|N) 48개를 시드 고정으로 섞는다. 반환 순서 = 장면 번호 1..48."""
    items = [(eid, k) for eid in range(N_CANON, N_TOTAL) for k in ("P", "N")]
    random.Random(SEED).shuffle(items)
    return items


def cmd_check(a: argparse.Namespace) -> int:
    root: Path = a.root
    work, tit = root / "work", root / "titanic"
    raw = (root / "source" / SRC_NAME).read_text(encoding="utf-8")
    evs = parse_source(raw)
    sheet = parse_sheet((work / "seed_sheet.md").read_text(encoding="utf-8"))
    errs, warns, stats = validate_sheet(sheet, evs)
    (work / "validation_report.md").write_text(render_report(errs, warns, stats, len(sheet)), encoding="utf-8")
    print(f"errors={len(errs)} warnings={len(warns)} type={stats['type']} premise={stats['premise']}")
    if errs:
        return 1

    plan = build_scene_plan()
    validated, labels = [], {}
    for sn, (eid, kind) in enumerate(plan, 1):
        e = evs[eid]
        d = sheet[eid][kind]
        num = int(d["바꿀 문장 번호"])
        new = d["바꾼 문장"]
        sid = f"scene_{sn:02d}"
        (tit / "scenes" / f"{sid}.txt").write_text(scene_text(e, num, new), encoding="utf-8")
        orig = next(u for u in e.units if u.num == num).text
        validated.append({"scene_id": sid, "event": e.name, "kind": kind, "sentence_num": num,
                          "original": orig, "replacement": new, **({k: d[k] for k in FIELDS_P if k in d and k != "충돌 근거"} if kind == "P" else {}),
                          **({"evidence": d["근거"]} if kind == "P" else {})})
        if kind == "P":
            labels[sid] = {
                "scene_id": sid, "has_contradiction": True, "type": d["유형"], "seeded_text": new,
                "violated_fact": d["한 줄 설명"],
                "evidence_quotes": [{"episode": episode_of(q), "quote": t, "role": "확립 근거"} for q, t in d["근거"]],
                "logic": d["한 줄 설명"], "work_id": "titanic_m3", "authored_by": a.authored_by,
                "notes": f"premise={d['단일·다중']}"}
        else:
            labels[sid] = {
                "scene_id": sid, "has_contradiction": False, "type": "없음", "seeded_text": None,
                "violated_fact": None, "evidence_quotes": [], "logic": None, "work_id": "titanic_m3",
                "authored_by": a.authored_by, "notes": "정상 — 모순 없이 문장 하나 수정"}
    (work / "seed_sheet.validated.json").write_text(json.dumps(
        {"seed": SEED, "scenes": validated, "stats": stats}, ensure_ascii=False, indent=1), encoding="utf-8")
    (tit / "labels.json").write_text(json.dumps(
        {"description": "titanic_m3 평가 코퍼스 라벨 — 사람(사용자)이 모순·정상 수정을 직접 작성. 사건 id·P/N 대응은 work/seed_sheet.validated.json(저장소 밖).",
         "labels": labels}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"scenes+labels 생성: {len(plan)}장면")
    return 0


# ───────────────────────── 봉인 (E) ─────────────────────────

def cmd_seal(a: argparse.Namespace) -> int:
    tit: Path = a.root / "titanic"
    files = {}
    for p in sorted(tit.rglob("*")):
        if p.is_file() and p.name != "SEAL.json":
            files[p.relative_to(tit).as_posix()] = sha256_file(p)
    for extra in ("chunk_map.json", "conditional_events.json"):   # work/ 의 보조 산출물 — 봉인 대상에 함께 넣는다
        f = a.root / "work" / extra
        if f.is_file():
            files[f"../work/{extra}"] = sha256_file(f)
    seal = {"sealed_at": datetime.now(timezone.utc).isoformat(), "pre_commit": a.commit,
            "source_sha256": sha256_file(a.root / "source" / SRC_NAME), "files": files}
    out = tit / "SEAL.json"
    out.write_text(json.dumps(seal, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"SEAL.json sha256={sha256_file(out)} files={len(files)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--src", type=Path, required=True)
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--force-sheet", action="store_true")
    p.set_defaults(fn=cmd_prepare)
    p = sub.add_parser("check")
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--authored-by", default="human", help="labels.json authored_by 값 — 사람이 쓴 경우만 human, 아니면 정확히 기록")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("seal")
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--commit", required=True, help="사전 확정 커밋 해시")
    p.set_defaults(fn=cmd_seal)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
