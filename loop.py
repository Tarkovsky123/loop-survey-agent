"""LOOP 시제품 — 정기 설문 자동 생성 → 응답 취합·결합 → 매출 확대 제안·액션플랜 → 실행·검증

실행: python make_data.py && python loop.py   → out/weekly.html, out/actions.csv, out/surveys.json, out/trace.jsonl
- 데이터는 가상이다(make_data.py).
- 숫자·트리거·상태 판정은 코드로만 계산한다.
- LLM은 주관식 분류와 안내 문안에만 쓴다. 백엔드는 LLM_BACKEND로 고른다:
    rule   (기본) 규칙·템플릿만
    openai Responses API (OPENAI_API_KEY 필요, 모델은 OPENAI_MODEL)
    codex  OpenAI Codex CLI의 --output-schema 호출 (구독 로그인, API 키 불필요)
  LLM 호출이 실패하거나 형식이 어긋나면 규칙으로 대체하고 trace에 남긴다.
- 승인 기록(out/approvals.jsonl)과 펄스 응답(data/pulse_responses.csv)은 approve.py로 쌓이고, 다음 실행에 반영된다.
"""
import csv
import hashlib
import html
import json
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

BASE = Path(__file__).parent
DATA, OUT = BASE / "data", BASE / "out"
AS_OF = date(2026, 11, 2)          # 이번 주 월요일
GAP_RATIO = 1.5                     # 주문 간격이 평소의 1.5배 이상이면 트리거
DROP, DEV_GROW = -0.20, 0.15      # 인화 수량 4주 변화율 기준
FASTER = 0.8                        # 최근 주문 간격이 평소의 0.8배 이하면 성장 신호
COOLDOWN_DAYS = 14                  # 최근 14일 안에 응답한 거래처는 설문 발송 제외
HOLDOUT_SHARE = 0.2                 # 제안 대상 중 대조군 비율(위험 거래처 제외)
DISCOUNT_LIMIT = 10                 # 안내 문안에 쓸 수 있는 할인율 상한(%)

THEMES = {
    "기기 고장": ["고장", "멈춰", "오류", "걸림"],
    "고객 감소": ["손님이 줄", "손님도 줄", "줄었", "줄어서", "한산"],
    "콘텐츠 식상": ["프레임", "식상", "비슷해"],
    "가격 고민": ["가격", "값이", "비싸"],
    "확장 의향": ["2호점", "확장", "추가 매장"],
    "수요 증가": ["대기줄", "늘고", "늘었", "바빠", "부족", "많아서"],
    "재고 부담": ["재고"],
    "업태 전환": ["셀프 촬영존", "전환"],
    "배송 불만": ["배송"],
    "공급처 변경": ["다른 곳", "다른 데", "타사", "인터넷에서", "온라인에서", "최저가", "싼 데"],
}
# LLM에게 주는 주제 정의: 1차 평가에서 '수요 증가'를 놓친 뒤 추가(llm_eval.py)
THEME_DEFS = {
    "기기 고장": "기계·프린터·카메라가 멈추거나 오류가 나거나 수리가 필요함",
    "고객 감소": "매장을 찾는 최종 손님이 줄어듦",
    "콘텐츠 식상": "프레임·콘텐츠가 비슷하거나 지겨워짐",
    "가격 고민": "인화지 값·원가·촬영 가격을 두고 고민함",
    "확장 의향": "추가 매장·기기 도입 등 사업을 넓힐 계획",
    "수요 증가": "손님·주문·대기줄이 늘어 바쁘거나 기기·렌탈·재고가 모자람",
    "재고 부담": "팔리지 않은 재고가 남거나 쌓임",
    "업태 전환": "셀프 촬영존 도입 등 사업 방식을 바꾸려는 고민",
    "배송 불만": "배송 속도나 상태에 대한 불만",
    "공급처 변경": "소모품을 본사가 아닌 다른 판매처(온라인·도매몰 등)에서 사거나 사려고 함",
}
STATE_RANK = {"위험": 0, "확장": 1, "성장": 2, "정체": 3, "안정": 4}
TRACE = []

