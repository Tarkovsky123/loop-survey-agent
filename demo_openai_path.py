"""OpenAI 모델 경로로 한 거래처를 끝까지 통과시키는 시연. 단계마다 별도 프로세스로 실행해 '재시작 뒤 조회'까지 확인한다.

  python -X utf8 demo_openai_path.py            → out/demo_openai_path.md (실행 기록)
  LLM_BACKEND 기본값은 codex(OpenAI 모델, Codex CLI). openai 로 바꾸면 Responses API를 쓴다.

흐름: 설문 생성(LLM 분류·문안) → 발송 승인 기록 → 모의 응답 취합 → 재분류·제안 → 실행 승인 기록(중복 승인 거부)
      → 새 프로세스에서 기록 조회 → 호출 실패를 유도해 규칙 대체 확인
"""
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
PY = [sys.executable, "-X", "utf8"]
ACC = "A08"            # 대전 셀프사진관: 주문 간격 지연 트리거, 응답은 '가격 고민'
BACKEND = os.environ.get("LLM_BACKEND", "codex")
LOG = []


def sh(args, env=None, expect_fail=False):
    t0 = time.perf_counter()
    r = subprocess.run(PY + args, cwd=BASE, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, **(env or {})})
    out = (r.stdout + r.stderr).strip()
    LOG.append((" ".join(args), round(time.perf_counter() - t0, 1), r.returncode, out))
    if r.returncode != 0 and not expect_fail:
        raise SystemExit(f"실패: {' '.join(args)}\n{out}")
    return out


