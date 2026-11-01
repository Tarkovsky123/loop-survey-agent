"""LLM 경로 검증: 실제 모델로 주관식 주제 분류·펄스 문항 생성·안내 문안 작성을 돌려
규칙 결과와 평가 단계(가드레일)에 비춰 본다. 데이터는 모두 가상이다.

실행 (먼저 python make_data.py):
  python -X utf8 llm_eval.py --backend agy     # Google Antigravity CLI (Gemini)
  python -X utf8 llm_eval.py --backend codex   # OpenAI Codex CLI (구독 한도 사용)
  python -X utf8 llm_eval.py --backend openai  # Responses API (OPENAI_API_KEY 필요)
결과: out/llm_eval_<backend>.json, out/llm_eval_<backend>.md
"""
import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import loop

HERE = Path(__file__).parent
AGY = Path(os.environ.get("AGY_EXE") or shutil.which("agy") or Path.home() / "AppData/Local/agy/bin/agy.exe")
AGY_MODEL = os.environ.get("AGY_MODEL", "gemini-4-argon-high")
CODEX = Path(os.environ.get("CODEX_EXE") or shutil.which("codex")
             or next((Path.home() / "AppData/Local/OpenAI/Codex/bin").glob("*/codex.exe"), "codex"))
NO_TOOLS = "파일을 읽거나 쓰지 말고, 명령을 실행하거나 웹을 검색하지 마라. 아래 입력만 보고 답하라."
OVERCLAIM = ("보장", "무조건", "반드시", "확실히", "무료", "공짜", "할인")


# ---------- 모델 호출: 같은 지시·스키마를 백엔드만 바꿔 보낸다 ----------
def parse_json(text):
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("JSON을 찾지 못함")
    return json.loads(text[start:end + 1])


