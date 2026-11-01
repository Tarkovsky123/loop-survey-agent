"""LOOP 시연용 가상 데이터 생성. 실제 한국후지필름 데이터가 아니다.
실행: python make_data.py  → data/*.csv
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(7)
DATA = Path(__file__).parent / "data"
START, END = date(2026, 1, 5), date(2026, 10, 30)

# (id, 유형, 지역, 담당, 설치일, 기기데이터 동의, 시나리오)
ACCOUNTS = [
    ("A01", "셀프사진관", "성수", "김영업", "2025-11-10", True, "risk"),
    ("A02", "셀프사진관", "홍대", "김영업", "2025-12-01", True, "flat"),
    ("A03", "셀프사진관", "강남", "이영업", "2026-02-15", True, "grow"),
    ("A04", "셀프사진관", "부산 서면", "박영업", "2025-10-20", True, "expand"),
    ("A05", "셀프사진관", "대구 동성로", "박영업", "2026-03-02", True, "as_done"),
    ("A06", "셀프사진관", "인천 송도", "이영업", "2026-01-20", True, "stable"),
    ("A07", "셀프사진관", "수원", "이영업", "2026-10-01", False, "new"),
    ("A08", "셀프사진관", "대전", "박영업", "2025-12-15", False, "price"),
    ("A09", "셀프사진관", "광주", "박영업", "2026-04-10", False, "stable"),
    ("A10", "셀프사진관", "제주", "김영업", "2026-05-05", False, "stable"),
    ("A11", "사진관", "종로", "김영업", "2019-03-01", False, "studio_risk"),
    ("A12", "사진관", "마포", "김영업", "2020-06-01", False, "stable"),
    ("A13", "사진관", "분당", "이영업", "2018-09-01", False, "as_done"),
    ("A14", "사진관", "일산", "이영업", "2021-01-01", False, "stable"),
    ("A15", "사진관", "울산", "박영업", "2017-05-01", False, "stable"),
    ("A16", "사진관", "청주", "박영업", "2022-02-01", False, "stable"),
    ("A17", "사진관", "전주", "박영업", "2019-11-01", False, "stable"),
    ("A18", "사진관", "춘천", "김영업", "2020-08-01", False, "stable"),
    ("A19", "대리점", "경기 남부", "이영업", "2016-01-01", False, "dealer_risk"),
    ("A20", "대리점", "영남", "박영업", "2015-01-01", False, "stable"),
    ("A21", "대리점", "호남", "박영업", "2018-01-01", False, "grow"),
    ("A22", "기업·행사", "서울", "김영업", "2025-06-01", False, "stable"),
    ("A23", "기업·행사", "경기", "이영업", "2025-09-01", False, "stable"),
    ("A24", "기업·행사", "부산", "박영업", "2026-02-01", False, "stable"),
]
BASE_INTERVAL = {"셀프사진관": 14, "사진관": 21, "대리점": 30, "기업·행사": 45}
ITEM = {"셀프사진관": ("인화지", 120_000, 3), "사진관": ("인화지", 120_000, 2),
        "대리점": ("인화지(대량)", 110_000, 25), "기업·행사": ("인스탁스 필름 묶음", 90_000, 10)}


def write(name, header, rows):
    with open(DATA / name, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def orders_for(acc):
    aid, kind, _, _, inst, _, scen = acc
    item, price, qty0 = ITEM[kind]
    d = max(START, date.fromisoformat(inst))
    rows = []
    while d <= END:
        iv, qty = BASE_INTERVAL[kind], qty0
        late = d >= date(2026, 8, 1)
        if scen in ("risk", "studio_risk", "dealer_risk", "price") and late:
            iv = int(iv * 1.8)
        if scen in ("grow", "expand") and d >= date(2026, 7, 1):
            iv, qty = int(iv * 0.75), qty0 + 1
        rows.append((d.isoformat(), aid, item, qty, qty * price))
        if scen == "flat" and d.month in (8, 9, 10) and random.random() < 0.3:
            rows.append((d.isoformat(), aid, "프레임 콘텐츠", 1, 50_000))
        d += timedelta(days=max(3, iv + random.randint(-2, 2)))
    # 위험 시나리오는 최근 주문이 끊긴 상태로 만든다
    cut = {"risk": "2026-09-20", "studio_risk": "2026-09-05", "dealer_risk": "2026-08-25", "price": "2026-09-25"}
    if scen in cut:
        rows = [r for r in rows if r[0] <= cut[scen]]
    return rows


def device_weekly(acc):
    aid, _, _, _, _, consent, scen = acc
    if not consent:
        return []
    shape = {"risk": (1500, 800), "flat": (900, 900), "grow": (1000, 1500),
             "expand": (1300, 1900), "as_done": (1100, 1100), "stable": (1100, 1100)}[scen]
    rows, end = [], date(2026, 11, 1)
    for i in range(12):
        week_end = end - timedelta(weeks=11 - i)
        prints = shape[0] + (shape[1] - shape[0]) * i / 11 + random.randint(-40, 40)
        if scen == "as_done" and i == 10:
            prints *= 0.5  # 고장 주간
        rows.append((week_end.isoformat(), aid, int(prints)))
    return rows


def main():
    DATA.mkdir(exist_ok=True)
    write("accounts.csv", ["account_id", "type", "region", "rep", "install_date", "device_consent"],
          [(a[0], f"{a[1]}", a[2], a[3], a[4], int(a[5])) for a in ACCOUNTS])
    write("orders.csv", ["date", "account_id", "item", "qty", "amount"],
          [r for a in ACCOUNTS for r in orders_for(a)])
    write("device_weekly.csv", ["week_end", "account_id", "prints"],
          [r for a in ACCOUNTS for r in device_weekly(a)])
    write("as_logs.csv", ["opened", "resolved", "account_id", "issue"], [
        ("2026-09-08", "2026-09-12", "A01", "프린터 용지 걸림"),
        ("2026-10-14", "2026-10-21", "A01", "카메라 인식 오류"),
        ("2026-10-24", "2026-10-28", "A05", "프린터 헤드 교체"),
        ("2026-10-27", "2026-10-29", "A13", "미니랩 롤러 점검"),
    ])
    # 분기 정기 설문(코어 문항) 응답과 최근 펄스 설문 응답
    core = {
        "A01": (2, 2, 4, "기계가 자꾸 멈춰서 손님이 그냥 나가요. 요즘 손님도 줄었어요"),
        "A02": (3, 3, 6, "프레임이 다 비슷해서 단골이 식상해해요"),
        "A03": (5, 5, 9, "주말에는 대기줄이 생길 정도예요"),
        "A04": (5, 5, 9, "손님이 많아서 2호점을 고민 중입니다"),
        "A05": (4, 4, 8, "수리가 빨라서 좋았어요"),
        "A06": (4, 4, 8, "특별한 불편은 없어요"),
        "A08": (3, 3, 6, "인화지 값이 올라서 촬영 가격을 올려야 하나 고민이에요"),
        "A09": (4, 4, 8, "무난합니다"),
        "A10": (4, 4, 7, "관광객 많은 달은 좋아요"),
        "A11": (2, 2, 5, "인화 손님이 줄어서 셀프 촬영존을 들일지 고민입니다"),
        "A12": (4, 4, 8, "졸업 시즌 증명사진이 바빠요"),
        "A13": (4, 4, 7, "롤러 점검 후 괜찮아졌어요"),
        "A14": (4, 4, 8, "괜찮습니다"),
        "A19": (2, 2, 5, "매장들 주문이 줄어서 재고가 많이 남아요"),
        "A20": (4, 4, 8, "배송만 조금 빨랐으면 해요"),
        "A21": (5, 5, 9, "거래처가 늘고 있어요"),
        "A22": (4, 4, 8, "행사 일정이 몰릴 때 렌탈이 부족해요"),
    }
    rows = []
    for aid, (sat, rebuy, nps, text) in core.items():
        day = f"2026-09-{random.randint(15, 25)}"
        rows += [(day, aid, "core", "C1", sat), (day, aid, "core", "C2", rebuy),
                 (day, aid, "core", "C3", nps), (day, aid, "core", "C4", text)]
    rows += [("2026-10-22", "A06", "pulse", "P_dev1", "요즘 커플 손님이 늘었어요"),
             ("2026-10-25", "A12", "pulse", "P_gap1", "비슷하다"),
             ("2026-10-26", "A19", "pulse", "P_gap1", "줄었다")]  # 최근 응답 → 피로 관리로 이번 주 발송 제외
    write("survey_responses.csv", ["date", "account_id", "survey", "q_id", "answer"], rows)


if __name__ == "__main__":
    main()
    print("data/ 생성 완료")
