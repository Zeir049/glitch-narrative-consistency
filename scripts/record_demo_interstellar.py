"""인터스텔라 발표 대본 시연 녹화 — 평소 시연처럼 처음부터(미리 돌려 둔 프로젝트 없이) 녹화하고,
대본의 문단마다 말하는 길이에 맞춰 편집한다(배속 없음: 대기는 점프 컷, 말이 더 길면 그 장면에 더 머문다).

서버: replay 모드, REPLAY_STORY=interstellar, FIREBASE_MODE=fake, REPLAY_DELAY_SECONDS=60
      (.claude/launch.json 의 glitch-app-replay-interstellar-video — 분석이 도는 동안(s7~s9, 약 40초) 끝나지 않게 길게)
흐름: (구글 로그인 영상) → 내 드라이브 → 새 프로젝트 → 1화 끌어 넣기 → 입력·규칙 칸 → 분석 시작
      → 진행 중에 내 드라이브 → (완료까지 점프 컷) → 작업 창 "결과 보기" → 1차 결과·시각화·수정하기
      → 1화 검토(편집에서 건너뜀) → 2화 끌어 넣기·분석 → 2화 검토(진행 중 → 점프 컷) → 검수 결과·판정 → 시각화 자료(세계관·인물·시간·공간·사건, 스크롤 없음) → 끝
결과(<출력 폴더>): <name>.mp4 (1920x1080, 소리 없음), <name>_raw.webm, <name>_marks.json
  --edit-only 로 녹화 없이 marks.json 만으로 다시 편집할 수 있다(문단 길이를 고쳐 볼 때).
  --short: 대본 없이 기능만 보여 주는 짧은 영상(인셉션 영상 방식) — interstellar_short.mp4 / _login.mp4
           서버는 분석 지연이 짧은 glitch-app-replay-interstellar(1.5초)로 띄운다.

실행: pwenv\\Scripts\\python scripts\\record_demo_interstellar.py <출력 폴더> --login-clip <구글 로그인 녹화.mp4>
"""
import argparse
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

import record_demo as rd

BASE = rd.BASE
DATA = Path(r"C:\glitch_integrated\app\pipeline\replay_data\interstellar")
PROJECT_NAME = "인터스텔라"
EDIT_TEXT = "웜홀은 누군가 일부러 열어 둔 관문이다"
FILE_NAMES = {1: "인터스텔라_1화.txt", 2: "인터스텔라_2화.txt"}
OUT_W, OUT_H = 1920, 1080

# 발표 대본(문단별). s1·s2 는 구글 로그인 영상 위에 읽는다.
SCRIPT = {
    "s1": "링크를 통해 들어오면, 시작 화면이 나옵니다.",
    "s2": "구글 계정을 통해 간편하게 로그인 할 수 있도록 하였습니다. firebase를 통해 사용자 인증을 처리하고, "
          "PostgreSQL(포스트그레스큐엘)로 저장/관리합니다.",
    "s3": "로그인하게 되면 발급된 JWT토큰으로 해당 사용자가 생성한 프로젝트들이 보입니다. "
          "추후 작가가 로그아웃을 하더라도 자기 작품 데이터는 그대로 관리됩니다.",
    "s4": "새 프로젝트를 생성해보겠습니다. 제목을 입력하고, 만들기를 합니다.",
    "s5": "창작자들에게는 저장과 백업이 중요한 문제이기에, 불안정한 웹 작성보다는 작성된 파일을 입력할 수 있도록 하였습니다. "
          "예문으로 준비한 인터스텔라를 업로드해보겠습니다.",
    "s6": "이후 아래에서 추가적인 지시사항이나 꼭 지켜야 하는 규칙, 사전 정의된 세계관 규칙 등을 직접 입력하거나, "
          "파일로 업로드할 수 있고,",
    "s7": "아래의 분석하기를 누르면 FastAPI를 기반으로 구성된 백엔드에서 워커가 LLM(claude opus 5-5)에게 요청을 보내며, "
          "1차 분석이 진행됩니다.",
    "s8": "분석에는 분량에 따라 다르지만 몇천 자에 몇 분 정도 걸리기 때문에, 시간 관계상 분석이 완료된 프로젝트로 발표 이어가겠습니다.",
    "s9": "보시는 것처럼, 분석 진행 중에도 다른 프로젝트를 보거나 입력할 수 있도록 설계하였습니다.",
    "s10": "분석이 완료되면, 내용에 대해 시각적으로 정리됩니다. 사용자는 이것으로 자신의 창작물에 대해 따로 정리하지 않아도 "
           "다시 상기할 수 있고, 잘못 분석된 부분이 있다면 수정하여 반영할 수 있습니다.",
    "s11": "2화까지 업로드하고, 분석이 완료된다면 1화를 기준으로 2화에 대한 검토를 진행할 수 있습니다. "
           "신규 분량에 대한 기존 분량과의 충돌이 있는지를 탐지하는 과정입니다.",
    "s12": "검토 단계를 진행하기 위해서는 정리된 원문의 내용을 임베딩으로 벡터화하고, 저희가 후술할 ERS라는 수식에 맞춰 "
           "연관도가 높은 사건을 뽑아내는 등의 사전 처리가 필요하여 단순히 LLM을 호출하고 응답을 받는 것 보다 긴 시간이 걸립니다. "
           "현재 예문 기준 몇 분 정도의 시간이 걸리기 때문에, 사전에 검토를 완료한 것으로, 시연 이어서 진행하도록 하겠습니다.",
    "s13": "검수가 완료되고 나면, 화면과 같이 내용별로 검토문과 모순이 있는 원문이 보여지게 됩니다. "
           "이를 바탕으로 사용자는 원문을 수정하거나, 의도된 어긋남을 표기하여 기록할 수 있습니다.",
    "s14": "정리된 자료들은 메뉴 4번째에 있는 시각화 자료 탭에서 자세히 확인할 수 있습니다.",
    "s15": "시연을 마치고, 다시 발표자료로 돌아가 마무리하도록 하겠습니다.",
}
SYLLABLES_PER_SEC = 5.0  # 발표 말하기 속도(한글 음절 기준) — 대본 전체가 약 3분 5초


