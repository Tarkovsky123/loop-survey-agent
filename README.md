# LOOP: 설문–제안 순환 에이전트 시제품

> **English summary.** A working prototype of a B2B survey-to-sales agent: order, A/S and kiosk signals decide *when and whom* to survey; survey answers explain *why*; the agent proposes evidence-backed sales actions that a human approves, and a hold-out control group measures the effect. Numbers, triggers and account states are computed in code; the LLM only classifies free text and drafts messages under a strict JSON schema, behind input/output guardrails. Approvals and mock responses persist to files and survive restarts. Standard-library Python, automated tests, a model-swappable LLM evaluation (Gemini via Antigravity CLI, OpenAI via Codex CLI or the Responses API), a fault-injection test for the guardrails, and an end-to-end run on the OpenAI path. **All data is synthetic.**

LOTTE AX Challenge 2026 예선의 한국후지필름 과제(설문 기반 매출 확대 제안 AI 에이전트)를 위해 만든 시제품이다. 데이터가 '언제·누구에게 물을지'를 정하고, 설문이 '왜'를 알려 준다. 가상 거래처 24곳으로 한 바퀴를 돌려 영업 담당자의 주간 승인 대기열을 만든다. **데이터는 모두 가상이며 실제 한국후지필름 데이터가 아니다.**

## 실행 (Python 3.10+, 표준 라이브러리만)

```bash
python -X utf8 make_data.py            # data/*.csv 가상 데이터 생성 (seed 7)
python -X utf8 loop.py                 # out/weekly.html 등 생성 (기본: 규칙 모드)
python -X utf8 test_loop.py            # 규칙·가드레일·파이프라인 테스트, "ok" 출력
LLM_BACKEND=codex python -X utf8 loop.py    # 주관식 분류·안내 문안을 OpenAI 모델(Codex CLI)로
LLM_BACKEND=openai OPENAI_API_KEY=... python -X utf8 loop.py   # Responses API (모델은 OPENAI_MODEL)
```

LLM 호출이 실패하거나 형식이 어긋나면 규칙으로 대체하고 `out/trace.jsonl`에 `llm_fallback`으로 남긴다(화면 상단에 모드 표시). 숫자·트리거·상태는 어느 모드에서든 코드로만 계산한다.

## 흐름

| 단계 | 하는 일 | 위치 |
|---|---|---|
| 정기 설문 자동 생성 | 주문 간격 1.5배 초과·A/S 완료 7일 이내·설치 30일·인화 수량 20% 감소를 트리거로 설문 생성. 평가 단계가 이중·유도·긴 질문을 제거하고, 14일 안에 응답한 거래처는 피로 관리로 제외 | `triggers_of`, `qa_question`, `build_survey` |
| 응답 취합·결합 | 주문·기기·A/S 신호와 정기 설문(C1–C4)·트리거 응답(`data/pulse_responses.csv`)을 거래처 단위로 결합. 주관식은 주제 10개(기기 고장·고객 감소·콘텐츠 식상·가격 고민·확장 의향·수요 증가·재고 부담·업태 전환·배송 불만·공급처 변경)로 분류 | `signals`, `code_themes_all` |
| 매출 확대 제안·액션플랜 | 위험 → 확장 → 성장 → 정체 → 안정 순으로 상태 분류, 액션 카탈로그에서 최대 3개 제안, 모든 제안에 근거 연결, 안내 문안 작성 | `classify`, `propose`, `messages_all` |
| 실행·검증 | 위험이 아닌 거래처 20%를 대조군으로 두어 제안을 보류하고 성과만 측정. 담당자가 설문 발송과 제안 실행을 승인하면 `approve.py`가 파일에 기록 | `holdout_ids`, `approve.py` |

## 가드레일

