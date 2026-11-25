"""Glitch 시연 영상 자동 녹화 + 점프 컷 편집 (replay 모드 서버, 127.0.0.1:8000, FIREBASE_MODE=fake).

흐름(보통 속도, 기다리는 장면은 점프 컷으로 잘라냄 — 약 1분 30초):
  드라이브에서 새 프로젝트 만들기(이름 타자)
  → 1화: 파일 끌어 넣기 → 분석 시작 → 1차 결과 → 수정하기(타자) → 1화 시각화 5개 탭 → 2차 검토
  → 2화: 파일 끌어 넣기 → 분석 시작 → 2화 1차 결과 → 2화 2차 검토 → 모순 카드 ✓/✕ 판정
  → 전체 시각화 5개 탭(탭마다 1화 부분은 건너뛰고 2화 항목까지 내려감)
  → "다음 회차 분석 시작하기"로 다시 원문 업로드 화면에서 끝(이어서 3화를 넣는 흐름을 설명하기 좋게).
  잘라내는 것: 시작 화면·로그인, 추출 대기, 검수·저장 대기("서버 연결 중" 화면은 잠깐 보여준 뒤 자름).
결과(<출력 폴더>): glitch_demo.mp4(편집본, 1280x720 H.264, 소리 없음) — --with-login 이면 glitch_demo_login.*
                  (시작 화면에서 커서로 "시작하기"를 눌러 들어가는 장면 포함),
                  glitch_demo_raw.webm(원본), glitch_demo_cuts.json(잘라낸 구간)

준비 (프로젝트 .venv 와 별도 가상환경 권장. 브라우저는 설치된 Chrome 을 쓴다):
  py -3.12 -m venv pwenv && pwenv\\Scripts\\pip install playwright httpx imageio-ffmpeg
  pwenv\\Scripts\\python -m playwright install ffmpeg
실행:
  1) 서버: PIPELINE_MODE=replay REPLAY_DELAY_SECONDS=1.5 QUEUE_MODE=inline FIREBASE_MODE=fake 로 uvicorn
     (.claude/launch.json 의 glitch-app-replay-video)
  2) pwenv\\Scripts\\python scripts\\record_demo.py <출력 폴더>
녹화는 녹화 전용 계정(video@glitch.local)으로 하고, 같은 이름의 이전 시연 프로젝트는 지운 뒤 화면에서 새로 만든다.
"""
import argparse
import json
import math
import random
import shutil
import time
from pathlib import Path

import httpx
from playwright.sync_api import Page, sync_playwright

BASE = "http://127.0.0.1:8000"
DATA = Path(r"C:\glitch_integrated\app\pipeline\replay_data")
PROJECT_NAME = "인셉션"
# 녹화 전용 빈 계정(FIREBASE_MODE=fake) — 사이드바에 테스트 프로젝트가 안 보이게.
# 읽기 전용 시연 계정(DEMO_ACCOUNT_EMAIL, 기본 demo@glitch.local)과 겹치면 프로젝트를 만들 수 없다(403).
DEMO_ID_TOKEN = "fake:video@glitch.local:Glitch 녹화"
EDIT_TEXT = "꿈속 금고에서 비밀을 훔쳐 온다."
W, H = 1280, 720