def speech_seconds(text: str) -> float:
    """읽는 데 걸리는 시간 추정: 한글 음절 + 영문·숫자(2자에 1음절꼴) + 쉼표·마침표 숨."""
    hangul = len(re.findall(r"[가-힣]", text))
    latin = len(re.findall(r"[A-Za-z0-9]", text))
    breaths = len(re.findall(r"[,.]", text))
    return round((hangul + latin / 2) / SYLLABLES_PER_SEC + breaths * 0.3, 2)


NARRATION = {k: speech_seconds(v) for k, v in SCRIPT.items()}
# 영상 길이 = 그 문단을 읽는 시간(대본에 맞춘 약 3분). 다만 내용이 적은 화면은 이 길이를 넘기지 않는다(초).
#   s3: 로그인 직후 빈 드라이브 — 로그인 문단이 넘쳐 들어와도 12초까지만
SCENE_MAX = {"s3": 12.0}
# 1화 2차 검토 장면은 편집에서 건너뛴다(s11 시작 ~ 2화 업로드 화면이 뜰 때까지, marks 의 skip_until)
SKIP_FIRST_REVIEW = True

# 구글 로그인 영상(1910x968, 사용자가 직접 녹화) — 다른 계정 줄을 흐리게 하고 9.1초(로그인 중...)까지 쓴다
# 이 영상은 팝업이 뜰 때(0.4~3.4초)와 닫힐 때(7.6~8.6초) 흰·회색 화면이 깜빡여 렉처럼 보인다 —
# 계정 선택 창(3.6~7.45초)과 "로그인 중..."(8.75~9.1초)만 쓰고, 시작 화면은 따로 녹화한 것(record_start)을 쓴다.
LOGIN_CUT = 9.1
LOGIN_KEEP = [(3.6, 7.45), (8.75, LOGIN_CUT)]
LOGIN_FILTER = (
    "fps=25,split=5[a0][a1][a2][a3][a4];"
    "[a1]crop=464:62:728:400,boxblur=14:3[k1];[a2]crop=464:62:728:538,boxblur=14:3[k2];"
    "[a3]crop=464:63:728:607,boxblur=14:3[k3];[a4]crop=464:71:728:677,boxblur=14:3[k4];"
    "[a0][k1]overlay=728:400:enable='{en}'[o1];[o1][k2]overlay=728:538:enable='{en}'[o2];"
    "[o2][k3]overlay=728:607:enable='{en}'[o3];[o3][k4]overlay=728:677:enable='{en}'[o4];"
    "[o4]crop=1720:968:95:0,scale={w}:{h}:flags=lanczos,setsar=1"
).format(en="between(t,3.3,8.1)", w=OUT_W, h=OUT_H)


def wait_status_slow(page, no: int, statuses: tuple[str, ...], timeout: float = 120) -> None:
    """rd.wait_status 와 같되 1초 간격 — 분석 지연이 길면 0.2초 간격 확인이 레이트리밋(분당 240)을 넘긴다."""
    end = time.time() + timeout
    while time.time() < end:
        st = page.evaluate(
            """async ([no]) => {
                 const t = sessionStorage.getItem('glitch_backend_token');
                 const r = await fetch(`/projects/${window.__pid}/chapters/${no}/result`,
                                       {headers: {Authorization: 'Bearer ' + t}});
                 return r.ok ? (await r.json()).status : null;
               }""",
            [no],
        )
        if st in statuses:
            return
        time.sleep(1.0)
    raise TimeoutError(f"{no}화가 {statuses} 상태가 되지 않음")


rd.wait_status = wait_status_slow  # rd.review 안에서도 이걸 쓰게