def trace():
    return [json.loads(x) for x in (BASE / "out/trace.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]


def actions_of(acc):
    with open(BASE / "out/actions.csv", encoding="utf-8") as f:
        return [(r["action_id"], r["status"]) for r in csv.DictReader(f) if r["account_id"] == acc]


def survey_of(acc):
    return next((s for s in json.loads((BASE / "out/surveys.json").read_text(encoding="utf-8")) if s["account_id"] == acc), None)


def main():
    facts = {}
    sh(["approve.py", "reset"])
    sh(["make_data.py"])
    # 1. LLM 경로로 한 바퀴: 설문 생성 + 분류 + 문안
    out = sh(["loop.py"], env={"LLM_BACKEND": BACKEND})
    tr = trace()
    facts["run1"] = {"mode": out, "llm_calls": [t for t in tr if t["step"] == "llm_call"],
                     "fallbacks": [t for t in tr if t["step"] == "llm_fallback"],
                     "survey": survey_of(ACC), "actions": actions_of(ACC)}
    # 2. 설문 발송 승인
    facts["approve_survey"] = sh(["approve.py", "survey", ACC, "--by", "박영업", "--note", "주문 지연 트리거 설문"])
    # 3. 모의 응답 취합 (공급처 변경 + 손님 수 비슷)
    facts["respond"] = [sh(["approve.py", "respond", ACC, "P_gap1", "비슷하다", "--date", "2026-11-03"]),
                        sh(["approve.py", "respond", ACC, "P_gap2", "인화지는 인터넷에서 더 싸게 사고 있어요", "--date", "2026-11-03"])]
    # 4. 응답을 결합해 다시 분류·제안 (LLM 경로)
    out = sh(["loop.py"], env={"LLM_BACKEND": BACKEND})
    tr = trace()
    rows = [json.loads(x) for x in []]  # placeholder (actions.csv 로 확인)
    facts["run2"] = {"mode": out, "llm_calls": [t for t in tr if t["step"] == "llm_call"],
                     "fallbacks": [t for t in tr if t["step"] == "llm_fallback"], "actions": actions_of(ACC),
                     "survey_now": survey_of(ACC)}
    html = (BASE / "out/weekly.html").read_text(encoding="utf-8")
    i = html.find(f"{ACC} ·")
    seg = html[i:i + 2600] if i >= 0 else ""
    import re
    facts["run2"]["themes"] = re.search(r"주제: ([^<]*)", seg).group(1) if seg else "?"
    facts["run2"]["message"] = re.search(r"안내 문안 초안: ([^<]*)", seg).group(1) if seg and "안내 문안 초안" in seg else "(대조군 또는 문안 없음)"
    facts["run2"]["guard"] = re.search(r"가드레일 보류: ([^<]*)", seg).group(1) if seg and "가드레일 보류" in seg else "없음"
    # 5. 실행 승인 + 중복 승인
    act = next((a for a, st in facts["run2"]["actions"] if st == "승인 대기"), None)
    facts["approve_action"] = sh(["approve.py", "action", ACC, act, "--by", "박영업"]) if act else "승인 대기 제안 없음"
    facts["approve_again"] = sh(["approve.py", "action", ACC, act, "--by", "박영업"]) if act else "-"
    # 6. 재시작 뒤 조회: 새 프로세스에서 기록을 읽고, 다시 실행해도 상태가 남는지
    facts["show_after_restart"] = sh(["approve.py", "show", ACC])
    out = sh(["loop.py"], env={"LLM_BACKEND": "rule"})  # 빠른 재실행(규칙)으로 승인 상태가 파일에서 복원되는지 확인
    approvals = [json.loads(x) for x in (BASE / "out/approvals.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    facts["run3"] = {"mode": out, "actions": actions_of(ACC),
                     "survey_approved": any(a["kind"] == "survey_send" and a["account_id"] == ACC for a in approvals)}
    # 7. 호출 실패 유도 → 규칙 대체
    out = sh(["loop.py"], env={"LLM_BACKEND": "codex", "CODEX_EXE": str(BASE / "없는_실행파일.exe")})
    tr = trace()
    facts["fallback"] = {"mode": out, "fallbacks": [t for t in tr if t["step"] == "llm_fallback"]}

    md = [f"# OpenAI 모델 경로 실행 기록 ({datetime.now():%Y-%m-%d %H:%M}, 백엔드 {BACKEND})", "",
          "가상 데이터. 단계마다 별도 프로세스로 실행했고, 승인·응답은 파일(`out/approvals.jsonl`, `data/pulse_responses.csv`)에 남는다.", "",
          f"## 1. LLM 경로로 한 바퀴 (`{facts['run1']['mode']}`)",
          f"- LLM 호출 {len(facts['run1']['llm_calls'])}회 "
          + ", ".join(f"{t['backend']} {t['ms']/1000:.0f}초" for t in facts['run1']['llm_calls'])
          + f" · 대체 {len(facts['run1']['fallbacks'])}회",
          f"- {ACC} 설문: " + (", ".join(q["q_id"] + " " + q["text"] for q in facts["run1"]["survey"]["questions"]) if facts["run1"]["survey"] else "없음"),
          f"- {ACC} 제안(응답 전): {facts['run1']['actions']}", "",
          "## 2. 설문 발송 승인", f"- {facts['approve_survey']}", "",
          "## 3. 모의 응답 취합", *[f"- {x}" for x in facts["respond"]], "",
          f"## 4. 응답을 결합해 재분류·제안 (`{facts['run2']['mode']}`)",
          f"- LLM 호출 {len(facts['run2']['llm_calls'])}회 · 대체 {len(facts['run2']['fallbacks'])}회",
          f"- {ACC} 주제: {facts['run2']['themes']}",
          f"- {ACC} 제안: {facts['run2']['actions']}",
          f"- 안내 문안(LLM): {facts['run2']['message']}",
          f"- 출력 가드레일: {facts['run2']['guard']}",
          f"- 설문 재발송 여부: {'제외(최근 응답 → 피로 관리)' if facts['run2']['survey_now'] is None else '발송 대상'}", "",
          "## 5. 실행 승인과 중복 승인", f"- {facts['approve_action']}", f"- 다시 승인: {facts['approve_again']}", "",
          "## 6. 재시작 뒤 조회 (새 프로세스)", "```", facts["show_after_restart"], "```",
          f"- 다시 실행한 결과(`{facts['run3']['mode']}`)의 {ACC} 제안 상태: {facts['run3']['actions']}, 설문 발송 승인: {facts['run3']['survey_approved']}", "",
          f"## 7. 호출 실패 유도 → 규칙 대체 (`{facts['fallback']['mode']}`)",
          f"- 대체 기록: {facts['fallback']['fallbacks']}", "",
          "## 명령 기록", "", "| 명령 | 초 | 종료 코드 |", "|---|---|---|",
          *[f"| `{c}` | {s} | {rc} |" for c, s, rc, _ in LOG]]
    (BASE / "out/demo_openai_path.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