def ask(backend, instruction, payload, schema):
    prompt = (f"{instruction}\n{NO_TOOLS}\n"
              f"출력은 아래 JSON 스키마를 따르는 JSON 하나만 쓴다. 설명과 코드 블록은 쓰지 않는다.\n"
              f"스키마: {json.dumps(schema, ensure_ascii=False)}\n"
              f"입력: {json.dumps(payload, ensure_ascii=False)}")
    t0 = time.time()
    if backend == "openai":
        out = loop.llm_json(instruction, payload, schema)
        if out is None:
            raise RuntimeError("OpenAI 호출 실패(OPENAI_API_KEY 확인)")
        return out, time.time() - t0
    if backend == "agy":
        r = subprocess.run([str(AGY), "-p", prompt, "--model", AGY_MODEL, "--print-timeout", "900s"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1000)
        return parse_json(r.stdout), time.time() - t0
    if backend == "codex":
        with tempfile.TemporaryDirectory() as tmp:
            schema_path, out_path = Path(tmp) / "schema.json", Path(tmp) / "out.json"
            schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
            subprocess.run([str(CODEX), "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral",
                            "--output-schema", str(schema_path), "-o", str(out_path), prompt],
                           cwd=tmp, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1000)
            return parse_json(out_path.read_text(encoding="utf-8")), time.time() - t0
    raise ValueError(backend)


# ---------- 과제 1: 주관식 주제 분류 → 규칙 결과와 비교 ----------
def rule_themes(text):
    return [t for t, kws in loop.THEMES.items() if any(k in text for k in kws)]


def task_classify(backend, with_defs=True):
    rows = list(csv.DictReader(open(HERE / "data/survey_responses.csv", encoding="utf-8")))
    items = [{"id": f"{r['account_id']}-{r['q_id']}", "text": r["answer"]}
             for r in rows if r["q_id"] == "C4" or r["q_id"] == "P_dev1"]
    schema = {"type": "object", "additionalProperties": False, "required": ["items"],
              "properties": {"items": {"type": "array", "items": {
                  "type": "object", "additionalProperties": False, "required": ["id", "themes"],
                  "properties": {"id": {"type": "string"},
                                 "themes": {"type": "array", "items": {"type": "string", "enum": list(loop.THEMES)}}}}}}}
    topics = loop.THEME_DEFS if with_defs else list(loop.THEMES)  # v1은 이름만, v2부터 정의를 준다
    out, sec = ask(backend, "B2B 거래처(사진관·셀프사진관·대리점·기업)의 설문 주관식 응답마다 주제를 주어진 목록에서만 고른다. "
                            "주제 정의가 있으면 정의에 맞춰 고른다. 응답에 근거가 없으면 빈 목록. 모든 id를 빠짐없이 돌려준다.",
                   {"주제 목록": topics, "응답": items}, schema)
    llm = {x["id"]: sorted(set(x["themes"])) for x in out["items"]}
    compared = []
    for it in items:
        rule, got = sorted(rule_themes(it["text"])), llm.get(it["id"])
        inter, union = set(rule) & set(got or []), set(rule) | set(got or [])
        compared.append({"id": it["id"], "text": it["text"], "rule": rule, "llm": got,
                         "exact": rule == got, "jaccard": 1.0 if not union else len(inter) / len(union)})
    n = len(compared)
    res = {"seconds": round(sec, 1), "n": n,
           "returned": sum(c["llm"] is not None for c in compared),
           "exact_match": sum(c["exact"] for c in compared),
           "mean_jaccard": round(sum(c["jaccard"] for c in compared) / n, 3),
           "items": compared}
    human = load_human_labels()
    scored = [c for c in compared if c["id"] in human]
    if scored:  # 사람 라벨이 있으면 규칙 대신 사람 기준 정확도도 낸다
        for c in scored:
            h = human[c["id"]]
            got = sorted(c["llm"] or [])
            u = set(got) | set(h)
            c.update(human=h, h_exact=got == h, h_jaccard=1.0 if not u else len(set(got) & set(h)) / len(u),
                     rule_h_exact=c["rule"] == h)
        res.update(human_n=len(scored), human_exact=sum(c["h_exact"] for c in scored),
                   human_jaccard=round(sum(c["h_jaccard"] for c in scored) / len(scored), 3),
                   rule_human_exact=sum(c["rule_h_exact"] for c in scored))
    return res


def read_csv_any(path):
    """엑셀이 저장한 CSV(CP949)와 UTF-8(BOM 유무) 모두 읽는다."""
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp949")
    return list(csv.DictReader(text.splitlines()))


def load_human_labels():
    """data/human_labels.csv 의 themes 칸이 채워진 행만 읽는다 (여러 주제는 | 로 구분)."""
    p = HERE / "data/human_labels.csv"
    if not p.exists():
        return {}
    out = {}
    for r in read_csv_any(p):
        t = (r.get("themes") or "").strip()
        if t:
            out[r["id"]] = sorted({x.strip() for x in t.split("|") if x.strip()})
    return out


# ---------- 과제 2: 펄스 문항 생성 → 평가 단계(qa_question) 통과율 ----------
TRIGGERS = {"order_gap": "소모품 주문 간격이 평소의 1.5배를 넘은 거래처", "as_done": "A/S 처리가 7일 안에 끝난 거래처",
            "install_30": "키오스크 설치 30일째 거래처", "device_drop": "키오스크 인화 수량이 4주 사이 20% 이상 줄어든 거래처"}


def task_generate(backend):
    schema = {"type": "object", "additionalProperties": False, "required": ["questions"],
              "properties": {"questions": {"type": "array", "items": {
                  "type": "object", "additionalProperties": False, "required": ["trigger", "text", "answer_type"],
                  "properties": {"trigger": {"type": "string", "enum": list(TRIGGERS)},
                                 "text": {"type": "string"},
                                 "answer_type": {"type": "string", "enum": ["1-5", "늘었다/비슷하다/줄었다", "text"]}}}}}}
    out, sec = ask(backend, "B2B 거래처에 보낼 짧은 펄스 설문 문항을 트리거마다 3개씩 쓴다. 한 문항은 한 가지만 묻고, "
                            "답을 유도하지 말고, 60자 이내로 쓴다. 답변 형식도 고른다.",
                   {"트리거": TRIGGERS}, schema)
    qs = [{**q, "issues": loop.qa_question(q["text"])} for q in out["questions"]]
    return {"seconds": round(sec, 1), "n": len(qs), "passed": sum(not q["issues"] for q in qs), "questions": qs}


# ---------- 과제 3: 안내 문안 → 숫자 환각·과장 표현 가드레일 ----------
def task_draft(backend):
    rows = loop.run()[0]
    risk = [r for r in rows if r["state"] == "위험" and r["actions"]]
    cases = [{"id": r["acc"]["account_id"], "거래처": f"{r['acc']['region']} {r['acc']['type']}",
              "상태": r["state"], "주제": r["themes"], "근거": r["evidence"],
              "제안": [a["name"] for a in r["actions"]]} for r in risk]
    schema = {"type": "object", "additionalProperties": False, "required": ["drafts"],
              "properties": {"drafts": {"type": "array", "items": {
                  "type": "object", "additionalProperties": False, "required": ["id", "message"],
                  "properties": {"id": {"type": "string"}, "message": {"type": "string"}}}}}}
    out, sec = ask(backend, "영업 담당자가 거래처 사장님께 보낼 3문장 이내의 정중한 안내 문안을 거래처마다 쓴다. "
                            "주어진 근거와 제안만 쓰고, 근거에 없는 숫자·약속·할인은 쓰지 않는다.",
                   {"거래처": cases}, schema)
    by_id = {c["id"]: c for c in cases}
    drafts = []
    for d in out["drafts"]:
        facts = set(re.findall(r"\d+(?:\.\d+)?", " ".join(by_id.get(d["id"], {}).get("근거", []))))
        nums = re.findall(r"\d+(?:\.\d+)?", d["message"])
        drafts.append({**d, "invented_numbers": [x for x in nums if x not in facts],
                       "overclaim": [w for w in OVERCLAIM if w in d["message"]]})
    flagged = sum(bool(d["invented_numbers"] or d["overclaim"]) for d in drafts)
    return {"seconds": round(sec, 1), "n": len(drafts), "flagged": flagged, "drafts": drafts}


# ---------- 과제 4: 미사용 평가셋(사람 라벨) → 정확도와 영업 판단 영향 ----------
def task_heldout(backend):
    """data/heldout_responses.csv: 모델 출력을 보기 전에 사람이 themes를 채운 24건. 모두 채워져야 돌아간다."""
    rows = read_csv_any(HERE / "data/heldout_responses.csv")
    missing = [r["id"] for r in rows if not (r.get("themes") or "").strip()]
    if missing:
        raise RuntimeError(f"사람 라벨이 비어 있음 {len(missing)}건 ({', '.join(missing[:5])}…). 먼저 themes 칸을 채울 것")
    catalog = loop.read("action_catalog.csv")

    canon = {k.replace(" ", ""): k for k in loop.THEMES}
    canon.update({"손님감소": "고객 감소", "고객감소": "고객 감소"})   # 라벨 표기 차이만 맞춘다(판단은 바꾸지 않음)

    def parse(t):
        t = t.strip()
        if t in ("", "-", "없음"):
            return []
        out = set()
        for x in t.replace("/", "|").replace(",", "|").split("|"):
            key = x.strip().replace(" ", "")
            if not key:
                continue
            if key not in canon:
                raise RuntimeError(f"목록에 없는 주제 표기: {x!r}")
            out.add(canon[key])
        return sorted(out)

    def num(v):
        return float(v) if v not in ("", None) else None

    items = [{"id": r["id"], "text": r["text"]} for r in rows]
    schema = {"type": "object", "additionalProperties": False, "required": ["items"],
              "properties": {"items": {"type": "array", "items": {
                  "type": "object", "additionalProperties": False, "required": ["id", "themes"],
                  "properties": {"id": {"type": "string"},
                                 "themes": {"type": "array", "items": {"type": "string", "enum": list(loop.THEMES)}}}}}}}
    out, sec = ask(backend, "B2B 거래처(사진관·셀프사진관·대리점·기업)의 설문 주관식 응답마다 주제를 주어진 정의에 맞춰 목록에서만 고른다. "
                            "응답에 근거가 없으면 빈 목록. 모든 id를 빠짐없이 돌려준다.",
                   {"주제 정의": loop.THEME_DEFS, "응답": items}, schema)
    llm = {x["id"]: sorted(set(x["themes"])) for x in out["items"]}
    miss, fp, cases = Counter(), Counter(), []
    for r in rows:
        human, model, rule = parse(r["themes"]), llm.get(r["id"], []), sorted(loop.rule_themes(r["text"]))
        s = {"gap_ratio": num(r["gap_ratio"]), "dev_trend": num(r["dev_trend"]), "c2": int(r["c2"]) if r["c2"] else None,
             "iv_ratio": num(r["iv_ratio"])}
        acc = {"type": r["type"]}
        st_h, st_m = loop.classify(s, human), loop.classify(s, model)
        act_h = [a["action_id"] for a in loop.propose(acc, st_h, human, catalog)]
        act_m = [a["action_id"] for a in loop.propose(acc, st_m, model, catalog)]
        for t in set(human) - set(model):
            miss[t] += 1
        for t in set(model) - set(human):
            fp[t] += 1
        u = set(human) | set(model)
        cases.append({"id": r["id"], "text": r["text"], "human": human, "model": model, "rule": rule,
                      "model_exact": model == human, "rule_exact": rule == human,
                      "jaccard": 1.0 if not u else len(set(human) & set(model)) / len(u),
                      "state_h": st_h, "state_m": st_m, "actions_h": act_h, "actions_m": act_m,
                      "decision_changed": (st_h != st_m) or (act_h != act_m)})
    n = len(cases)
    return {"seconds": round(sec, 1), "n": n, "model_exact": sum(c["model_exact"] for c in cases),
            "rule_exact": sum(c["rule_exact"] for c in cases),
            "mean_jaccard": round(sum(c["jaccard"] for c in cases) / n, 3),
            "decision_changed": sum(c["decision_changed"] for c in cases),
            "miss": dict(miss), "fp": dict(fp), "cases": cases}


def heldout_md(res):
    h = res["heldout"]
    lines = [f"# 미사용 평가셋 24건 사람 라벨 기준 평가 ({res['backend']}, {res['model']}, {res['ran_at']})", "",
             "라벨은 모델 출력을 보기 전에 작성자가 달았다(합성 응답, 작성자 1인 라벨이라는 한계). 규칙 결과는 참고용.", "",
             f"- 모델 완전 일치 **{h['model_exact']}/{h['n']}** (평균 자카드 {h['mean_jaccard']}), 규칙 완전 일치 {h['rule_exact']}/{h['n']} ({h['seconds']}초)",
             f"- 분류 차이가 거래처 상태나 제안까지 바꾼 사례: **{h['decision_changed']}/{h['n']}**",
             f"- 주제별 놓침: {h['miss'] or '없음'} · 잘못 붙임: {h['fp'] or '없음'}", "",
             "## 모델과 사람 라벨이 다른 사례", ""]
    diff = [c for c in h["cases"] if not c["model_exact"]]
    lines += [f"- {c['id']} \"{c['text']}\": 사람 {c['human']} / 모델 {c['model']} / 규칙 {c['rule']}"
              + (f" → 상태 {c['state_h']}→{c['state_m']}, 제안 {c['actions_h']}→{c['actions_m']}" if c["decision_changed"] else " (판단 불변)")
              for c in diff] or ["- 없음"]
    return "\n".join(lines) + "\n"


def summary_md(res):
    c, g, d = res["classify"], res["generate"], res["draft"]
    skip = "건너뜀"
    lines = [f"# LLM 경로 검증 ({res['backend']}, {res['model']}, {res['ran_at']})", "",
             "데이터는 모두 가상이다. 규칙 결과는 정답이 아니라 비교 기준이다.", "",
             "- 주관식 주제 분류: " + (skip if c.get("skipped") else
                                  f"{c['n']}건 중 {c['returned']}건 응답, 규칙과 완전 일치 {c['exact_match']}건, "
                                  f"평균 자카드 {c['mean_jaccard']} ({c['seconds']}초)"),
             *([f"- 사람 라벨 대비: LLM 정확 일치 {c['human_exact']}/{c['human_n']} (평균 자카드 {c['human_jaccard']}), "
                f"규칙 정확 일치 {c['rule_human_exact']}/{c['human_n']}"] if c.get("human_n") else []),
             "- 펄스 문항 생성: " + (skip if g.get("skipped") else
                                 f"{g['n']}개 중 평가 단계 통과 {g['passed']}개 ({g['seconds']}초)"),
             "- 안내 문안: " + (skip if d.get("skipped") else
                              f"{d['n']}건 중 가드레일 경고 {d['flagged']}건 ({d['seconds']}초)"), "",
             "## 분류가 규칙과 다른 응답", ""]
    lines += [f"- {x['id']} \"{x['text']}\": 규칙 {x['rule']} / LLM {x['llm']}" for x in c["items"] if not x["exact"]] or ["- 없음"]
    lines += ["", "## 평가 단계에서 걸린 생성 문항", ""]
    lines += [f"- [{q['trigger']}] {q['text']} → {', '.join(q['issues'])}" for q in g["questions"] if q["issues"]] or ["- 없음"]
    lines += ["", "## 안내 문안", ""]
    lines += [f"- {x['id']}: {x['message']}" + (f" ⚠ 근거에 없는 숫자 {x['invented_numbers']}" if x["invented_numbers"] else "")
              + (f" ⚠ 과장 표현 {x['overclaim']}" if x["overclaim"] else "") for x in d["drafts"]]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["agy", "codex", "openai"], required=True)
    ap.add_argument("--tasks", default="classify,generate,draft")
    ap.add_argument("--tag", default="", help="결과 파일 이름 꼬리표 (예: v1)")
    args = ap.parse_args()
    backend, wanted = args.backend, args.tasks.split(",")
    model = {"agy": f"Gemini ({AGY_MODEL}, Antigravity CLI)", "codex": "OpenAI (Codex CLI 기본 모델)",
             "openai": os.environ.get("OPENAI_MODEL", "gpt-5-mini")}[backend]
    res = {"backend": backend, "model": model, "ran_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
    if "heldout" in wanted:  # 미사용 평가셋은 따로 보고서를 낸다
        res["heldout"] = task_heldout(backend)
        stem = f"heldout_eval_{backend}" + (f"_{args.tag}" if args.tag else "")
        (HERE / "out" / f"{stem}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        (HERE / "out" / f"{stem}.md").write_text(heldout_md(res), encoding="utf-8")
        print(heldout_md(res))
        return
    for name, fn in (("classify", task_classify), ("generate", task_generate), ("draft", task_draft)):
        if name not in wanted:
            res[name] = {"skipped": True, "seconds": 0, "n": 0, "returned": 0, "exact_match": 0, "mean_jaccard": 0,
                         "passed": 0, "flagged": 0, "items": [], "questions": [], "drafts": []}
            continue
        try:
            res[name] = fn(backend)
            print(f"{name}: 완료 ({res[name]['seconds']}초)")
        except Exception as e:  # 한 과제가 실패해도 나머지는 돌린다
            res[name] = {"error": str(e), "seconds": 0, "n": 0, "returned": 0, "exact_match": 0, "mean_jaccard": 0,
                         "passed": 0, "flagged": 0, "items": [], "questions": [], "drafts": []}
            print(f"{name}: 실패 → {e}")
    out, stem = HERE / "out", f"llm_eval_{backend}" + (f"_{args.tag}" if args.tag else "")
    (out / f"{stem}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / f"{stem}.md").write_text(summary_md(res), encoding="utf-8")
    print(summary_md(res))


if __name__ == "__main__":
    main()