# 가드레일 어휘: 응답 속 지시문 의심, 카탈로그 밖 약속, 과장
INJECTION_WORDS = ("무시하", "이전 지시", "지시를 따", "지시사항", "프롬프트", "시스템", "규칙을 바꿔", "규칙을 무시",
                   "명령:", "instruction", "ignore", "모든 거래처")
OFF_CATALOG_WORDS = ("무상 교체", "무료 교체", "환불", "전액", "보상금", "무상 제공", "공짜")
OVERCLAIM_WORDS = ("보장", "무조건", "반드시", "확실히", "100%")


def log(step, t0, **kw):
    TRACE.append({"step": step, "ms": round((time.perf_counter() - t0) * 1000, 1), **kw})


def read(name, optional=False):
    p = DATA / name
    if optional and not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def d(s):
    return date.fromisoformat(s)


# ---------- LLM (선택) ----------
def backend():
    b = os.environ.get("LLM_BACKEND", "").lower()
    if b in ("openai", "codex", "rule"):
        return b
    return "openai" if os.environ.get("OPENAI_API_KEY") else "rule"


BACKEND_LABEL = {"rule": "규칙", "openai": "LLM(OpenAI API)", "codex": "LLM(OpenAI 모델, Codex CLI)"}


def codex_exe():
    p = os.environ.get("CODEX_EXE") or shutil.which("codex")
    if not p:
        hits = list((Path.home() / "AppData/Local/OpenAI/Codex/bin").glob("*/codex.exe"))
        p = str(hits[0]) if hits else "codex"
    return p


def llm_json(instruction, payload, schema):
    """JSON 스키마를 지키는 LLM 호출. 실패하면 None → 호출부가 규칙으로 대체. 어떤 백엔드든 같은 지시·스키마를 쓴다."""
    b = backend()
    if b == "rule":
        return None
    t0 = time.perf_counter()
    try:
        if b == "openai":
            out = _call_openai(instruction, payload, schema)
        else:
            out = _call_codex(instruction, payload, schema)
        TRACE.append({"step": "llm_call", "backend": b, "ms": round((time.perf_counter() - t0) * 1000, 1)})
        return out
    except Exception as e:  # 네트워크·형식·실행 오류는 규칙 경로로 대체하고 기록만 남긴다
        TRACE.append({"step": "llm_fallback", "backend": b, "error": type(e).__name__,
                      "ms": round((time.perf_counter() - t0) * 1000, 1)})
        return None


