"""한국어 수사·토큰·인용 정규화 — 검사기 공통 도구 (결정적, 외부 의존 없음).

수사 → 숫자 변환은 scripts/probe/number_preservation.py 의 NUM·kor2int 를 옮기고 두 가지를 보탰다:
  · 단독 수사 '하나·둘·셋·넷'(뒤에 한글이 바로 붙지 않을 때. '뱅크 하나 교체' → 1)
  · 엔티티 ID(CHAR_001·OBJ_015·EVENT_3_4 등)는 수치로 보지 않는다(먼저 지운다)
수치 언급(Mention)은 (값, 단위, 주변 내용어)로 다룬다. 같은 수치인지는 값이 같고, 단위가 둘 다 있으면 같고,
주변 내용어(앞뒤 WINDOW 토큰)가 하나 이상 겹칠 때로 본다 — '요금 600원'과 '600원 채무'를 가르기 위한 규칙이다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

ID_RE = re.compile(r"\b(?:CHAR|OBJ|LOC|EVENT|RULE|TIME|FACT)_[A-Za-z0-9_]+|NEW:\S+")
UNITS = ("원", "시", "분", "초", "년", "월", "일", "개", "벌", "명", "번", "회", "째", "배", "리터", "도", "층", "호", "조",
         "킬로미터", "킬로와트", "킬로그램", "킬로", "미터", "킬로리터", "장", "시간", "달", "해", "주", "살", "병", "잔", "그릇", "사람", "곳", "칸", "kW", "kWh", "km",
         "m", "L", "%", "퍼센트", "만", "천", "뱅크", "대", "통", "권", "쪽", "줄", "kg", "g")
# 단위 동의어 표(v3 (b)) — 왼쪽을 오른쪽으로 바꿔 비교한다
UNIT_CANON = {"km": "킬로미터", "킬로": "킬로미터", "kW": "킬로와트", "L": "리터", "m": "미터", "kg": "킬로그램",
              "퍼센트": "%", "사람": "명", "회": "번"}
_UNIT_RE = "|".join(sorted((re.escape(u) for u in UNITS), key=len, reverse=True))
NUM = re.compile(
    # 숫자: 영숫자 식별자 속 숫자(G1·AO-3·R-1·C-4)는 제외(v3 (a))
    r"(?P<n>(?<![A-Za-z0-9０-９])(?<![A-Za-z]-)[0-9０-９]+(?:[.,][0-9]+)*"
    # 한자 수사: 여러 글자면 십·백·천·만 중 하나를 포함해야 하고('일이 일어날'·'이층' 배제),
    # 한 글자면 일·이·사를 빼고 단위 앞에 띄어쓰기가 있어야 한다('사장'·'이 일'·'이도' 배제, '삼 년'·'오 분' 허용)
    r"|(?<![가-힣])(?=[일이삼사오육칠팔구십백천만]*[십백천만])[일이삼사오육칠팔구십백천만]{2,6}"
    r"(?=\s*(?:원|시|분|초|년|월|일|개|벌|명|번|회|째|배|리터|도|층|호|조|킬로|미터|장))"
    r"|(?<![가-힣])[삼오육칠팔구십백천](?=\s+(?:원|시|분|초|년|월|일|개|벌|명|번|회|배|리터|도|층|호|킬로|미터))"
    r"|(?<![가-힣])[십백천](?=(?:원|시|분|초|년|월|일|개|명|번|회|배|리터|도|층))"
    r"|(?<![가-힣])(?:열한|열두|열세|열네|열다섯|열여섯|열일곱|열여덟|열아홉|스물한|스물두|스물세|스물네|스무|서른|마흔|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)(?=\s*(?:개|벌|명|번|시간|시|달|해|주|배|살|장|병|잔|그릇|사람|곳|칸|뱅크|대|통))"
    r"|첫째|둘째|셋째|넷째|다섯째"
    # 단독 수사: 조사 하나가 바로 붙은 꼴도 읽는다(v4 (e) — '하나를'). '둘이서'처럼 조사 뒤에 한글이 더 붙으면 제외
    r"|(?<![가-힣])(?:하나|둘|셋|넷)(?=(?:를|가|는|도|만|씩|을|은|이|와|과|로)?(?![가-힣]))"
    r"|월요일|화요일|수요일|목요일|금요일|토요일|일요일)")
D = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
U = {"십": 10, "백": 100, "천": 1000, "만": 10000}
SMALL = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10, "열한": 11, "열두": 12, "열세": 13, "열네": 14, "열다섯": 15, "열여섯": 16, "열일곱": 17,
         "열여덟": 18, "열아홉": 19, "스무": 20, "스물한": 21, "스물두": 22, "스물세": 23, "스물네": 24, "서른": 30, "마흔": 40,
         "첫째": 1, "둘째": 2, "셋째": 3, "넷째": 4, "다섯째": 5, "하나": 1, "둘": 2, "셋": 3, "넷": 4}
DAY = {"월요일": "mon", "화요일": "tue", "수요일": "wed", "목요일": "thu", "금요일": "fri", "토요일": "sat", "일요일": "sun"}
PARTICLES = ("에서는", "으로는", "에게서", "까지", "부터", "에서", "에게", "으로", "처럼", "보다", "이나", "이고", "이며",
             "이다", "였다", "은", "는", "이", "가", "을", "를", "의", "에", "로", "와", "과", "도", "만", "요", "다")
STOP = {"있음", "없음", "있다", "없다", "상태", "가능", "인지", "알고", "있는", "하는", "하고", "그리고", "이후", "이전", "현재",
        "대한", "관련", "부터", "까지", "이다", "였다", "이었다", "에서", "으로", "정도", "가량", "중", "및", "등", "것", "수", "때", "그", "이", "저", "더", "약", "총", "각", "매", "unknown"}
WINDOW = 2


def kor2int(s: str) -> str:
    if s in SMALL:
        return str(SMALL[s])
    if s in DAY:
        return DAY[s]
    if re.fullmatch(r"[0-9０-９]+(?:[.,][0-9]+)*", s):
        t = s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        if re.fullmatch(r"[0-9]{1,3}(?:,[0-9]{3})+", t):
            t = t.replace(",", "")
        return t.lstrip("0") or "0"
    total = sec = cur = 0          # 만 단위 절(section)마다 십·백·천을 모은다: 삼십오만 → 35 × 10000
    for ch in s:
        if ch in D:
            cur = D[ch]
        elif ch == "만":
            total += ((sec + cur) or 1) * 10000
            sec = cur = 0
        elif ch in U:
            sec += (cur or 1) * U[ch]
            cur = 0
    return str(total + sec + cur)


def strip_ids(s: str) -> str:
    return ID_RE.sub(" ", s or "")


def stem(tok: str) -> str:
    for p in PARTICLES:
        if len(tok) > len(p) + 1 and tok.endswith(p):
            return tok[: -len(p)]
    return tok


_TOK = re.compile(r"[가-힣A-Za-z]{2,}")


def content_tokens(s: str) -> list[str]:
    """내용어(한글·영문 2자 이상, 조사 떼고, 불용어 제외). 순서 보존."""
    out = []
    for m in _TOK.finditer(strip_ids(s)):
        t = stem(m.group())
        if len(t) >= 2 and t not in STOP:
            out.append(t)
    return out


@dataclass(frozen=True)
class Mention:
    value: str
    unit: str
    context: frozenset
    surface: str

    def same(self, other: "Mention") -> bool:
        if self.value != other.value:
            return False
        if self.unit and other.unit and self.unit != other.unit:
            return False
        if not self.context or not other.context:      # 주변 내용어가 없는 쪽이 있으면 값·단위만 본다(단위는 있어야)
            return bool(self.unit and self.unit == other.unit)
        return bool(self.context & other.context)


def mentions(s: str) -> list[Mention]:
    """문자열의 수치 언급 목록(나온 순서). 엔티티 ID 는 먼저 지운다."""
    text = strip_ids(s)
    found = []                                   # (start, unit_end, value, unit)
    for m in NUM.finditer(text):
        if found and m.start() < found[-1][1]:       # 앞 언급의 단위('35만 원'의 만)에 먹힌 자리
            continue
        raw = m.group("n")
        value = kor2int(raw)
        end = m.end()
        um = re.match(r"\s*(" + _UNIT_RE + ")", text[end:])
        unit = um.group(1) if um else ""
        if unit in ("만", "천") and value.isdigit():   # '35만 원' → 350000 원 ('삼십오만 원'과 같게)
            value = str(int(value) * (10000 if unit == "만" else 1000))
            end += um.end()
            um = re.match(r"\s*(" + _UNIT_RE + ")", text[end:])
            unit = um.group(1) if um else ""
        unit_end = end + (um.end() if um else 0)
        unit = UNIT_CANON.get(unit, unit)
        if raw in DAY:
            unit = "요일"
        elif unit == "월" and text[unit_end:unit_end + 2] == "요일":   # '첫째 월요일' — 월은 단위가 아니다
            unit, unit_end = "", end
        found.append((m.start(), unit_end, value, unit))
    for k in range(len(found) - 1):              # 범위 표기 '1~2회' → 앞 끝도 뒤 끝 단위(v3 (b))
        a, b = found[k], found[k + 1]
        if not a[3] and b[3] and re.fullmatch(r"\s*[~～〜-]\s*", text[a[1]:b[0]]):
            found[k] = (a[0], a[1], a[2], b[3])
    found = _clock(text, found)
    masked = list(text)
    for a, b, _, _ in found:
        masked[a:b] = " " * (b - a)
    toks = [(m.start(), m.end(), stem(m.group())) for m in _TOK.finditer("".join(masked))]
    out = []
    for a0, b0, value, unit in found:
        before = [t for a, b, t in toks if b <= a0 and t not in STOP][-WINDOW:]
        after = [t for a, b, t in toks if a >= b0 and t not in STOP][:WINDOW]
        ctx = frozenset(t for t in before + after if len(t) >= 2)
        out.append(Mention(value, unit, ctx, text[a0:b0].strip()))
    return out


def _clock(text: str, found: list) -> list:
    """시각 묶음(v4 (d)): 'N시' + ('M분' | '반') 또는 'H:MM' 을 값 'H:MM', 단위 '시각' 하나로 합친다.
    'N시' 혼자면 'N:00'. '시간'(지속)·'분' 혼자(지속 또는 분)는 그대로 둔다."""
    out, k = [], 0
    while k < len(found):
        a = found[k]
        if a[3] == "시" and a[2].isdigit():
            nxt = found[k + 1] if k + 1 < len(found) else None
            if nxt and nxt[3] == "분" and nxt[2].isdigit() and re.fullmatch(r"\s*", text[a[1]:nxt[0]]):
                out.append((a[0], nxt[1], f"{int(a[2])}:{int(nxt[2]):02d}", "시각"))
                k += 2
                continue
            m = re.match(r"\s*반", text[a[1]:])
            if m:
                out.append((a[0], a[1] + m.end(), f"{int(a[2])}:30", "시각"))
            else:
                out.append((a[0], a[1], f"{int(a[2])}:00", "시각"))
            k += 1
            continue
        if not a[3] and a[2].isdigit() and k + 1 < len(found):
            nxt = found[k + 1]
            if not nxt[3] and nxt[2].isdigit() and text[a[1]:nxt[0]] == ":" and len(text[nxt[0]:nxt[1]]) == 2:
                out.append((a[0], nxt[1], f"{int(a[2])}:{int(nxt[2]):02d}", "시각"))
                k += 2
                continue
        out.append(a)
        k += 1
    return out


def label_text(s: str) -> str:
    """NEW: 라벨 속 글을 읽을 수 있게 편다(v4 (f) — 항목 자신의 구조값에만 쓴다). 'NEW:프리든_4번_방' → '프리든 4번 방'."""
    return re.sub(r"NEW:(\S+)", lambda m: m.group(1).replace("_", " "), s or "")


def carried(m: Mention, pool: list[Mention]) -> bool:
    return any(m.same(p) for p in pool)


# ---------------------------------------------------------------- 인용 정규화 (probe 규약)

WS = re.compile(r"[\s\"'“”‘’「」『』\-—–·,。]+")
ELL = re.compile(r"\.{3,}|…|⋯|\(중략\)|\[중략\]|\s/\s")


def norm(s: str) -> str:
    return WS.sub("", s or "")


def sentences(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group()) for m in re.finditer(r"[^.!?\n]+[.!?]*", text) if m.group().strip()]


def quote_pieces(q: str) -> list[str]:
    """인용을 생략 표시로 쪼개고, 4자 미만 조각은 버린다."""
    return [p for p in ELL.split(q or "") if len(norm(p)) >= 4]
