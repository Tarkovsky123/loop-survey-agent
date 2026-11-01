"""담당자 승인·응답 기록 (터미널용). 기록은 파일에 남아 프로그램을 다시 열어도 그대로 조회된다.

  python approve.py survey A08 --by 박영업            설문 발송 승인 (out/surveys.json 에 있는 거래처만)
  python approve.py respond A08 P_gap2 "응답 원문"    모의 응답 기록 → data/pulse_responses.csv
  python approve.py action A08 ACT11 --by 박영업      제안 실행 승인 (out/actions.csv 의 승인 대기 제안만, 중복 승인은 기록하지 않음)
  python approve.py show [A08]                        승인 기록 조회
  python approve.py reset                             승인 기록·모의 응답 삭제 (시연 재실행용)
"""
import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
APPROVALS, PULSE = BASE / "out/approvals.jsonl", BASE / "data/pulse_responses.csv"


def load():
    if not APPROVALS.exists():
        return []
    return [json.loads(x) for x in APPROVALS.read_text(encoding="utf-8").splitlines() if x.strip()]


def append(rec):
    APPROVALS.parent.mkdir(exist_ok=True)
    with open(APPROVALS, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **rec}, ensure_ascii=False) + "\n")


def cmd_survey(a):
    surveys = json.loads((BASE / "out/surveys.json").read_text(encoding="utf-8"))
    sv = next((s for s in surveys if s["account_id"] == a.account), None)
    if not sv:
        sys.exit(f"{a.account}: 이번 주 발송 대상 설문이 없다 (out/surveys.json)")
    if any(r["kind"] == "survey_send" and r["account_id"] == a.account for r in load()):
        print(f"{a.account}: 설문 발송이 이미 승인돼 있다 (중복 기록 안 함)")
        return
    append({"kind": "survey_send", "account_id": a.account, "by": a.by,
            "questions": [q["q_id"] for q in sv["questions"]], "note": a.note})
    print(f"{a.account}: 설문 {len(sv['questions'])}문항 발송 승인 기록 (담당 {a.by})")


def cmd_respond(a):
    new = not PULSE.exists()
    with open(PULSE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "account_id", "survey", "q_id", "answer"])
        w.writerow([a.date, a.account, "pulse", a.q_id, a.answer])
    print(f"{a.account}: {a.q_id} 응답 기록 → data/pulse_responses.csv")


def cmd_action(a):
    with open(BASE / "out/actions.csv", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["account_id"] == a.account and r["action_id"] == a.action]
    if not rows:
        sys.exit(f"{a.account} {a.action}: 제안 목록에 없다 (out/actions.csv). 먼저 loop.py를 실행했는지 확인")
    if rows[0]["status"] == "측정만":
        sys.exit(f"{a.account} {a.action}: 대조군이라 이번 주기는 승인할 수 없다")
    if rows[0]["status"] == "가드레일 보류":
        sys.exit(f"{a.account} {a.action}: 안내 문안이 가드레일에 걸려 있다. 수정 뒤 승인할 것")
    if any(r["kind"] == "action_exec" and r["account_id"] == a.account and r["action_id"] == a.action for r in load()):
        print(f"{a.account} {a.action}: 이미 승인돼 있다 (중복 기록 안 함)")
        return
    append({"kind": "action_exec", "account_id": a.account, "action_id": a.action, "by": a.by, "note": a.note})
    print(f"{a.account} {a.action}: 실행 승인 기록 (담당 {a.by})")


def cmd_show(a):
    recs = [r for r in load() if not a.account or r["account_id"] == a.account]
    if not recs:
        print("승인 기록 없음")
    for r in recs:
        extra = r.get("action_id") or ",".join(r.get("questions", []))
        print(f"{r['ts']}  {r['kind']:<11} {r['account_id']}  {extra}  담당 {r['by']}" + (f"  ({r['note']})" if r.get("note") else ""))


def cmd_reset(a):
    for p in (APPROVALS, PULSE):
        if p.exists():
            p.unlink()
    print("승인 기록과 모의 응답을 지웠다")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("survey"); p.add_argument("account"); p.add_argument("--by", required=True); p.add_argument("--note", default="")
    p = sub.add_parser("respond"); p.add_argument("account"); p.add_argument("q_id"); p.add_argument("answer")
    p.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    p = sub.add_parser("action"); p.add_argument("account"); p.add_argument("action"); p.add_argument("--by", required=True); p.add_argument("--note", default="")
    p = sub.add_parser("show"); p.add_argument("account", nargs="?")
    sub.add_parser("reset")
    a = ap.parse_args()
    {"survey": cmd_survey, "respond": cmd_respond, "action": cmd_action, "show": cmd_show, "reset": cmd_reset}[a.cmd](a)


if __name__ == "__main__":
    main()