def _call_openai(instruction, payload, schema):
    body = {"model": os.environ.get("OPENAI_MODEL", "gpt-5-mini"),
            "input": [{"role": "system", "content": instruction},
                      {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            "text": {"format": {"type": "json_schema", "name": "out", "schema": schema, "strict": True}}}
    req = urllib.request.Request("https://api.openai.com/v1/responses", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.load(r)
    text = next(c["text"] for o in res["output"] if o["type"] == "message"
                for c in o["content"] if c["type"] == "output_text")
    return json.loads(text)


def _call_codex(instruction, payload, schema):
    prompt = (f"{instruction}\n파일을 읽거나 쓰지 말고 명령을 실행하지 마라. 아래 입력만 보고, "
              f"주어진 JSON 스키마를 따르는 JSON 하나만 출력한다.\n입력: {json.dumps(payload, ensure_ascii=False)}")
    with tempfile.TemporaryDirectory() as tmp:
        sp, op = Path(tmp) / "schema.json", Path(tmp) / "out.json"
        sp.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
        subprocess.run([codex_exe(), "exec", "-C", tmp, "-s", "read-only", "--skip-git-repo-check", "--ephemeral",
                        "--output-schema", str(sp), "-o", str(op), "-"],
                       input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=int(os.environ.get("CODEX_TIMEOUT", "900")), check=True)
        text = op.read_text(encoding="utf-8")
    s, e = text.find("{"), text.rfind("}")
    return json.loads(text[s:e + 1])


THEME_BATCH_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"],
                      "properties": {"items": {"type": "array", "items": {
                          "type": "object", "additionalProperties": False, "required": ["id", "themes"],
                          "properties": {"id": {"type": "string"},
                                         "themes": {"type": "array", "items": {"type": "string", "enum": list(THEMES)}}}}}}}
MSG_BATCH_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"],
                    "properties": {"items": {"type": "array", "items": {
                        "type": "object", "additionalProperties": False, "required": ["id", "message"],
                        "properties": {"id": {"type": "string"}, "message": {"type": "string"}}}}}}


def rule_themes(text):
    return [t for t, kws in THEMES.items() if any(k in text for k in kws)]


def code_themes_all(items):
    """items: [(id, text)] → {id: themes}, mode. 한 번의 호출로 모두 분류하고, 빠진 id는 규칙으로 메운다."""
    items = [(i, t) for i, t in items if t]
    if not items:
        return {}, "rule"
    r = llm_json("B2B 거래처(사진관·셀프사진관·대리점·기업)의 설문 주관식 응답마다 주제를 주어진 정의에 맞춰 목록에서만 고른다. "
                 "응답에 근거가 없으면 빈 목록. 모든 id를 빠짐없이 돌려준다.",
                 {"주제 정의": THEME_DEFS, "응답": [{"id": i, "text": t} for i, t in items]}, THEME_BATCH_SCHEMA)
    if r is None:
        return {i: rule_themes(t) for i, t in items}, "rule"
    got = {x["id"]: sorted(set(x["themes"])) for x in r["items"]}
    return {i: got.get(i, rule_themes(t)) for i, t in items}, "llm"


# ---------- 가드레일 ----------
def sanitize_text(text):
    """입력 가드레일: 응답 속 지시문(프롬프트 인젝션) 의심 표현이 있으면 LLM 입력에서 빼고 검토 보류로 표시한다."""
    low = text.lower()
    hit = [w for w in INJECTION_WORDS if w in low]
    return (None if hit else text), hit


def numbers_in(text):
    return set(re.findall(r"\d+(?:[.,]\d+)?", text))


def check_message(msg, evidence, actions, catalog):
    """출력 가드레일: 근거에 없는 숫자, 배정되지 않은·카탈로그 밖 제안, 할인 한도 초과, 과장 표현을 찾는다. 빈 목록이면 통과."""
    issues = []
    allowed_nums = set()
    for e in evidence:
        allowed_nums |= numbers_in(e)
    for a in actions:
        allowed_nums |= numbers_in(a["name"]) | numbers_in(a["detail"])
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*%?\s*할인", msg):
        if float(m.group(1).replace(",", "")) > DISCOUNT_LIMIT:
            issues.append(f"할인 한도 초과({m.group(1)}%)")
    bad_nums = sorted(n for n in numbers_in(msg) - allowed_nums
                      if not re.search(rf"{re.escape(n)}\s*%?\s*할인", msg))
    if bad_nums:
        issues.append("근거에 없는 숫자: " + ", ".join(bad_nums))
    assigned = {a["name"] for a in actions}
    stray = [c["name"] for c in catalog if c["name"] in msg and c["name"] not in assigned]
    if stray:
        issues.append("배정되지 않은 제안: " + ", ".join(stray))
    off = [w for w in OFF_CATALOG_WORDS if w in msg]
    if off:
        issues.append("카탈로그 밖 약속: " + ", ".join(off))
    over = [w for w in OVERCLAIM_WORDS if w in msg]
    if over:
        issues.append("과장 표현: " + ", ".join(over))
    return issues


# ---------- ① 정기 설문 자동 생성 ----------
def qa_question(text):
    """평가자: 이중질문·유도질문·길이를 검사해 문제 목록을 돌려준다(빈 목록이면 통과)."""
    issues = []
    if ("모두" in text and ("과 " in text or "와 " in text)) or " 및 " in text or text.count("?") > 1:
        issues.append("이중질문")
    if any(w in text for w in ("힘드시죠", "좋으시죠", "그렇죠", "훌륭한")):
        issues.append("유도질문")
    if len(text) > 60:
        issues.append("너무 김")
    return issues


def build_survey(trigger, bank, rejected):
    picked = []
    for q in (q for q in bank if q["trigger"] == trigger):
        issues = qa_question(q["text"])
        if issues:
            rejected.append({"q_id": q["q_id"], "text": q["text"], "issues": issues})
            continue
        picked.append(q)
        if len(picked) == 2:
            break
    return picked


# ---------- ② 응답 취합·결합 ----------
def signals(acc, orders, device, as_logs, responses):
    od = sorted(d(o["date"]) for o in orders)
    gaps = [(b - a).days for a, b in zip(od, od[1:])]
    base = statistics.median(gaps[: max(1, len(gaps) * 2 // 3)]) if gaps else None
    since = (AS_OF - od[-1]).days if od else None
    gap_ratio = round(since / base, 1) if base and since is not None else None
    # 성장 판정은 주문액(드문 주문에선 우연에 흔들림) 대신 최근 주문 간격으로 본다
    recent_iv = statistics.median(gaps[-3:]) if len(gaps) >= 6 else None
    iv_ratio = round(recent_iv / base, 2) if recent_iv and base else None

    def amount(lo, hi):
        return sum(int(o["amount"]) for o in orders if lo < d(o["date"]) <= hi)

    recent, prev = amount(AS_OF - timedelta(90), AS_OF), amount(AS_OF - timedelta(180), AS_OF - timedelta(90))
    prints = [int(x["prints"]) for x in sorted(device, key=lambda x: x["week_end"])]
    dev_trend = None
    if len(prints) >= 8:
        last4, prev4 = statistics.mean(prints[-4:]), statistics.mean(prints[-8:-4])
        dev_trend = round((last4 - prev4) / prev4, 2)
    core = {r["q_id"]: r["answer"] for r in responses if r["survey"] == "core"}
    texts, held = [], []
    for r in responses:
        a = r["answer"]
        if a.isdigit() or a in ("늘었다", "비슷하다", "줄었다", "있다", "없다"):
            continue
        clean, hit = sanitize_text(a)
        (texts if clean else held).append(a)
    resolved = [d(a["resolved"]) for a in as_logs]
    return {
        "base_interval": base, "days_since": since, "gap_ratio": gap_ratio,
        "recent_interval": recent_iv, "iv_ratio": iv_ratio,
        "trend": round((recent - prev) / prev, 2) if prev else None, "recent_amount": recent,
        "dev_trend": dev_trend, "c1": int(core["C1"]) if "C1" in core else None,
        "c2": int(core["C2"]) if "C2" in core else None, "texts": texts, "held_texts": held,
        "last_response": max((d(r["date"]) for r in responses), default=None),
        "as_resolved_days": (AS_OF - max(resolved)).days if resolved else None,
        "install_days": (AS_OF - d(acc["install_date"])).days,
    }


def triggers_of(s):
    t = []
    if (s["gap_ratio"] or 0) >= GAP_RATIO:
        t.append("order_gap")
    if s["as_resolved_days"] is not None and s["as_resolved_days"] <= 7:
        t.append("as_done")
    if 28 <= s["install_days"] <= 35:
        t.append("install_30")
    if s["dev_trend"] is not None and s["dev_trend"] <= DROP:
        t.append("device_drop")
    return t


# ---------- ③ 매출 확대 제안·액션플랜 ----------
def classify(s, themes):
    neg = {"기기 고장", "고객 감소", "재고 부담", "업태 전환"}
    stalled = (s["gap_ratio"] or 0) >= GAP_RATIO or (s["dev_trend"] is not None and s["dev_trend"] <= DROP)
    if stalled and (neg & set(themes) or (s["c2"] is not None and s["c2"] <= 2)):
        return "위험"
    growing = (s["iv_ratio"] is not None and s["iv_ratio"] <= FASTER) or (s["dev_trend"] or 0) >= DEV_GROW
    if growing and "확장 의향" in themes:
        return "확장"
    if growing:
        return "성장"
    if stalled or {"콘텐츠 식상", "가격 고민", "공급처 변경"} & set(themes):
        return "정체"
    return "안정"


def evidence(s):
    ev = []
    if (s["gap_ratio"] or 0) >= GAP_RATIO:
        ev.append(f"마지막 주문 후 {s['days_since']}일 (평소 주문 간격 {s['base_interval']:.0f}일의 {s['gap_ratio']}배)")
    if s["iv_ratio"] is not None and s["iv_ratio"] <= FASTER:
        ev.append(f"최근 주문 간격 {s['recent_interval']:.0f}일로 평소({s['base_interval']:.0f}일)보다 빨라짐")
    if s["trend"] is not None and s["trend"] <= -0.3:
        ev.append(f"최근 90일 주문액 {s['trend']:+.0%} (직전 90일 대비)")
    if s["dev_trend"] is not None and abs(s["dev_trend"]) >= 0.1:
        ev.append(f"키오스크 인화 수량 {s['dev_trend']:+.0%} (최근 4주 vs 직전 4주, 거래처 동의 데이터)")
    if s["as_resolved_days"] is not None and s["as_resolved_days"] <= 7:
        ev.append(f"A/S 처리 완료 {s['as_resolved_days']}일 전")
    if s["c2"] is not None and s["c2"] <= 2:
        ev.append(f"정기 설문 재주문 의향 {s['c2']}/5")
    ev += [f"응답: \"{t}\"" for t in s["texts"]]
    ev += [f"응답 {len(s['held_texts'])}건 검토 보류(지시문 의심, 분류·문안에서 제외)"] if s["held_texts"] else []
    return ev


def propose(acc, state, themes, catalog):
    out = []
    for a in catalog:
        if state in a["states"].split("|") and set(a["themes"].split("|")) & set(themes) \
                and acc["type"] in a["types"].split("|"):
            out.append(a)
    return out[:3]


def rule_message(acc, themes, actions):
    topic = themes[0] if themes else "운영 현황"
    return (f"{acc['region']} {acc['type']} 사장님, 최근 말씀해 주신 '{topic}' 관련해 "
            f"{', '.join(a['name'] for a in actions)}을(를) 제안드립니다. 편하신 시간에 짧게 통화 가능하실까요?")


def messages_all(rows):
    """제안이 있는 거래처의 안내 문안을 한 번에 만든다. 근거·제안만 주고, 숫자·약속은 근거 밖에서 쓰지 말라고 지시한다."""
    todo = [r for r in rows if r["actions"]]
    if not todo:
        return {}, "rule"
    cases = [{"id": r["acc"]["account_id"], "거래처": f"{r['acc']['region']} {r['acc']['type']}", "상태": r["state"],
              "주제": r["themes"], "근거": r["evidence"], "제안": [a["name"] for a in r["actions"]]} for r in todo]
    r = llm_json("영업 담당자가 거래처 사장님께 보낼 3문장 이내의 정중한 안내 문안을 거래처마다 쓴다. "
                 "주어진 근거와 제안만 쓰고, 근거에 없는 숫자·약속·할인은 쓰지 않는다. 모든 id를 빠짐없이 돌려준다.",
                 {"거래처": cases}, MSG_BATCH_SCHEMA)
    if r is None:
        return {x["acc"]["account_id"]: rule_message(x["acc"], x["themes"], x["actions"]) for x in todo}, "rule"
    got = {x["id"]: x["message"] for x in r["items"]}
    return {x["acc"]["account_id"]: got.get(x["acc"]["account_id"]) or rule_message(x["acc"], x["themes"], x["actions"])
            for x in todo}, "llm"


def holdout_ids(rows):
    pool = sorted((r for r in rows if r["state"] != "위험"),
                  key=lambda r: hashlib.md5(r["acc"]["account_id"].encode()).hexdigest())
    return {r["acc"]["account_id"] for r in pool[: max(1, round(len(pool) * HOLDOUT_SHARE))]} if pool else set()


# ---------- 승인 기록 ----------
def load_approvals():
    p = OUT / "approvals.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------- 실행 ----------
def run():
    TRACE.clear()
    t0 = time.perf_counter()
    accounts, orders, device = read("accounts.csv"), read("orders.csv"), read("device_weekly.csv")
    as_logs = read("as_logs.csv")
    responses = read("survey_responses.csv") + read("pulse_responses.csv", optional=True)
    bank, catalog = read("question_bank.csv"), read("action_catalog.csv")
    approvals = load_approvals()
    log("load", t0, accounts=len(accounts), orders=len(orders), responses=len(responses), approvals=len(approvals),
        backend=backend())

    t0 = time.perf_counter()
    by = lambda rows, aid: [r for r in rows if r["account_id"] == aid]  # noqa: E731
    rows = []
    for acc in accounts:
        aid = acc["account_id"]
        dev = by(device, aid) if acc["device_consent"] == "1" else []   # 동의한 거래처만
        s = signals(acc, by(orders, aid), dev, by(as_logs, aid), by(responses, aid))
        rows.append({"acc": acc, "s": s, "triggers": triggers_of(s)})
    log("combine", t0, with_device=sum(1 for r in rows if r["s"]["dev_trend"] is not None),
        held_texts=sum(len(r["s"]["held_texts"]) for r in rows))

    t0 = time.perf_counter()
    surveys, rejected, skipped = [], [], []
    sent_ok = {a["account_id"] for a in approvals if a["kind"] == "survey_send"}
    for r in rows:
        if not r["triggers"]:
            continue
        last = r["s"]["last_response"]
        if last and (AS_OF - last).days < COOLDOWN_DAYS:
            skipped.append({"account_id": r["acc"]["account_id"], "last_response": last.isoformat()})
            continue
        qs = []
        for trig in r["triggers"]:
            qs += [{"trigger": trig, **q} for q in build_survey(trig, bank, rejected)]
        surveys.append({"account_id": r["acc"]["account_id"], "triggers": r["triggers"], "questions": qs[:3],
                        "approved": r["acc"]["account_id"] in sent_ok})
    log("survey", t0, sent=len(surveys), rejected=len(rejected), skipped=len(skipped))

    t0 = time.perf_counter()
    themes_by, m1 = code_themes_all([(r["acc"]["account_id"], " / ".join(r["s"]["texts"])) for r in rows])
    for r in rows:
        themes = list(themes_by.get(r["acc"]["account_id"], []))
        if "as_done" in r["triggers"]:
            themes.append("A/S 후속")
        r["themes"], r["state"] = themes, classify(r["s"], themes)
        r["actions"] = propose(r["acc"], r["state"], themes, catalog)
        r["evidence"] = evidence(r["s"])
    log("classify", t0, mode=m1)

    t0 = time.perf_counter()
    msgs, m2 = messages_all(rows)
    approved_actions = {(a["account_id"], a["action_id"]) for a in approvals if a["kind"] == "action_exec"}
    guard_hold = 0
    for r in rows:
        aid = r["acc"]["account_id"]
        r["message"] = msgs.get(aid, "")
        r["msg_issues"] = check_message(r["message"], r["evidence"], r["actions"], catalog) if r["actions"] else []
        guard_hold += bool(r["msg_issues"])
        for a in r["actions"]:
            a_status = "승인됨" if (aid, a["action_id"]) in approved_actions else None
            r.setdefault("approved", {})[a["action_id"]] = a_status
    proposals = [r for r in rows if r["actions"]]
    hold = holdout_ids(proposals)
    for r in proposals:
        r["holdout"] = r["acc"]["account_id"] in hold
    log("propose", t0, proposals=len(proposals), holdout=len(hold), guard_hold=guard_hold, mode=f"{m1}/{m2}")
    mode = BACKEND_LABEL[backend()] if "llm" in (m1, m2) else "규칙" + ("(대체)" if backend() != "rule" else "")
    return rows, proposals, surveys, rejected, skipped, mode


# ---------- 출력 ----------
CSS = """
body{font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;margin:0;background:#f5f6f8;color:#1d2433}
header{background:#1d2433;color:#fff;padding:22px 32px}header h1{margin:0;font-size:22px}
header p{margin:6px 0 0;color:#b9c0cf;font-size:13px}.wrap{padding:22px 32px}
.badge{display:inline-block;padding:2px 9px;border-radius:10px;font-size:12px;font-weight:700;margin-left:6px}
.cards{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:20px}
.card{background:#fff;border-radius:10px;padding:14px;box-shadow:0 1px 3px #0001}
.card b{display:block;font-size:26px}.card span{font-size:12px;color:#5b6475}
h2{font-size:17px;margin:26px 0 10px}.acc{background:#fff;border-radius:10px;padding:16px 18px;margin-bottom:12px;
box-shadow:0 1px 3px #0001;display:grid;grid-template-columns:1.1fr 1.6fr 1fr;gap:18px}
.acc h3{margin:0 0 4px;font-size:15px}.muted{color:#5b6475;font-size:12px}ul{margin:6px 0;padding-left:18px;font-size:13px}
.act{border-left:3px solid #2f6fde;padding:4px 10px;margin:6px 0;font-size:13px}.act small{color:#5b6475;display:block}
.msg{background:#f0f3f8;border-radius:8px;padding:8px 10px;font-size:12px;margin-top:6px}
.btn{display:inline-block;border:1px solid #c7cdd8;border-radius:6px;padding:4px 10px;font-size:12px;margin-right:6px;background:#fff}
.btn.ok{background:#1d2433;color:#fff;border-color:#1d2433}table{width:100%;border-collapse:collapse;background:#fff;font-size:13px}
td,th{border-bottom:1px solid #e3e6ec;padding:8px;text-align:left;vertical-align:top}th{background:#eef1f5}
.bad{color:#c0392b}.hold{background:#fff7e0;border:1px dashed #d9a400}.ok{color:#1e8e5a;font-weight:700}
"""
STATE_COLOR = {"위험": "#c0392b", "확장": "#6c3fc4", "성장": "#1e8e5a", "정체": "#d9822b", "안정": "#7a8496"}
TRIG = {"order_gap": "주문 간격 지연", "as_done": "A/S 완료", "install_30": "설치 30일", "device_drop": "인화 수량 감소"}


def esc(x):
    return html.escape(str(x))


def render(rows, proposals, surveys, rejected, skipped, mode):
    n_state = {k: sum(1 for r in rows if r["state"] == k) for k in STATE_RANK}
    cards = [("관리 거래처", len(rows)), ("이번 주 트리거", sum(1 for r in rows if r["triggers"])),
             ("위험 거래처", n_state["위험"]), ("제안(승인 대기)", sum(1 for r in proposals if not r["holdout"])),
             ("대조군(측정만)", sum(1 for r in proposals if r["holdout"])), ("트리거 설문 발송", len(surveys))]
    h = [f"<!doctype html><meta charset='utf-8'><title>LOOP 주간 액션</title><style>{CSS}</style>",
         "<header><h1>LOOP · 이번 주 거래처 액션 <span class='badge' style='background:#2f6fde'>"
         f"{esc(mode)} 모드</span></h1><p>{AS_OF.isoformat()} (월) · 담당자 승인 대기열 · "
         "가상 데이터로 만든 시연 화면 · 모든 수치는 코드로 계산</p></header><div class='wrap'><div class='cards'>"]
    h += [f"<div class='card'><b>{v}</b><span>{esc(k)}</span></div>" for k, v in cards]
    h.append("</div><h2>① 승인 대기 제안 (상태 심각도 순)</h2>")
    for r in sorted(proposals, key=lambda r: (r["holdout"], STATE_RANK[r["state"]])):
        a = r["acc"]
        col = STATE_COLOR[r["state"]]
        h.append(f"<div class='acc{' hold' if r['holdout'] else ''}'><div><h3>{esc(a['region'])} {esc(a['type'])}"
                 f"<span class='badge' style='background:{col};color:#fff'>{r['state']}</span></h3>"
                 f"<div class='muted'>{a['account_id']} · 담당 {esc(a['rep'])} · 트리거: "
                 f"{esc(', '.join(TRIG[t] for t in r['triggers']) or '없음')}</div>"
                 f"<div class='muted'>주제: {esc(', '.join(r['themes']) or '-')}</div></div><div><b style='font-size:13px'>근거</b><ul>")
        h += [f"<li>{esc(e)}</li>" for e in r["evidence"]]
        h.append("</ul></div><div><b style='font-size:13px'>제안</b>")
        for x in r["actions"]:
            st = r["approved"].get(x["action_id"])
            h.append(f"<div class='act'>{esc(x['name'])}" + (f" <span class='ok'>✓ {st}</span>" if st else "") +
                     f"<small>{esc(x['precedent'])}</small><small>예상 효과: {esc(x['expected'])}</small></div>")
        if r["holdout"]:
            h.append("<div class='msg'>대조군: 이번 주기는 제안을 보류하고 성과만 측정합니다</div>")
        else:
            h.append(f"<div class='msg'>안내 문안 초안: {esc(r['message'])}</div>")
            if r["msg_issues"]:
                h.append(f"<div class='msg bad'>가드레일 보류: {esc('; '.join(r['msg_issues']))} → 담당자 수정 필요</div>")
            h.append("<div style='margin-top:8px'><span class='btn ok'>승인</span><span class='btn'>수정</span>"
                     "<span class='btn'>보류</span></div>")
        h.append("</div></div>")
    h.append("<h2>② 이번 주 발송할 트리거 설문 (담당자 승인 후 발송)</h2><table><tr><th>거래처</th><th>트리거</th><th>문항</th><th>발송 승인</th></tr>")
    names = {r["acc"]["account_id"]: f"{r['acc']['region']} {r['acc']['type']}" for r in rows}
    for sv in surveys:
        qs = "<br>".join(f"{esc(q['text'])} <span class='muted'>({esc(q['answer_type'])})</span>" for q in sv["questions"])
        ok = "<span class='ok'>✓ 승인됨</span>" if sv["approved"] else "<span class='muted'>대기</span>"
        h.append(f"<tr><td>{esc(names[sv['account_id']])}</td><td>{esc(', '.join(TRIG[t] for t in sv['triggers']))}</td><td>{qs}</td><td>{ok}</td></tr>")
    h.append("</table>")
    if skipped:
        h.append("<p class='muted'>피로 관리로 이번 주 제외: " + ", ".join(
            f"{esc(names[x['account_id']])}(최근 응답 {x['last_response']})" for x in skipped) + "</p>")
    h.append("<h2>③ 평가자 검수: 걸러진 문항</h2><table><tr><th>문항 후보</th><th>판정</th></tr>")
    seen = set()
    for x in rejected:
        if x["q_id"] in seen:
            continue
        seen.add(x["q_id"])
        h.append(f"<tr><td>{esc(x['text'])}</td><td class='bad'>{esc(', '.join(x['issues']))} → 제외</td></tr>")
    h.append("</table><h2>④ 실행 추적</h2><table><tr><th>단계</th><th>처리 시간</th><th>결과</th></tr>")
    for t in TRACE:
        rest = ", ".join(f"{k}={v}" for k, v in t.items() if k not in ("step", "ms"))
        h.append(f"<tr><td>{esc(t['step'])}</td><td>{t.get('ms', '-')} ms</td><td>{esc(rest)}</td></tr>")
    h.append("</table><p class='muted'>측정 계획: 비위험 거래처는 대조군(측정이 끝난 뒤 교대)과 사전·사후 차이를 비교하고, "
             "위험 거래처는 모두 조치한 뒤 도입 전 분기 대비 이탈률·주문 회복을 관찰합니다.</p></div>")
    return "\n".join(h)


def main():
    rows, proposals, surveys, rejected, skipped, mode = run()
    OUT.mkdir(exist_ok=True)
    (OUT / "weekly.html").write_text(render(rows, proposals, surveys, rejected, skipped, mode), encoding="utf-8")
    with open(OUT / "actions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["account_id", "state", "action_id", "status", "holdout"])
        for r in proposals:
            for a in r["actions"]:
                status = ("측정만" if r["holdout"] else r["approved"].get(a["action_id"])
                          or ("가드레일 보류" if r["msg_issues"] else "승인 대기"))
                w.writerow([r["acc"]["account_id"], r["state"], a["action_id"], status, int(r["holdout"])])
    (OUT / "surveys.json").write_text(json.dumps(surveys, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "trace.jsonl").write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in TRACE), encoding="utf-8")
    print(f"{mode} 모드 · 거래처 {len(rows)} · 제안 {len(proposals)} · 설문 {len(surveys)} → out/weekly.html")
    return rows


if __name__ == "__main__":
    main()
