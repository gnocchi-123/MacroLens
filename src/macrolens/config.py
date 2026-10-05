"""M1 고정 설정. 실제 연구용 섹터 계수는 아직 없다."""

INDICATORS = {
    "INDPRO": {
        "name": "산업생산",
        "factor": "growth",
        "frequency": "Monthly",
        "units": "Index 2017=100",
        "seasonal_adjustment": "Seasonally Adjusted",
        "transform": "yoy",
        "window": 60,
        "max_age_days": 75,
    },
    "UNRATE": {
        "name": "실업률",
        "factor": "employment",
        "frequency": "Monthly",
        "units": "Percent",
        "seasonal_adjustment": "Seasonally Adjusted",
        "transform": "negative_3m",
        "window": 60,
        "max_age_days": 75,
    },
    "CPIAUCSL": {
        "name": "소비자물가",
        "factor": "inflation",
        "frequency": "Monthly",
        "units": "Index 1982-1984=100",
        "seasonal_adjustment": "Seasonally Adjusted",
        "transform": "yoy",
        "window": 60,
        "max_age_days": 75,
    },
    "DGS10": {
        "name": "10년 국채금리",
        "factor": "rates",
        "frequency": "Daily",
        "units": "Percent",
        "seasonal_adjustment": "Not Seasonally Adjusted",
        "transform": "difference_28d",
        "window": 1260,
        "max_age_days": 7,
    },
}

# 경제적 근거가 있는 섹터명/ETF로 가장하지 않는 실행 확인용 계수.
DEMO_MODEL = {
    "version": "demo-v1",
    "purpose": "실행 확인용 가상 계수 — 미검증 연구용",
    "sectors": {
        "가상 섹터 A": {"growth": 2, "employment": 1, "inflation": -1, "rates": -1},
        "가상 섹터 B": {"growth": -1, "employment": 1, "inflation": 2, "rates": 1},
        "가상 섹터 C": {"growth": 0, "employment": -1, "inflation": -1, "rates": 2},
    },
}