- **입력**: 응답 속 지시문 의심 표현(`무시하`, `이전 지시`, `프롬프트`, `시스템`, `명령:` 등)이 있으면 그 응답을 분류·문안 입력에서 빼고 근거에 '검토 보류'로만 남긴다(`sanitize_text`). 주제 분류 출력은 JSON 스키마가 주제 목록 밖 값을 막는다.
- **출력**: 안내 문안에서 근거·배정 제안에 없는 숫자, 배정되지 않은 카탈로그 제안, 카탈로그 밖 약속(무상 교체·환불 등), 할인 한도(10%) 초과, 과장 표현을 찾아 '가드레일 보류'로 표시한다(`check_message`). 보류된 제안은 `approve.py`가 승인하지 않는다.
- **결함 심기 시험**: `python -X utf8 guardrail_test.py --backend codex` → `out/guardrail_test.md`. 일부러 틀린 문안 8건·정상 4건, 지시문 응답 4건·정상 4건(어휘가 겹치는 정상 2건 포함)을 넣어 차단·보류·통과를 기록한다. 2026-11-01 결과 **20/21 PASS**: 틀린 문안 8건 전부 차단, 지시문 4건 전부 보류, 정상 문안 4건 통과. 기대와 다른 1건은 "시스템 점검 때문에 하루 쉬었어요"를 '시스템' 어휘 때문에 보류한 **과잉 차단**이다(담당자 검토로 복구되므로 놓침보다 낫다고 보고 그대로 둠). 지시문 4건을 모델 분류에 그대로 넣어도 스키마 때문에 주제 목록 밖 값은 나오지 않았다(모두 빈 목록).

## 승인 기록과 OpenAI 경로 실행

```bash
python approve.py survey A08 --by 박영업            # 설문 발송 승인
python approve.py respond A08 P_gap2 "인화지는 인터넷에서 더 싸게 사고 있어요"   # 모의 응답
python approve.py action A08 ACT11 --by 박영업      # 제안 실행 승인 (중복 승인은 기록하지 않음)
python approve.py show A08                          # 재시작 뒤에도 파일에서 조회
```

`python -X utf8 demo_openai_path.py`는 한 거래처(A08)를 OpenAI 모델 경로(Codex CLI)로 끝까지 통과시키고 `out/demo_openai_path.md`에 기록한다: 설문 생성(LLM 분류·문안) → 발송 승인 기록 → 모의 응답 취합 → 재분류(응답에서 '공급처 변경' 주제가 새로 잡혀 '소모품 정기 배송·가격 안내' 제안이 추가됨) → LLM 안내 문안과 출력 가드레일 통과 → 실행 승인 기록과 중복 승인 거부 → 새 프로세스에서 기록 조회 → 호출 실패를 유도해 규칙 대체 확인. 단계마다 별도 프로세스로 실행한다. Responses API 실행은 키가 있을 때 `LLM_BACKEND=openai`로 같은 절차를 돌리면 된다(이 저장소의 기록은 Codex CLI 경로다).

## 출력

- `out/weekly.html`: 월요일 승인 대기열 화면(승인된 제안·설문, 가드레일 보류 표시)
- `out/actions.csv`: 거래처별 제안과 상태(승인 대기 / 승인됨 / 가드레일 보류 / 측정만)
- `out/surveys.json`: 이번 주 트리거 설문과 발송 승인 여부
- `out/trace.jsonl`: 단계별 처리 시간, LLM 호출·대체 기록
- `out/approvals.jsonl`: 승인 기록 (`approve.py`)

## LLM 경로 검증 (`llm_eval.py`)

같은 지시문과 JSON 스키마를 백엔드만 바꿔 보내, 실제 모델이 세 가지 일을 제대로 하는지 본다.

```bash
python -X utf8 llm_eval.py --backend agy     # Google Antigravity CLI (Gemini)
python -X utf8 llm_eval.py --backend codex   # OpenAI Codex CLI
python -X utf8 llm_eval.py --backend openai  # Responses API (OPENAI_API_KEY 필요)
```

| 과제 | 확인 방법 | Gemini (2026-10-31) | OpenAI 모델, Codex CLI (2026-11-01) |
|---|---|---|---|
| 주관식 주제 분류(개발용 18건) | **규칙 결과와의** 완전 일치 수·평균 자카드 | v1 13/18(주제 이름만) → v2 **15/18**(주제 정의 추가) | **16/18** (자카드 0.917) |
| 트리거 문항 생성(트리거 4종 × 3개) | 평가 단계(`qa_question`) 통과율 | **12/12** | **12/12** |
| 위험 거래처 안내 문안 | 근거에 없는 숫자·과장 표현 가드레일 | 3건 중 경고 **0건** | 3건 중 경고 **0건** |