# 영상에 마우스가 안 찍히므로 화살표 커서 + 클릭 표시를 그린다. 파일을 끌 때는 커서 옆에 파일 아이콘이 따라온다.
CURSOR_JS = """
(() => {
  const ARROW = '<svg xmlns="http://www.w3.org/2000/svg" width="22" height="28" viewBox="0 0 22 28">' +
    '<path d="M2 2 L2 22 L7.5 17 L11 25.5 L14.5 24 L11 15.8 L18.5 15.8 Z" fill="#fff" stroke="#111" ' +
    'stroke-width="1.6" stroke-linejoin="round"/></svg>';
  const add = () => {
    if (document.getElementById('__cursor')) return;
    const c = document.createElement('div');
    c.id = '__cursor';
    c.innerHTML = ARROW;
    c.style.cssText = 'position:fixed;left:-50px;top:-50px;width:22px;height:28px;margin:-2px 0 0 -2px;' +
      'z-index:2147483647;pointer-events:none;filter:drop-shadow(0 1px 1.5px rgba(0,0,0,.35));' +
      'transform-origin:2px 2px;transition:transform .08s';
    const g = document.createElement('div');
    g.id = '__drag';
    g.style.cssText = 'position:fixed;left:-300px;top:-300px;display:none;align-items:center;gap:6px;' +
      'padding:6px 10px 6px 8px;background:rgba(255,255,255,.92);border:1px solid #cbd5e1;border-radius:6px;' +
      'box-shadow:0 4px 12px rgba(0,0,0,.18);font:13px "Malgun Gothic",sans-serif;color:#111;' +
      'z-index:2147483646;pointer-events:none;opacity:.95';
    g.innerHTML = '<svg width="18" height="22" viewBox="0 0 18 22"><path d="M1 1h10l6 6v14H1z" fill="#f8fafc" ' +
      'stroke="#64748b"/><path d="M11 1v6h6" fill="none" stroke="#64748b"/>' +
      '<path d="M4 11h9M4 14h9M4 17h6" stroke="#94a3b8"/></svg><span></span>';
    document.body.append(c, g);
    addEventListener('mousemove', e => {
      c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px';
      g.style.left = (e.clientX + 14) + 'px'; g.style.top = (e.clientY + 18) + 'px';
    }, true);
    addEventListener('mousedown', e => {
      c.style.transform = 'scale(.88)';
      const r = document.createElement('div');
      r.style.cssText = `position:fixed;left:${e.clientX}px;top:${e.clientY}px;width:8px;height:8px;` +
        'margin:-4px 0 0 -4px;border-radius:50%;background:rgba(100,116,139,.28);z-index:2147483645;' +
        'pointer-events:none;transition:all .35s ease-out';
      document.body.appendChild(r);
      requestAnimationFrame(() => {
        r.style.width = r.style.height = '34px'; r.style.margin = '-17px 0 0 -17px'; r.style.opacity = '0';
      });
      setTimeout(() => r.remove(), 400);
    }, true);
    addEventListener('mouseup', () => { c.style.transform = ''; }, true);
    window.__dragGhost = (name) => {
      g.querySelector('span').textContent = name || '';
      g.style.display = name ? 'flex' : 'none';
    };
  };
  if (document.body) add(); else addEventListener('DOMContentLoaded', add);
})();
"""


# 사람 손 같은 흔들림 — 매 녹화가 같게 나오도록 시드를 고정한다
RNG = random.Random(20261029)
MOUSE = [W * 0.55, H * 0.45]  # 지금 커서 위치(뷰포트 좌표)


def pause(s: float) -> None:
    time.sleep(s)


def jitter(lo: float, hi: float) -> float:
    return RNG.uniform(lo, hi)


def human_move(page: Page, x: float, y: float) -> None:
    """곡선 경로 + 가속·감속으로 커서를 옮긴다. 먼 거리는 살짝 지나쳤다가 돌아온다."""
    x0, y0 = MOUSE
    dist = math.hypot(x - x0, y - y0)
    if dist < 2:
        return
    # 경로를 옆으로 휘게 하는 제어점
    nx, ny = -(y - y0) / dist, (x - x0) / dist
    bend = jitter(-0.18, 0.18) * dist
    cx, cy = (x0 + x) / 2 + nx * bend, (y0 + y) / 2 + ny * bend
    overshoot = dist > 280 and RNG.random() < 0.45
    if overshoot:
        tx, ty = x + (x - x0) / dist * jitter(6, 14), y + (y - y0) / dist * jitter(4, 10)
    else:
        tx, ty = x, y
    steps = int(min(48, max(10, dist / 14)))
    total = (0.18 + dist / 2000) * jitter(0.85, 1.15)
    for i in range(1, steps + 1):
        t = i / steps
        e = t * t * (3 - 2 * t)  # ease-in-out
        bx = (1 - e) ** 2 * x0 + 2 * (1 - e) * e * cx + e * e * tx
        by = (1 - e) ** 2 * y0 + 2 * (1 - e) * e * cy + e * e * ty
        page.mouse.move(bx, by)
        pause(total / steps)
    if overshoot:
        pause(0.05)
        for i in range(1, 6):
            page.mouse.move(tx + (x - tx) * i / 5, ty + (y - ty) * i / 5)
            pause(0.016)
    MOUSE[0], MOUSE[1] = x, y


