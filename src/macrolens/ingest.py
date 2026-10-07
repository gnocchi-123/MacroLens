"""명시적으로 분리된 합성 샘플과 FRED 스냅샷 수집."""

import json
import math
import socket
import ssl
import sys
from datetime import UTC, date, datetime, timedelta
from http.client import HTTPException
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from .config import INDICATORS

SAMPLE_AS_OF = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)


class DataError(ValueError):
    """비밀값을 포함하지 않는 사용자용 오류."""


def month_shift(day, months):
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def observation(day, value, available_at, retrieved_at, vintage_date):
    return {
        "observation_date": day.isoformat(),
        "value": value,
        "published_at": None,
        "available_at": available_at.isoformat(),
        "retrieved_at": retrieved_at.isoformat(),
        "vintage_date": vintage_date,
    }


def sample_snapshot():
    """고정 시점의 합성 자료. 경제 관측값 또는 발표 일정이 아니다."""
    series = {}
    for sid, spec in INDICATORS.items():
        rows = []
        if spec["frequency"] == "Monthly":
            for i in range(100):
                day = month_shift(date(2018, 7, 1), i)
                available = datetime.combine(day + timedelta(days=45), datetime.min.time(), UTC)
                if available > SAMPLE_AS_OF:
                    continue
                value = (
                    4 + 0.6 * math.sin(i / 7)
                    if sid == "UNRATE"
                    else 100 + i * 0.3 + 2 * math.sin(i / 5)
                )
                rows.append(observation(day, value, available, SAMPLE_AS_OF, "synthetic-v1"))
        else:
            for i in range((SAMPLE_AS_OF.date() - date(2019, 1, 1)).days):
                day = date(2019, 1, 1) + timedelta(days=i)
                available = datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC)
                if day.weekday() >= 5 or available > SAMPLE_AS_OF:
                    continue
                value = 3 + 0.5 * math.sin(i / 65) + 0.2 * math.cos(i / 17)
                rows.append(observation(day, value, available, SAMPLE_AS_OF, "synthetic-v1"))
        series[sid] = {
            "metadata": {k: spec[k] for k in ("units", "frequency", "seasonal_adjustment")},
            "source": "synthetic://macrolens/sample-v1",
            "availability_policy": "가상 이용 가능 시각 (샘플 전용)",
            "observations": rows,
        }
    return {"mode": "sample", "as_of": SAMPLE_AS_OF.isoformat(), "series": series, "errors": {}}


def connection_error(error):
    """예외 문자열/URL/본문을 출력하지 않고 원인 종류만 분류한다."""
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "FRED 시간 초과: 연결 또는 응답 읽기 대기(30초 설정)"
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "FRED TLS 인증서 검증 실패"
    if isinstance(reason, ssl.SSLError):
        return "FRED TLS 연결 오류"
    if isinstance(reason, socket.gaierror):
        return "FRED DNS 이름 해석 실패"
    if isinstance(reason, ConnectionError):
        return "FRED 연결 거부·중단 오류"
    return "FRED 통신 오류: " + type(reason).__name__


def request_json(endpoint, params, api_key):
    url = (
        "https://api.stlouisfed.org/fred/"
        + endpoint
        + "?"
        + urlencode({**params, "api_key": api_key, "file_type": "json"})
    )
    try:
        # Codespaces에서 확인된 Python 기본 User-Agent를 사용한다.
        with urlopen(url, timeout=30) as response:
            result = json.load(response)
    except HTTPError as exc:
        # HTTP 오류 본문/URL에는 API 키가 포함될 수 있어 출력하거나 저장하지 않는다.
        raise DataError(f"FRED HTTP {exc.code}: 키·사용량·요청을 확인하세요.") from None
    except (URLError, TimeoutError, OSError, HTTPException) as exc:
        raise DataError(connection_error(exc)) from None
    except (ValueError, UnicodeError):
        raise DataError("FRED 응답을 JSON으로 해석할 수 없음 (본문은 비공개)") from None
    if not isinstance(result, dict) or "error_code" in result:
        raise DataError("FRED 응답 형식 또는 API 오류.")
    return result