규칙 결과는 정답이 아니라 비교 기준이다. 남은 차이 가운데 A19("매장들 주문이 줄어서 재고가 많이 남아요")는 규칙이 '고객 감소'를 잘못 붙인 오탐으로 판정했다. 결과 원본은 `out/llm_eval_<backend>_<tag>.json`·`.md`.

**사람 라벨 기준 정확도 (미사용 평가셋 24건, 2026-11-01)**: `data/heldout_responses.csv`는 모델 출력을 보기 전에 작성자가 라벨을 단 24건(복수 주제·애매한 표현·정보 부족·어휘 겹침 포함)이다. `python -X utf8 llm_eval.py --backend <codex|agy> --tasks heldout` → `out/heldout_eval_<backend>.md`.

| | OpenAI 모델 (Codex CLI) | Gemini | 규칙 |
|---|---|---|---|
| 사람 라벨과 완전 일치 | **17/24** (자카드 0.792) | 16/24 (0.729) | 12/24 |
| 분류 차이가 상태·제안을 바꾼 사례 | 4/24 | 5/24 | — |

두 모델이 똑같이 틀린 곳: 복수 주제에서 하나를 놓침(H01 '프린터가 걸려서 손님이 기다리다 가요' → 기기 고장만), 가격 불만의 '손님은 줄고'를 고객 감소로 더 붙임(H06), '배송을 더 자주'를 배송 불만이 아니라 수요 증가로 읽음(H19, 라벨 자체도 논쟁 여지), 행사 기기 사전 확보를 수요 증가로 안 잡음(H23). 작성자 1인이 단 합성 자료라는 한계가 있고, 이 결과를 보고 주제 정의를 고치면 이 세트는 더 이상 미사용 평가셋이 아니므로 그대로 둔다. 입력 방법은 `data/human_labels_README.md`.

## 평가 설계 (측정 약속)

| 항목 | 설계 |
|---|---|
| 주 지표 | 거래처당 월 소모품 매출. 보조: 재주문율, 주문 간격, 인화 수량(동의 거래처), 제안 채택률, 거래처당 제안 준비 시간 |
| 비위험 거래처 | 측정 주기(4주, 파일럿 3개월 = 3주기) 동안 집단을 **고정**한다. 업종과 최근 90일 매출 분위로 층화해 무작위 배정하고 대조군은 20%. 효과 = (개입군 사후 − 사전) − (대조군 사후 − 사전), 즉 차이의 차이. 교대는 주기가 끝난 뒤 다음 배정에서 하고, 직전 주기 개입군은 이월 효과를 피하려 다음 주기 대조군에서 뺀다 |
| 위험 거래처 | 모두 필요한 조치를 제공한다. 도입 전 분기 대비 이탈률·주문 회복은 **관찰 결과**로 보고하며 개입의 인과 효과로 확정하지 않는다 |
| 손익 | 쿠폰, 담당자 시간(건당 분), 모델 호출 비용을 집계해 순효과를 낸다. 위험 거래처의 추정 유지 매출은 실험으로 측정한 증분 매출에 합산하지 않는다 |
| 중복 집계 금지 | 이탈을 막은 거래처의 기준 매출과 유지 거래처의 추가 구매를 거래처별로 분리해 더한다 |
| 시제품 현재 | `holdout_ids`가 위험 제외·20%·해시 기반 고정 배정까지 구현. 주기 교대와 성과 집계는 본선 과제 |

## 만든 방식

코드는 AI 코딩 에이전트(Claude Code)가 작성했다. 작성자는 흐름과 판정 규칙, 합격 기준, 평가·가드레일 시험 설계를 정하고 결과를 검토해 고쳤다. 외부 검수(업계 관점, OpenAI 모델 심사)에서 받은 지적 가운데 '규칙 일치도는 정확도가 아니다', '위험 거래처를 뺀 대조군으로는 이탈 효과를 못 잰다', '가드레일은 정상 문안 통과만으로 검증되지 않는다'를 받아들여 미사용 평가셋, 평가 설계, 결함 심기 시험을 더했다.