def target_point(locator) -> tuple[float, float]:
    """요소 안의 한 점 — 정중앙이 아니라 사람처럼 조금 비껴 누른다."""
    box = locator.bounding_box()
    return box["x"] + box["width"] * jitter(0.38, 0.62), box["y"] + box["height"] * jitter(0.4, 0.6)


def click(page: Page, locator, wait_after: float = 0.35) -> None:
    """커서를 옮겨 잠깐 멈췄다가 클릭."""
    locator.scroll_into_view_if_needed()
    human_move(page, *target_point(locator))
    pause(jitter(0.08, 0.18))
    page.mouse.down()
    time.sleep(jitter(0.06, 0.11))
    page.mouse.up()
    pause(wait_after)


def type_human(page: Page, text: str) -> None:
    """한 글자씩, 간격을 들쭉날쭉하게. 띄어쓰기·문장부호 뒤에는 가끔 숨을 고른다."""
    for ch in text:
        page.keyboard.type(ch)
        d = jitter(0.07, 0.17)
        if ch in " ,." and RNG.random() < 0.35:
            d += jitter(0.15, 0.4)
        pause(d)


def smooth_scroll(page: Page, dy: int, steps: int = 24, dt: float = 0.045) -> None:
    """휠을 굴리듯 조금씩, 속도는 고르지 않게."""
    weights = [jitter(0.6, 1.4) for _ in range(steps)]
    total = sum(weights)
    for w in weights:
        page.mouse.wheel(0, dy * w / total)
        pause(dt * jitter(0.7, 1.4))


def scroll_top(page: Page) -> None:
    page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
    pause(0.3)


DROP_JS = """([sel, files, type]) => {
    const zone = document.querySelector(sel);
    const dt = new DataTransfer();
    for (const f of files) dt.items.add(new File([f.text], f.name, {type: 'text/plain'}));
    zone.dispatchEvent(new DragEvent(type, {bubbles: true, cancelable: true, dataTransfer: dt}));
}"""


def drag_files(page: Page, paths: list[Path], zone_selector: str = "#source-dropzone") -> None:
    """파일을 화면 오른쪽 끝(탐색기 창 쪽)에서 끌고 와 업로드 칸에 놓는다. 여러 개면 한 번에.
    파일은 실제 drop 이벤트(DataTransfer)로 넘긴다."""
    zone = page.locator(zone_selector)
    zone.scroll_into_view_if_needed()
    box = zone.bounding_box()
    # 화면 밖 움직임은 페이지에 전달되지 않아 커서가 순간 이동한 것처럼 찍힌다 —
    # 사람처럼 커서를 오른쪽 끝으로 먼저 가져가고, 거기서 파일을 집어 온다
    human_move(page, W - jitter(4, 9), box["y"] + box["height"] * jitter(0.3, 0.9))
    pause(jitter(0.5, 0.8))
    page.evaluate("name => window.__dragGhost(name)", " · ".join(p.name for p in paths))
    page.mouse.move(MOUSE[0] - 1, MOUSE[1])  # 아이콘을 커서 옆에 바로 붙인다
    pause(0.25)
    files = [{"name": p.name, "text": p.read_text(encoding="utf-8")} for p in paths]
    # 칸 안으로 들어오면 칸이 강조된다(dragenter/dragover)
    human_move(page, box["x"] + box["width"] * 0.82, box["y"] + box["height"] * 0.55)
    for ev in ("dragenter", "dragover"):
        page.evaluate(DROP_JS, [zone_selector, files, ev])
    human_move(page, box["x"] + box["width"] * jitter(0.45, 0.55), box["y"] + box["height"] * jitter(0.45, 0.6))
    page.evaluate(DROP_JS, [zone_selector, files, "dragover"])
    pause(0.45)
    page.evaluate(DROP_JS, [zone_selector, files, "drop"])
    page.evaluate("() => window.__dragGhost(null)")
    pause(0.9)