def clear_projects(token: str) -> None:
    c = httpx.Client(base_url=BASE, timeout=30)
    tok = c.post("/auth/firebase", json={"id_token": token}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    for p in c.get("/projects", params={"page": 1, "page_size": 50}, headers=h).json()["items"]:
        c.delete(f"/projects/{p['id']}", headers=h)


class Segments:
    """문단별 원본 구간과 '머무는 구간'(편집 때 말 길이에 맞춰 줄이는 대기)을 기록한다."""

    def __init__(self, cuts: rd.Cuts) -> None:
        self.cuts = cuts
        self.marks: dict[str, dict] = {}
        self.order: list[str] = []

    def start(self, key: str) -> None:
        if self.order:
            self.marks[self.order[-1]]["end"] = self.cuts.now()
        self.order.append(key)
        self.marks[key] = {"start": self.cuts.now(), "hold": None}
        print(f"  {self.cuts.now():6.1f}s  {key}  (대본 {NARRATION[key]:.1f}s)", flush=True)

    def hold(self, extra: float = 1.5, page=None, drift: tuple[str, ...] = ()) -> None:
        """지금 문단에서 머문다 — 대본 길이 + 여유만큼 녹화해 두고 편집에서 잘라 맞춘다.
        drift 를 주면 그동안 커서가 그 요소들을 천천히 오간다(멈춘 화면처럼 보이지 않게)."""
        a = self.cuts.now()
        seconds = NARRATION[self.order[-1]] + extra
        if page is not None and drift:
            drift_cursor(page, drift, seconds)
        else:
            rd.pause(seconds)
        self.marks[self.order[-1]]["hold"] = (a, self.cuts.now())

    def finish(self) -> None:
        self.marks[self.order[-1]]["end"] = self.cuts.now()


def drift_cursor(page, selectors: tuple[str, ...], seconds: float) -> None:
    """정해진 시간 동안 보이는 요소들 사이로 커서를 천천히 옮긴다."""
    end = time.time() + seconds
    i = 0
    while time.time() < end - 0.3:
        loc = page.locator(selectors[i % len(selectors)]).first
        i += 1
        try:
            if loc.count() and loc.is_visible():
                box = loc.bounding_box()
                if box and 0 < box["y"] < rd.H - 20:
                    rd.human_move(page, *rd.target_point(loc))
        except Exception:  # noqa: BLE001 - 화면이 바뀌는 중이면 건너뛴다
            pass
        rd.pause(max(0.0, min(rd.jitter(1.0, 1.6), end - time.time())))


def point_at(page, selector: str, hold: float) -> None:
    loc = page.locator(selector)
    loc.evaluate("e => e.scrollIntoView({block: 'center', behavior: 'smooth'})")
    rd.pause(0.5)
    rd.human_move(page, *rd.target_point(loc))
    rd.pause(hold)


def record(args, token: str, out_dir: Path) -> Path:
    files_dir = out_dir / "_files"
    files_dir.mkdir(parents=True, exist_ok=True)
    for no, fname in FILE_NAMES.items():  # 끌어 넣는 파일 이름이 화면에 보인다
        shutil.copyfile(DATA / f"chapter_{no}.txt", files_dir / fname)
    tmp = out_dir / "_raw"
    shutil.rmtree(tmp, ignore_errors=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(
            viewport={"width": rd.W, "height": rd.H}, record_video_dir=str(tmp),
            record_video_size={"width": rd.W, "height": rd.H}, locale="ko-KR", color_scheme="dark",
        )
        ctx.add_init_script(rd.CURSOR_JS)

        def swap_demo_user(route) -> None:
            resp = route.fetch()
            body = resp.text().replace('displayName: "Cho Euna"', f"displayName: {json.dumps(args.display_name)}")
            if args.display_email:
                body = body.replace('email: "cho@example.com"', f"email: {json.dumps(args.display_email)}")
            route.fulfill(response=resp, body=body)

        ctx.route("**/auth/demo-auth.js", swap_demo_user)
        ctx.route("**/auth/firebase", lambda route: route.continue_(
            post_data=json.dumps({"id_token": token}),
            headers={**route.request.headers, "content-type": "application/json"},
        ))
        page = ctx.new_page()
        cuts = rd.Cuts(time.time())
        seg = Segments(cuts)

        # 로그인은 구글 로그인 영상으로 대신한다 — 여기서는 드라이브까지 바로 들어가고 그 앞은 버린다
        page.goto(f"{BASE}/?demo=1")
        page.wait_for_function(
            "[...document.querySelectorAll('button')].some(b => b.textContent.trim() === '시작하기' && !b.disabled)",
            timeout=10000,
        )
        page.get_by_role("button", name="시작하기").first.click()
        page.wait_for_function("location.hash === '#drive'", timeout=10000)
        time.sleep(1.0)

        # s3 내 드라이브 — 로그인한 계정(왼쪽 아래)과 드라이브를 가리킨다
        seg.start("s3")
        drift_cursor(page, ("#sidebar-profile-name", "#sidebar-home-btn", "#new-project-btn"), 4.5)
        seg.hold(1.5, page, ("#sidebar-profile-name", "#new-project-btn"))

        # s4 새 프로젝트
        seg.start("s4")
        rd.click(page, page.locator("#new-project-btn"), 0.5)
        rd.click(page, page.locator("#project-name-input"), 0.2)
        rd.type_human(page, PROJECT_NAME)
        rd.pause(0.5)
        rd.click(page, page.locator("#project-name-confirm-btn"), 0.6)
        page.wait_for_function("location.hash === '#analyze'", timeout=10000)
        page.evaluate(
            """async (name) => {
                 const t = sessionStorage.getItem('glitch_backend_token');
                 const r = await fetch('/projects?page=1&page_size=50', {headers: {Authorization: 'Bearer ' + t}});
                 window.__pid = (await r.json()).items.find(p => p.name === name).id;
               }""",
            PROJECT_NAME,
        )
        seg.hold(1.0)

        # s5 파일 업로드 — 업로드 칸 설명을 훑다가 끝에 파일을 끌어 넣는다
        seg.start("s5")
        seg.hold(0.5, page, ("#source-dropzone .dropzone-title", "#source-dropzone .dropzone-sub",
                             ".field-desc", "#source-dropzone .dropzone-icon"))
        rd.drag_files(page, [files_dir / FILE_NAMES[1]])

        # s6 직접 입력·규칙 파일 칸(넣지는 않는다 — 넣으면 따로 회차로 올라간다)
        seg.start("s6")
        point_at(page, "#content-input", 1.4)
        point_at(page, "#rules-dropzone", 1.4)
        seg.hold(0.5, page, ("#content-input", "#rules-dropzone", "#analyze-btn"))
        page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
        rd.pause(0.5)

        # s7 분석하기 → 서버 연결 중 → 1차 분석 진행(작업 창 시간이 흘러간다)
        seg.start("s7")
        rd.click(page, page.locator("#analyze-btn"), 0)
        rd.show_connecting(page, hold=1.2)
        page.wait_for_function(
            "document.getElementById('analyze-btn').textContent.includes('분석 시작')", timeout=20000
        )
        seg.hold(0.5, page, ("#analyze-round-filter-wrap", ".task-tray .task-card"))

        # s8 분석 시간 설명 — 진행 중인 작업 창
        seg.start("s8")
        seg.hold(0.5, page, (".task-tray .task-card", "#analyze-round-filter-wrap"))

        # s9 분석이 도는 동안 다른 화면 — 내 드라이브, 새 프로젝트 입력 창까지 열어 본다
        seg.start("s9")
        rd.click(page, page.locator("#sidebar-home-btn"), 0.5)
        page.wait_for_function("location.hash === '#drive'", timeout=10000)
        card = page.locator(".project-card").first
        card.wait_for(timeout=10000)
        rd.human_move(page, *rd.target_point(card))
        rd.pause(0.8)
        rd.click(page, page.locator("#new-project-btn"), 0.4)
        rd.click(page, page.locator("#project-name-input"), 0.8)
        rd.click(page, page.get_by_role("button", name="취소").first, 0.4)
        seg.hold(0.5, page, (".project-card", ".task-tray .task-card"))
        cuts.start()  # 1차 분석이 끝날 때까지 점프 컷
        rd.wait_status(page, 1, ("extracted",), timeout=120)
        action = page.locator(".task-tray .task-action").first
        action.wait_for(state="visible", timeout=20000)
        rd.pause(0.5)
        cuts.end()
        rd.pause(1.0)

        # s10 결과 보기 → 시각화 → 수정하기 → 반영
        seg.start("s10")
        rd.click(page, action, 0.3)
        page.wait_for_function("location.hash === '#result'", timeout=10000)
        page.locator("#tab-extract .extract-item").first.wait_for(timeout=15000)
        rd.pause(1.0)
        rd.show_visuals(page, "1회차", [("character", 1.6, 0)])
        rd.click(page, page.locator('.tab-btn[data-tab="extract"]'), 0.5)
        rd.edit_first_item(page)
        rd.scroll_top(page)
        rd.click(page, page.locator('.tab-btn[data-tab="visual"]'), 0.4)
        rd.visual(page, "1회차", "worldview", 0.3)  # 고친 문구가 세계관 카드에 반영됨
        seg.hold(0.5, page, ("#visual-content .issue-card",))

        # s11 1화 검토(편집에서 건너뜀) → 2화 업로드 → 2화 1차 분석
        seg.start("s11")
        rd.review(page, cuts, 1)
        rd.pause(0.8)
        rd.scroll_top(page)
        rd.click(page, page.locator("#new-analysis-btn"), 0.6)
        page.wait_for_function("location.hash === '#analyze'", timeout=10000)
        seg.marks["s11"]["skip_until"] = cuts.now()  # 여기까지(1화 검토)는 편집에서 건너뛸 수 있다
        rd.pause(0.4)
        rd.drag_files(page, [files_dir / FILE_NAMES[2]])
        rd.click(page, page.locator("#analyze-btn"), 0)
        rd.show_connecting(page, hold=1.0)
        cuts.start()  # 2화 1차 분석 대기
        rd.wait_status(page, 2, ("extracted",), timeout=120)
        page.wait_for_function("location.hash === '#result'", timeout=20000)
        # 1화 검토 뒤라 결과 화면이 검수결과 탭으로 열릴 수 있다 — 잘리는 구간 안에서 1차 탭으로 돌린다
        page.evaluate("document.querySelector('.tab-btn[data-tab=\"extract\"]').click()")
        page.locator("#tab-extract .extract-item").first.wait_for(timeout=20000)
        rd.tidy_tray(page)
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(0.6)
        cuts.end()
        rd.pause(1.2)
        rd.smooth_scroll(page, 420, steps=30, dt=0.07)  # 2화 1차 결과를 천천히 훑는다
        rd.pause(1.0)
        rd.smooth_scroll(page, 420, steps=30, dt=0.07)
        seg.hold(0.5, page, ("#tab-extract .extract-item",))

        # s12 2화 검토 시작 — 도는 동안 2화 원문을 훑어본다(원문 임베딩 설명) → 완료까지 점프 컷
        seg.start("s12")
        rd.scroll_top(page)
        rd.click(page, page.locator('.tab-btn[data-tab="report"]'), 0.6)
        rd.click(page, page.locator("#report-review-btn"), 0)
        rd.show_connecting(page, hold=1.2)
        rd.pause(1.5)
        rd.click(page, page.locator('.tab-btn[data-tab="source"]'), 0.6)
        toggle = page.locator(".source-entry-toggle").last
        if toggle.count():
            rd.click(page, toggle, 0.6)
        rd.smooth_scroll(page, 700, steps=40, dt=0.12)
        rd.pause(1.0)
        rd.smooth_scroll(page, 700, steps=40, dt=0.12)
        rd.pause(0.8)
        rd.scroll_top(page)
        rd.click(page, page.locator('.tab-btn[data-tab="report"]'), 0.6)
        seg.hold(0.5, page, ("#report-review-btn", ".task-tray .task-card"))
        cuts.start()
        rd.wait_status(page, 2, ("saved",), timeout=120)
        page.wait_for_function(
            "document.getElementById('report-review-btn').textContent.includes('다시 검토하기')", timeout=15000
        )
        rd.tidy_tray(page)
        time.sleep(0.6)
        cuts.end()

        # s13 검수 결과 — 검토문과 모순 원문, ✓/✕ 판정
        seg.start("s13")
        page.locator("#issue-list .issue-card").first.wait_for(timeout=10000)
        rd.pause(1.5)
        rd.smooth_scroll(page, 520, steps=26, dt=0.06)
        rd.pause(1.2)
        rd.smooth_scroll(page, 520, steps=26, dt=0.06)
        rd.pause(1.0)
        rd.scroll_top(page)
        rd.judge_contradictions(page)
        seg.hold(0.5, page, ("#issue-list .issue-card",))

        # s14 시각화 자료(4번째 메뉴) — 스크롤 없이 세계관 → 인물 → 시간 → 공간 → 사건(전체 회차)
        seg.start("s14")
        rd.click(page, page.locator('.tab-btn[data-tab="visual"]'), 0.5)
        for category in ("worldview", "character", "time", "space", "event"):
            rd.visual(page, "전체", category, 1.6)
        seg.hold(0.5)

        # s15 끝
        seg.start("s15")
        seg.hold(0.5)
        seg.finish()

        video = page.video
        ctx.close()
        browser.close()
        raw = Path(video.path())

    raw_out = out_dir / f"{args.name}_raw.webm"
    shutil.move(str(raw), raw_out)
    shutil.rmtree(tmp, ignore_errors=True)
    (out_dir / f"{args.name}_marks.json").write_text(
        json.dumps({"cuts": cuts.ranges, "segments": seg.marks, "order": seg.order}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return raw_out


def subtract(a: float, b: float, holes: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """[a, b) 에서 holes 를 뺀 구간들."""
    out, t = [], a
    for ha, hb in sorted(holes):
        if hb <= t or ha >= b:
            continue
        if ha > t:
            out.append((t, ha))
        t = max(t, hb)
    if t < b:
        out.append((t, b))
    return out


def record_start(out_dir: Path) -> None:
    """평소 시작 화면(데모 표시 없음)을 몇 초 녹화한다 — 입자가 Scope 글자를 이루고 커서가 '시작하기'를 누르기까지.
    누르는 순간 구글 로그인 영상의 계정 선택 창으로 이어 붙인다(실제 로그인은 하지 않는다)."""
    tmp = out_dir / "_start"
    shutil.rmtree(tmp, ignore_errors=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(
            viewport={"width": rd.W, "height": rd.H}, record_video_dir=str(tmp),
            record_video_size={"width": rd.W, "height": rd.H}, locale="ko-KR", color_scheme="dark",
        )
        ctx.add_init_script(rd.CURSOR_JS)
        page = ctx.new_page()
        t0 = time.time()
        page.goto(f"{BASE}/")
        btn = page.get_by_role("button", name="시작하기").first
        btn.wait_for(state="visible", timeout=15000)
        painted = time.time() - t0 + 0.2
        rd.pause(3.6)  # 입자가 글자를 이루는 동안
        rd.human_move(page, *rd.target_point(btn))
        rd.pause(0.25)
        page.mouse.down()
        click = time.time() - t0 + 0.08
        rd.pause(0.4)
        video = page.video
        ctx.close()  # 버튼을 떼지 않는다 — 실제 구글 팝업을 띄우지 않게
        browser.close()
        shutil.move(str(Path(video.path())), out_dir / "start_raw.webm")
    shutil.rmtree(tmp, ignore_errors=True)
    (out_dir / "start.json").write_text(json.dumps({"from": painted, "click": click}), encoding="utf-8")


def plan_edit(marks: dict, start: dict) -> tuple[list[tuple], list[dict]]:
    """문단마다 원본에서 쓸 구간을 정한다. 대기(cut)는 빼고, '머무는 구간'은 대본 길이에 맞춰 줄인다.
    로그인 부분(s1·s2)은 멈춘 화면 없이 그대로 이어 붙인다 — 대본은 다음 장면 위로 이어 읽는다."""
    pieces: list[tuple] = [("S", start["from"], start["click"])]
    pieces += [("L", a, b) for a, b in LOGIN_KEEP]
    report: list[dict] = [
        {"seg": "s1", "script": NARRATION["s1"], "video": round(start["click"] - start["from"], 2)},
        {"seg": "s2", "script": NARRATION["s2"], "video": round(sum(b - a for a, b in LOGIN_KEEP), 2)},
    ]

    cuts = [tuple(c) for c in marks["cuts"]]
    for key in marks["order"]:
        m = marks["segments"][key]
        a, b = m["start"], m["end"]
        ha, hb = m["hold"]
        seg_cuts = list(cuts)
        if SKIP_FIRST_REVIEW and m.get("skip_until"):
            seg_cuts.append((a, m["skip_until"]))
        base = sum(y - x for x, y in subtract(a, b, seg_cuts + [(ha, hb)]))
        keep_hold = min(hb - ha, max(0.4, NARRATION[key] - base))
        if key in SCENE_MAX:
            keep_hold = min(keep_hold, max(0.4, SCENE_MAX[key] - base))
        ranges = subtract(a, b, seg_cuts + [(ha + keep_hold, hb)])
        pieces += [("R", x, y) for x, y in ranges]
        report.append({"seg": key, "script": NARRATION[key], "video": round(base + keep_hold, 2)})
    return pieces, report


def encode(raw: Path, login_clip: Path, start_raw: Path, out: Path, pieces: list[tuple]) -> None:
    """입력: 0=구글 로그인 영상(L), 1=본 녹화(R), 2=시작 화면 녹화(S)."""
    import imageio_ffmpeg

    parts, labels = [], []
    for i, (src, a, b) in enumerate(pieces):
        parts.append((src, f"trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[p{i}]"))
        labels.append(f"[p{i}]")
    count = {k: sum(1 for s, _ in parts if s == k) for k in ("L", "R", "S")}
    norm = f"fps=25,scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1"
    sources = {
        "L": f"[0:v]trim=0:{LOGIN_CUT},setpts=PTS-STARTPTS,{LOGIN_FILTER}",
        "R": f"[1:v]{norm}",
        "S": f"[2:v]{norm}",
    }
    head = "".join(
        f"{sources[k]},split={n}" + "".join(f"[{k}{j}]" for j in range(n)) + ";"
        for k, n in count.items() if n
    )
    used = {k: 0 for k in count}
    fixed = []
    for src, body in parts:  # 입력 하나를 여러 번 쓰려면 split 한 갈래씩 나눠 준다
        fixed.append(f"[{src}{used[src]}]{body}")
        used[src] += 1
    graph = head + ";".join(fixed) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=1:a=0[out]"
    script = out.with_suffix(".filter.txt")
    script.write_text(graph, encoding="utf-8")
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(login_clip), "-i", str(raw), "-i", str(start_raw), "-filter_complex_script", str(script),
         "-map", "[out]", "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
         "-r", "25", "-movflags", "+faststart", "-an", str(out)],
        check=True,
    )


def record_short(args, token: str, out_dir: Path) -> dict:
    """기능만 보여 주는 짧은 시연(인셉션 영상과 같은 방식, 배속 없음 — 기다리는 장면만 점프 컷).
    서버는 분석 지연이 짧아야 한다(glitch-app-replay-interstellar, REPLAY_DELAY_SECONDS=1.5).
    새 프로젝트 → 1화 끌어 넣기 → 분석 → 1차 결과 → 수정하기 → 1화 시각화 5개 탭
    → (1화 2차 검토는 잘라냄) → 2화 끌어 넣기 → 분석 → 2차 검토 → ✓/✕ → 전체 시각화 5개 탭(스크롤 없음)
    → 다음 회차 업로드 화면에서 끝."""
    files_dir = out_dir / "_files"
    files_dir.mkdir(parents=True, exist_ok=True)
    for no, fname in FILE_NAMES.items():
        shutil.copyfile(DATA / f"chapter_{no}.txt", files_dir / fname)
    tmp = out_dir / "_raw"
    shutil.rmtree(tmp, ignore_errors=True)
    marks: dict[str, float] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(
            viewport={"width": rd.W, "height": rd.H}, record_video_dir=str(tmp),
            record_video_size={"width": rd.W, "height": rd.H}, locale="ko-KR", color_scheme="dark",
        )
        ctx.add_init_script(rd.CURSOR_JS)

        def swap_demo_user(route) -> None:
            resp = route.fetch()
            body = resp.text().replace('displayName: "Cho Euna"', f"displayName: {json.dumps(args.display_name)}")
            if args.display_email:
                body = body.replace('email: "cho@example.com"', f"email: {json.dumps(args.display_email)}")
            route.fulfill(response=resp, body=body)

        ctx.route("**/auth/demo-auth.js", swap_demo_user)
        ctx.route("**/auth/firebase", lambda route: route.continue_(
            post_data=json.dumps({"id_token": token}),
            headers={**route.request.headers, "content-type": "application/json"},
        ))
        page = ctx.new_page()
        cuts = rd.Cuts(time.time())

        def mark(label: str) -> None:
            marks[label] = cuts.now()
            print(f"  {cuts.now():6.1f}s  {label}", flush=True)

        page.goto(f"{BASE}/?demo=1")
        page.wait_for_function(
            "[...document.querySelectorAll('button')].some(b => b.textContent.trim() === '시작하기' && !b.disabled)",
            timeout=10000,
        )
        page.get_by_role("button", name="시작하기").first.click()
        page.wait_for_function("location.hash === '#drive'", timeout=10000)
        time.sleep(0.8)
        cuts.ranges.append((0.0, cuts.now()))  # 시작 화면·로그인은 버린다(로그인 버전은 구글 로그인 영상을 붙인다)
        mark("drive")
        rd.pause(0.8)

        # 새 프로젝트 → 1화 → 분석
        rd.click(page, page.locator("#new-project-btn"), 0.5)
        rd.click(page, page.locator("#project-name-input"), 0.2)
        rd.type_human(page, PROJECT_NAME)
        rd.pause(0.4)
        rd.click(page, page.locator("#project-name-confirm-btn"), 0.6)
        page.wait_for_function("location.hash === '#analyze'", timeout=10000)
        page.evaluate(
            """async (name) => {
                 const t = sessionStorage.getItem('glitch_backend_token');
                 const r = await fetch('/projects?page=1&page_size=50', {headers: {Authorization: 'Bearer ' + t}});
                 window.__pid = (await r.json()).items.find(p => p.name === name).id;
               }""",
            PROJECT_NAME,
        )
        rd.pause(0.4)
        rd.drag_files(page, [files_dir / FILE_NAMES[1]])
        rd.analyze(page, cuts, [1])
        mark("1화 1차 결과")
        rd.pause(1.0)
        rd.edit_first_item(page)
        mark("수정하기")
        rd.show_visuals(page, "1회차", [("worldview", 1.4, 0), ("character", 1.6, 0), ("time", 1.0, 0),
                                       ("space", 1.0, 0), ("event", 1.0, 0)])
        mark("1화 시각화")

        # 1화 2차 검토는 영상에서 잘라낸다(저장은 해야 2화 검토가 1화를 기준으로 돈다)
        cuts.start()
        page.evaluate("document.querySelector('.tab-btn[data-tab=\"report\"]').click()")
        page.wait_for_timeout(400)
        page.evaluate("document.getElementById('report-review-btn').click()")
        rd.wait_status(page, 1, ("saved",), timeout=120)
        page.wait_for_function(
            "document.getElementById('report-review-btn').textContent.includes('다시 검토하기')", timeout=15000
        )
        rd.tidy_tray(page)
        page.evaluate("window.scrollTo(0, 0); document.getElementById('new-analysis-btn').click()")
        page.wait_for_function("location.hash === '#analyze'", timeout=10000)
        time.sleep(0.6)
        cuts.end()
        rd.pause(0.4)

        # 2화 → 분석 → 2화 1차 결과
        rd.drag_files(page, [files_dir / FILE_NAMES[2]])
        rd.click(page, page.locator("#analyze-btn"), 0)
        rd.show_connecting(page, hold=0.6)
        cuts.start()
        rd.wait_status(page, 2, ("extracted",), timeout=120)
        page.wait_for_function("location.hash === '#result'", timeout=20000)
        page.evaluate("document.querySelector('.tab-btn[data-tab=\"extract\"]').click()")
        page.locator("#tab-extract .extract-item").first.wait_for(timeout=20000)
        rd.tidy_tray(page)
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(0.6)
        cuts.end()
        mark("2화 1차 결과")
        rd.pause(1.2)

        # 2화 2차 검토 → 판정
        rd.review(page, cuts, 2)
        mark("2화 검수 결과")
        rd.pause(1.2)
        rd.smooth_scroll(page, 480, steps=24, dt=0.05)
        rd.pause(1.0)
        rd.scroll_top(page)
        rd.judge_contradictions(page)
        mark("판정")

        # 전체 시각화 5개 탭(스크롤 없음)
        rd.click(page, page.locator('.tab-btn[data-tab="visual"]'), 0.5)
        for category in ("worldview", "character", "time", "space", "event"):
            rd.visual(page, "전체", category, 1.6)
        mark("전체 시각화")

        # 다음 회차 업로드 화면에서 끝
        rd.scroll_top(page)
        rd.click(page, page.locator("#new-analysis-btn"), 0.6)
        page.wait_for_function("location.hash === '#analyze'", timeout=10000)
        rd.human_move(page, *rd.target_point(page.locator("#source-dropzone")))
        rd.pause(2.5)
        mark("끝")

        video = page.video
        ctx.close()
        browser.close()
        raw = Path(video.path())

    raw_out = out_dir / f"{args.name}_raw.webm"
    shutil.move(str(raw), raw_out)
    shutil.rmtree(tmp, ignore_errors=True)
    data = {"cuts": cuts.ranges, "marks": marks}
    (out_dir / f"{args.name}_marks.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def build_short(out_dir: Path, name: str, login_clip: Path, data: dict) -> None:
    """짧은 영상 두 개: 로그인 없는 버전(드라이브부터)과 로그인 버전(시작 화면 + 구글 로그인 영상 + 같은 녹화)."""
    raw = out_dir / f"{name}_raw.webm"
    cuts = [tuple(c) for c in data["cuts"]]
    end = data["marks"]["끝"] + 0.3
    body = [("R", a, b) for a, b in subtract(0.0, end, cuts)]
    start = json.loads((out_dir / "start.json").read_text(encoding="utf-8"))
    login = [("S", start["from"], start["click"])] + [("L", a, b) for a, b in LOGIN_KEEP]
    for suffix, pieces in (("", body), ("_login", login + body)):
        out = out_dir / f"{name}{suffix}.mp4"
        encode(raw, login_clip, out_dir / "start_raw.webm", out, pieces)
        print(f"saved {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description="인터스텔라 발표 대본 시연 녹화(처음부터) + 대본 길이 편집")
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--login-clip", type=Path, required=True, help="직접 녹화한 구글 로그인 영상(1910x968)")
    ap.add_argument("--size", default="1720x968")
    ap.add_argument("--display-name", default="Glitch 녹화")
    ap.add_argument("--display-email")
    ap.add_argument("--account", default="video-is@glitch.local")
    ap.add_argument("--name", default="interstellar_demo_login")
    ap.add_argument("--edit-only", action="store_true", help="녹화 없이 기존 raw·marks 로 다시 편집")
    ap.add_argument("--short", action="store_true",
                    help="기능만 보여 주는 짧은 영상(약 1분 30초) — 로그인 없는 버전·로그인 버전 두 개")
    args = ap.parse_args()

    rd.W, rd.H = (int(v) for v in args.size.lower().split("x"))
    rd.MOUSE[0], rd.MOUSE[1] = rd.W * 0.55, rd.H * 0.45
    rd.EDIT_TEXT = EDIT_TEXT
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.short:
        name = "interstellar_short" if args.name == "interstellar_demo_login" else args.name
        args.name = name
        if args.edit_only:
            data = json.loads((out_dir / f"{name}_marks.json").read_text(encoding="utf-8"))
        else:
            token = f"fake:{args.account}:{args.display_name}"
            clear_projects(token)
            data = record_short(args, token, out_dir)
        if not (out_dir / "start.json").exists():
            record_start(out_dir)
        build_short(out_dir, name, args.login_clip, data)
        return

    raw = out_dir / f"{args.name}_raw.webm"
    if not args.edit_only:
        token = f"fake:{args.account}:{args.display_name}"
        clear_projects(token)
        raw = record(args, token, out_dir)
    if not args.edit_only or not (out_dir / "start.json").exists():
        record_start(out_dir)

    marks = json.loads((out_dir / f"{args.name}_marks.json").read_text(encoding="utf-8"))
    start = json.loads((out_dir / "start.json").read_text(encoding="utf-8"))
    pieces, report = plan_edit(marks, start)
    for r in report:
        flag = "" if r["video"] <= r["script"] + 1.5 else "  ← 화면이 대본보다 김"
        print(f"  {r['seg']:>4}  대본 {r['script']:5.1f}s  영상 {r['video']:5.1f}s{flag}")
    out = out_dir / f"{args.name}.mp4"
    encode(raw, args.login_clip, out_dir / "start_raw.webm", out, pieces)
    print(f"saved {out}")


if __name__ == "__main__":
    main()