def parse_rows(payload, retrieved_at, vintage):
    rows = payload["observations"]
    if not isinstance(rows, list) or int(payload["count"]) != len(rows):
        raise DataError("FRED 관측 응답 누락 또는 페이지 초과.")
    parsed = []
    for row in rows:
        value = None if row["value"] == "." else float(row["value"])
        if value is not None and not math.isfinite(value):
            raise DataError("비유한 FRED 관측값.")
        item = observation(
            date.fromisoformat(row["date"]), value, retrieved_at, retrieved_at, vintage
        )
        # 요청한 실시간 기간은 실제 최초 발표 시각과 같지 않다.
        item["realtime_start"] = row.get("realtime_start")
        item["realtime_end"] = row.get("realtime_end")
        parsed.append(item)
    return parsed


def fred_snapshot(api_key):
    if not api_key or not api_key.strip():
        raise DataError("FRED_API_KEY 환경변수를 설정하세요. 키를 대화에 붙여 넣지 마세요.")
    api_key = api_key.strip()
    if any(character.isspace() for character in api_key):
        raise DataError("API 키 안에 공백/줄바꿈이 있습니다. 키만 다시 입력하세요.")
    # 장중 발표시각을 아는 척하지 않도록 미국 중부시간 전일까지의 vintage만 요청한다.
    started = datetime.now(UTC)
    vintage = (
        started.astimezone(ZoneInfo("America/Chicago")).date() - timedelta(days=1)
    ).isoformat()
    start = (date.fromisoformat(vintage) - timedelta(days=365 * 10)).isoformat()
    series, errors = {}, {}
    for index, sid in enumerate(INDICATORS, 1):
        stage = "메타데이터"
        timer = monotonic()

        def progress(message):
            print(f"[{index}/{len(INDICATORS)}] {sid} {message}", file=sys.stderr, flush=True)

        progress("메타데이터 요청 중 (연결/읽기 타임아웃 30초)")
        params = {"series_id": sid, "realtime_start": vintage, "realtime_end": vintage}
        try:
            metadata = request_json("series", params, api_key)["seriess"][0]
            if metadata["id"] != sid:
                raise DataError("FRED 지표 ID 불일치.")
            stage = "관측값"
            progress("관측값 요청 중 (최근 약 10년)")
            payload = request_json(
                "series/observations",
                {
                    **params,
                    "observation_start": start,
                    "observation_end": vintage,
                    "sort_order": "asc",
                    "limit": 100000,
                    "units": "lin",
                },
                api_key,
            )
            retrieved = datetime.now(UTC)
            series[sid] = {
                "source": f"https://fred.stlouisfed.org/series/{sid}",
                "metadata": metadata,
                "availability_policy": "수집 시각부터 사용; 전일 vintage; 실제 발표시각 미확인",
                "observations": parse_rows(payload, retrieved, vintage),
                "raw_response": payload,
            }
            progress(
                f"수집 완료: {len(series[sid]['observations'])}개 / {monotonic() - timer:.1f}초"
            )
        except DataError as exc:
            errors[sid] = f"{stage}: {exc}"
            progress(f"실패 / {monotonic() - timer:.1f}초 / {errors[sid]}")
        except (KeyError, IndexError, TypeError, ValueError, OverflowError):
            errors[sid] = f"{stage}: FRED 응답 필드 또는 날짜/숫자 형식 오류."
            progress(f"실패 / {monotonic() - timer:.1f}초 / {errors[sid]}")
    return {
        "mode": "fred",
        "as_of": datetime.now(UTC).isoformat(),
        "vintage_date": vintage,
        "series": series,
        "errors": errors,
    }