def wait_status(page: Page, no: int, statuses: tuple[str, ...], timeout: float = 30) -> None:
    end = time.time() + timeout
    while time.time() < end:
        st = page.evaluate(
            """async ([no]) => {
                 const t = sessionStorage.getItem('glitch_backend_token');
                 const pid = window.__pid;
                 const r = await fetch(`/projects/${pid}/chapters/${no}/result`, {headers: {Authorization: 'Bearer ' + t}});
                 return (await r.json()).status;
               }""",
            [no],
        )
        if st in statuses:
            return
        time.sleep(0.2)
    raise TimeoutError(f"{no}화가 {statuses} 상태가 되지 않음")


def delete_old_projects() -> None:
    """이전 녹화에서 만든 같은 이름의 프로젝트를 지운다(프로젝트는 화면에서 새로 만든다)."""
    c = httpx.Client(base_url=BASE, timeout=30)
    tok = c.post("/auth/firebase", json={"id_token": DEMO_ID_TOKEN}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    for p in c.get("/projects", params={"page": 1, "page_size": 50}, headers=h).json()["items"]:
        if p["name"] == PROJECT_NAME:
            c.delete(f"/projects/{p['id']}", headers=h)


class Cuts:
    """점프 컷으로 잘라낼 구간(영상 시각, 초)을 모은다. 녹화가 끝나면 cut_video 가 잘라 붙인다."""

    def __init__(self, t0: float) -> None:
        self.t0 = t0
        self.ranges: list[tuple[float, float]] = []
        self._start: float | None = None

    def now(self) -> float:
        return time.time() - self.t0

    def start(self, back: float = 0.0) -> None:
        """지금부터 자른다. back 초만큼 앞당길 수 있다(클릭 직후 바로 뜨는 화면을 빼려고)."""
        self._start = self.now() - back

    def end(self) -> None:
        assert self._start is not None
        self.ranges.append((self._start, self.now()))
        self._start = None


def tidy_tray(page: Page) -> None:
    """작업 창 정리(끝난 카드 닫기 + 접기). 잘려 나갈 구간 안에서만 부른다 — 영상에 시간이 들지 않게."""
    page.evaluate(
        """() => {
             document.querySelectorAll('.task-card-close').forEach(b => b.click());
             document.querySelector('.task-tray:not(.collapsed) .task-tray-toggle')?.click();
           }"""
    )


def show_connecting(page: Page, hold: float = 0.6) -> None:
    """클릭 뒤 뜨는 "서버 연결 중" 화면을 잠깐 보여준다(먼저 사라지면 그때까지만)."""
    try:
        page.wait_for_selector(".embedding-loader.visible", timeout=3000)
    except Exception:  # noqa: BLE001 - 아주 빨리 끝나 못 잡으면 그냥 넘어간다
        return
    end = time.time() + hold
    while time.time() < end and page.locator(".embedding-loader.visible").count():
        time.sleep(0.05)


def analyze(page: Page, cuts: Cuts, chapters: list[int]) -> None:
    """분석 시작 → (서버 연결·추출 대기는 잘라냄) → 1차 결과 화면."""
    click(page, page.locator("#analyze-btn"), 0)
    show_connecting(page)  # "서버 연결 중"은 잠깐 보여주고, 그 뒤 추출 대기부터 자른다
    cuts.start()
    for no in chapters:
        wait_status(page, no, ("extracted",))
    page.wait_for_function("location.hash === '#result'", timeout=15000)
    page.locator("#tab-extract .extract-item").first.wait_for(timeout=15000)
    tidy_tray(page)
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(0.6)
    cuts.end()


def review(page: Page, cuts: Cuts, no: int) -> None:
    """2차 검토 → (검수·저장 대기는 잘라냄) → 검수 결과."""
    click(page, page.locator('.tab-btn[data-tab="report"]'), 0.6)
    click(page, page.locator("#report-review-btn"), 0)
    show_connecting(page)  # 검토하기도 "서버 연결 중"부터 뜬다 — 잠깐 보여주고 검수 대기부터 자른다
    cuts.start()
    wait_status(page, no, ("saved",))
    page.wait_for_function(
        "document.getElementById('report-review-btn').textContent.includes('다시 검토하기')", timeout=15000
    )
    tidy_tray(page)
    time.sleep(0.6)
    cuts.end()


def edit_first_item(page: Page) -> None:
    """1차 결과 첫 항목(세계관 규칙) 내용을 짧게 고친다."""
    item = page.locator("#tab-extract .extract-item").first
    click(page, item.locator(".extract-item-edit-toggle"), 0.5)
    click(page, item.locator(".extract-item-content"), 0.3)
    page.keyboard.press("Control+A")
    type_human(page, EDIT_TEXT)
    pause(0.5)
    click(page, item.locator(".extract-item-edit-toggle"), 0.6)


def judge_contradictions(page: Page) -> None:
    """모순 카드 판정: 첫 카드는 ✓(모순 맞음), 둘째 카드는 ✕(무시). 누르면 바로 서버에 저장된다."""
    cards = page.locator("#issue-list .issue-card")
    pause(0.4)
    # 버튼이 카드 오른쪽 아래라 화면 맨 아래에 두면 접힌 작업 창 머리글에 가린다 — 가운데로 올린다
    center = "e => e.scrollIntoView({block: 'center', behavior: 'smooth'})"
    cards.nth(0).locator(".issue-confirm-btn").evaluate(center)
    pause(0.4)
    click(page, cards.nth(0).locator(".issue-confirm-btn"), 0.6)
    page.wait_for_function(
        "document.querySelector('#issue-list .issue-card').classList.contains('confirmed')", timeout=10000
    )
    cards.nth(1).locator(".issue-remove").evaluate(center)
    pause(0.4)
    click(page, cards.nth(1).locator(".issue-remove"), 0.6)
    page.wait_for_function(
        "document.querySelectorAll('#issue-list .issue-card')[1].classList.contains('dismissed')", timeout=10000
    )
    pause(0.6)
    scroll_top(page)


def select_round(page: Page, label: str) -> None:
    btn = page.locator("#round-filter-bar-result").get_by_role("button", name=label, exact=True)
    if "active" not in (btn.get_attribute("class") or ""):
        click(page, btn, 0.7)


def visual(page: Page, filter_label: str, category: str, hold: float, scroll: int = 0) -> None:
    select_round(page, filter_label)
    click(page, page.locator(f'.category-btn[data-category="{category}"]'), 0.6)
    page.wait_for_function(
        "!document.getElementById('visual-content').textContent.includes('불러오는 중')", timeout=10000
    )
    if scroll:
        smooth_scroll(page, scroll, steps=20, dt=0.05)
    pause(hold)
    scroll_top(page)


def show_visuals(page: Page, filter_label: str, plan: list[tuple[str, float, int]]) -> None:
    scroll_top(page)
    click(page, page.locator('.tab-btn[data-tab="visual"]'), 0.6)
    for category, hold, scroll in plan:
        visual(page, filter_label, category, hold, scroll)


# 시각화 탭 (복선은 AI 결과에 없어 뺀다): (카테고리, 머무는 시간, 스크롤)
# 1화는 5개 탭을 모두 본다(저장 전 추출 결과, 세계관 첫 카드에 고친 문구). 2화는 분석만 하고
# 마지막 "전체"에서 5개 탭을 다시 본다.
FIRST_CHAPTER_VISUALS = [
    ("worldview", 1.0, 0),     # 첫 카드에 고친 문구
    ("character", 1.0, 420),   # 관계도 → 인물 카드
    ("time", 0.8, 0),
    ("space", 0.7, 0),
    ("event", 0.6, 300),
]
# 전체: (카테고리, 맨 위에서 머무는 시간, 내려가 볼 출처 회차들). 항목은 출처 회차 순이라
# 1화 부분을 건너뛰고 2화 첫 항목으로 내려간다.
FINAL_VISUALS = [
    ("worldview", 0.6, (2,)),
    ("character", 1.2, (2,)),   # 관계도 → "출처 2화 · 버전 2" 카드
    ("time", 0.5, (2,)),
    ("space", 0.5, (2,)),
    ("event", 0.5, (2,)),
]


def scroll_to_chapter(page: Page, chapter: int) -> bool:
    """전체 보기에서 "출처 N화" 항목 중 맨 위 것이 화면 위쪽에 오도록 휠로 내려간다.
    세계관처럼 회차 순으로 정렬되지 않은 탭도 있어 DOM 순서가 아니라 화면 위치로 고른다.
    그 회차 항목이 이미 화면에 보이면 움직이지 않는다. 항목이 없으면 False."""
    top = page.evaluate(
        """(n) => {
             const tops = [...document.querySelectorAll('#visual-content .canon-meta')]
               .filter(e => e.textContent.includes(`출처 ${n}화`))
               .map(e => (e.closest('.issue-card, .timeline-item, .character-card') || e).getBoundingClientRect().top);
             return tops.length ? Math.min(...tops) : null;
           }""",
        chapter,
    )
    if top is None:
        return False
    if 80 < top < H * 0.7:
        return True  # 이미 보인다
    smooth_scroll(page, top - 150, steps=12, dt=0.04)
    return True


def final_visual(page: Page, category: str, hold_top: float, chapters: tuple[int, ...]) -> None:
    select_round(page, "전체")
    click(page, page.locator(f'.category-btn[data-category="{category}"]'), 0.2)
    page.wait_for_function(
        "!document.getElementById('visual-content').textContent.includes('불러오는 중')", timeout=10000
    )
    pause(hold_top)
    for n in chapters:
        if scroll_to_chapter(page, n):
            pause(0.8)
    scroll_top(page)


def cut_video(raw: Path, out: Path, cuts: list[tuple[float, float]]) -> None:
    """잘라낼 구간을 빼고 이어 붙여(점프 컷) H.264 MP4 로 만든다. ffmpeg 는 imageio-ffmpeg 것을 쓴다."""
    import subprocess

    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    keep, t = [], 0.0
    for a, b in sorted(cuts):
        if a > t:
            keep.append((t, a))
        t = max(t, b)
    keep.append((t, None))  # 마지막 구간은 끝까지
    parts = []
    for i, (a, b) in enumerate(keep):
        end = f":end={b:.3f}" if b is not None else ""
        parts.append(f"[0:v]trim=start={a:.3f}{end},setpts=PTS-STARTPTS[v{i}]")
    graph = ";".join(parts) + ";" + "".join(f"[v{i}]" for i in range(len(keep))) + f"concat=n={len(keep)}:v=1:a=0[out]"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw), "-filter_complex", graph,
         "-map", "[out]", "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
         "-r", "25", "-movflags", "+faststart", str(out)],
        check=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Glitch 시연 영상 녹화 (점프 컷)")
    ap.add_argument("out_dir", type=Path, nargs="?", default=Path(__file__).parent / "video")
    ap.add_argument("--with-login", action="store_true",
                    help="시작 화면 → 커서로 '시작하기' → 드라이브 장면을 남긴다(glitch_demo_login.mp4)")
    ap.add_argument("--size", default="1280x720", help="뷰포트이자 영상 크기, 예: 1720x968")
    ap.add_argument("--dark", action="store_true", help="브라우저를 다크 모드로(앱 테마는 시스템 설정을 따른다)")
    ap.add_argument("--display-name", help="왼쪽 아래·작성자에 보일 이름(녹화 화면에서만 바꾼다)")
    ap.add_argument("--display-email", help="왼쪽 아래에 보일 이메일(녹화 화면에서만 바꾼다)")
    ap.add_argument("--account", default="video@glitch.local",
                    help="녹화에 쓰는 백엔드 계정 이메일(FIREBASE_MODE=fake). 시연 계정과 겹치면 안 된다")
    ap.add_argument("--name", help="출력 파일 이름(확장자 없이)")
    args = ap.parse_args()
    name = args.name or ("glitch_demo_login" if args.with_login else "glitch_demo")
    global W, H, DEMO_ID_TOKEN
    W, H = (int(v) for v in args.size.lower().split("x"))
    MOUSE[0], MOUSE[1] = W * 0.55, H * 0.45
    DEMO_ID_TOKEN = f"fake:{args.account}:{args.display_name or 'Glitch 녹화'}"
    out_dir: Path = args.out_dir

    delete_old_projects()
    tmp = out_dir / "_raw"
    shutil.rmtree(tmp, ignore_errors=True)
    t0 = time.time()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(
            viewport={"width": W, "height": H}, record_video_dir=str(tmp),
            record_video_size={"width": W, "height": H}, locale="ko-KR",
            color_scheme="dark" if args.dark else "light",
        )
        ctx.add_init_script(CURSOR_JS)
        if args.display_name or args.display_email:
            def swap_demo_user(route) -> None:
                resp = route.fetch()
                body = resp.text()
                if args.display_name:
                    body = body.replace('displayName: "Cho Euna"', f'displayName: {json.dumps(args.display_name)}')
                if args.display_email:
                    body = body.replace('email: "cho@example.com"', f'email: {json.dumps(args.display_email)}')
                route.fulfill(response=resp, body=body)

            ctx.route("**/auth/demo-auth.js", swap_demo_user)
        # 데모 로그인 버튼은 시드 계정 토큰을 보낸다 — 녹화에서는 시연 전용 계정으로 바꿔 보낸다
        ctx.route("**/auth/firebase", lambda route: route.continue_(
            post_data=json.dumps({"id_token": DEMO_ID_TOKEN}),
            headers={**route.request.headers, "content-type": "application/json"},
        ))
        page = ctx.new_page()
        cuts = Cuts(time.time())

        def mark(label: str) -> None:
            print(f"  {cuts.now():5.1f}s  {label}", flush=True)

        # ── 로그인 → 드라이브에서 새 프로젝트 만들기 ──
        page.goto(f"{BASE}/?demo=1")
        start_btn = page.get_by_role("button", name="시작하기").first
        if args.with_login:
            # 시작 화면을 잠깐 보여주고 사람처럼 커서로 "시작하기"를 누른다
            page.wait_for_function(
                "[...document.querySelectorAll('button')].some(b => b.textContent.trim() === '시작하기' && !b.disabled)",
                timeout=10000,
            )
            time.sleep(0.6)
            cuts.ranges.append((0.0, cuts.now()))  # 페이지가 그려지기 전 빈 화면만 자른다
            pause(1.6)
            click(page, start_btn, 0.3)
            page.wait_for_function("location.hash === '#drive'", timeout=10000)
            pause(1.0)
        else:
            time.sleep(1.0)
            start_btn.click()
            page.wait_for_function("location.hash === '#drive'", timeout=10000)
            time.sleep(0.8)
            cuts.ranges.append((0.0, cuts.now()))  # 시작 화면·로그인은 잘라낸다
            pause(0.4)
        click(page, page.locator("#new-project-btn"), 0.5)
        click(page, page.locator("#project-name-input"), 0.2)
        type_human(page, PROJECT_NAME)
        pause(0.5)
        click(page, page.locator("#project-name-confirm-btn"), 0.6)
        page.wait_for_function("location.hash === '#analyze'", timeout=10000)
        page.evaluate(
            """async (name) => {
                 const t = sessionStorage.getItem('glitch_backend_token');
                 const r = await fetch('/projects?page=1&page_size=50', {headers: {Authorization: 'Bearer ' + t}});
                 window.__pid = (await r.json()).items.find(p => p.name === name).id;
               }""",
            PROJECT_NAME,
        )
        pause(0.4)
        mark("새 프로젝트")

        # ── 1화: 파일 끌어 넣기 → 1차 결과 → 수정하기 → 1화 시각화 5개 탭 → 2차 검토 ──
        drag_files(page, [DATA / "chapter_1.txt"])
        analyze(page, cuts, [1])
        mark("1화 1차 결과 도착")
        pause(0.9)
        edit_first_item(page)
        mark("1화 수정하기")
        show_visuals(page, "1회차", FIRST_CHAPTER_VISUALS)
        review(page, cuts, 1)
        pause(0.7)  # 첫 회차는 비교할 앞 내용이 없어 모순 0건
        mark("1화 2차 검토")
        scroll_top(page)
        click(page, page.locator("#new-analysis-btn"), 0.6)
        page.wait_for_function("location.hash === '#analyze'", timeout=10000)
        pause(0.4)

        # ── 2화: 파일 끌어 넣기 → 2화 1차 결과 → 2화 2차 검토 → 모순 카드 판정 ──
        drag_files(page, [DATA / "chapter_2.txt"])
        analyze(page, cuts, [2])
        mark("2화 1차 결과 도착")
        pause(1.2)  # 2화 1차 결과는 통계만 잠깐
        review(page, cuts, 2)
        judge_contradictions(page)
        mark("2화 판정")

        # ── 저장된 캐논 "전체" 시각화 5개 탭 — 1화 부분을 지나 2화 항목까지 내려가 본다 ──
        scroll_top(page)
        click(page, page.locator('.tab-btn[data-tab="visual"]'), 0.5)
        for category, hold_top, chapters in FINAL_VISUALS:
            final_visual(page, category, hold_top, chapters)
        mark("전체 시각화")

        # ── 마무리: 다시 원문을 넣는 화면으로(다음 회차를 이어서 올리는 흐름) ──
        click(page, page.locator("#new-analysis-btn"), 0.6)
        page.wait_for_function("location.hash === '#analyze'", timeout=10000)
        scroll_top(page)
        zone = page.locator("#source-dropzone")
        human_move(page, *target_point(zone))  # 다음 원문을 넣으려는 듯 업로드 칸 위에 커서를 둔다
        pause(2.5)
        mark("업로드 화면으로 마무리")

        video = page.video
        ctx.close()
        browser.close()
        raw = Path(video.path())

    out_dir.mkdir(parents=True, exist_ok=True)
    raw_out = out_dir / f"{name}_raw.webm"
    shutil.move(str(raw), raw_out)
    shutil.rmtree(tmp, ignore_errors=True)
    (out_dir / f"{name}_cuts.json").write_text(json.dumps(cuts.ranges, indent=1), encoding="utf-8")
    cut_total = sum(b - a for a, b in cuts.ranges)
    print(f"raw {raw_out}  잘라낼 구간 {len(cuts.ranges)}개, {cut_total:.1f}s")
    out = out_dir / f"{name}.mp4"
    cut_video(raw_out, out, cuts.ranges)
    print(f"saved {out}  ({time.time() - t0:.1f}s)")


if __name__ == "__main__":
    main()
