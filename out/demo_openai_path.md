# OpenAI 모델 경로 실행 기록 (2026-11-01 13:38, 백엔드 codex)

가상 데이터. 단계마다 별도 프로세스로 실행했고, 승인·응답은 파일(`out/approvals.jsonl`, `data/pulse_responses.csv`)에 남는다.

## 1. LLM 경로로 한 바퀴 (`LLM(OpenAI 모델, Codex CLI) 모드 · 거래처 24 · 제안 11 · 설문 6 → out/weekly.html`)
- LLM 호출 2회 codex 28초, codex 28초 · 대체 0회
- A08 설문: P_gap1 최근 한 달 매장 손님 수는 어떻게 변했습니까?, P_gap2 주문이 줄어든 가장 큰 이유는 무엇입니까?
- A08 제안(응답 전): [('ACT06', '승인 대기')]

## 2. 설문 발송 승인
- A08: 설문 2문항 발송 승인 기록 (담당 박영업)

## 3. 모의 응답 취합
- A08: P_gap1 응답 기록 → data/pulse_responses.csv
- A08: P_gap2 응답 기록 → data/pulse_responses.csv

## 4. 응답을 결합해 재분류·제안 (`LLM(OpenAI 모델, Codex CLI) 모드 · 거래처 24 · 제안 10 · 설문 5 → out/weekly.html`)
- LLM 호출 2회 · 대체 0회
- A08 주제: 가격 고민, 공급처 변경
- A08 제안: [('ACT06', '승인 대기'), ('ACT11', '승인 대기')]
- 안내 문안(LLM): 사장님, 인화지 비용으로 촬영 가격 인상을 고민하시고, 현재 인터넷에서 더 저렴하게 인화지를 구매하고 계시다는 말씀을 확인했습니다. 가격·레이아웃 설정 가이드와 소모품 정기 배송·가격 안내를 받아보시길 제안드립니다.
- 출력 가드레일: 없음
- 설문 재발송 여부: 제외(최근 응답 → 피로 관리)

## 5. 실행 승인과 중복 승인
- A08 ACT06: 실행 승인 기록 (담당 박영업)
- 다시 승인: A08 ACT06: 이미 승인돼 있다 (중복 기록 안 함)

## 6. 재시작 뒤 조회 (새 프로세스)
```
2026-11-01 13:37:01  survey_send A08  P_gap1,P_gap2  담당 박영업  (주문 지연 트리거 설문)
2026-11-01 13:38:07  action_exec A08  ACT06  담당 박영업
```
- 다시 실행한 결과(`규칙 모드 · 거래처 24 · 제안 11 · 설문 5 → out/weekly.html`)의 A08 제안 상태: [('ACT06', '승인됨'), ('ACT11', '승인 대기')], 설문 발송 승인: True

## 7. 호출 실패 유도 → 규칙 대체 (`규칙(대체) 모드 · 거래처 24 · 제안 11 · 설문 5 → out/weekly.html`)
- 대체 기록: [{'step': 'llm_fallback', 'backend': 'codex', 'error': 'FileNotFoundError', 'ms': 4.8}, {'step': 'llm_fallback', 'backend': 'codex', 'error': 'FileNotFoundError', 'ms': 3.4}]

## 명령 기록

| 명령 | 초 | 종료 코드 |
|---|---|---|
| `approve.py reset` | 0.1 | 0 |
| `make_data.py` | 0.1 | 0 |
| `loop.py` | 55.9 | 0 |
| `approve.py survey A08 --by 박영업 --note 주문 지연 트리거 설문` | 0.1 | 0 |
| `approve.py respond A08 P_gap1 비슷하다 --date 2026-11-03` | 0.1 | 0 |
| `approve.py respond A08 P_gap2 인화지는 인터넷에서 더 싸게 사고 있어요 --date 2026-11-03` | 0.1 | 0 |
| `loop.py` | 65.7 | 0 |
| `approve.py action A08 ACT06 --by 박영업` | 0.2 | 0 |
| `approve.py action A08 ACT06 --by 박영업` | 0.2 | 0 |
| `approve.py show A08` | 0.2 | 0 |
| `loop.py` | 0.3 | 0 |
| `loop.py` | 0.3 | 0 |
