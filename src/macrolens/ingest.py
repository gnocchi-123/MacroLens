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
from urllib.parse import quote, quote_plus, urlencode
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


def sample_snapshot(as_of=None):
    """지정 시점의 합성 자료; 기본 시점은 고정. 실제 관측·발표 일정이 아니다."""
    sample_as_of = as_of or SAMPLE_AS_OF
    series = {}
    for sid, spec in INDICATORS.items():
        rows = []
        if spec["frequency"] == "Monthly":
            for i in range(max(0, (sample_as_of.year - 2018) * 12 + sample_as_of.month - 6)):
                day = month_shift(date(2018, 7, 1), i)
                available = datetime.combine(day + timedelta(days=45), datetime.min.time(), UTC)
                if available > sample_as_of:
                    continue
                value = (
                    4 + 0.6 * math.sin(i / 7)
                    if sid == "UNRATE"
                    else 100 + i * 0.3 + 2 * math.sin(i / 5)
                )
                rows.append(observation(day, value, available, sample_as_of, "synthetic-v1"))
        else:
            for i in range((sample_as_of.date() - date(2019, 1, 1)).days):
                day = date(2019, 1, 1) + timedelta(days=i)
                available = datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC)
                if day.weekday() >= 5 or available > sample_as_of:
                    continue
                value = 3 + 0.5 * math.sin(i / 65) + 0.2 * math.cos(i / 17)
                rows.append(observation(day, value, available, sample_as_of, "synthetic-v1"))
        series[sid] = {
            "metadata": {k: spec[k] for k in ("units", "frequency", "seasonal_adjustment")},
            "source": "synthetic://macrolens/sample-v1",
            "availability_policy": "가상 이용 가능 시각 (샘플 전용)",
            "observations": rows,
        }
    return {"mode": "sample", "as_of": sample_as_of.isoformat(), "series": series, "errors": {}}


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
    return redact(result, api_key)


def parse_rows(payload, retrieved_at, vintage, available_at=None, historical=False):
    if historical:
        validate_period(payload, vintage)
    rows = payload["observations"]
    if not isinstance(rows, list) or int(payload["count"]) != len(rows):
        raise DataError("FRED 관측 응답 누락 또는 페이지 초과.")
    parsed = []
    for row in rows:
        if historical or row.get("realtime_start") or row.get("realtime_end"):
            validate_period(row, vintage)
        if date.fromisoformat(row["date"]) > date.fromisoformat(vintage):
            raise DataError("요청 vintage 이후의 미래 관측")
        value = None if row["value"] == "." else float(row["value"])
        if value is not None and not math.isfinite(value):
            raise DataError("비유한 FRED 관측값.")
        item = observation(
            date.fromisoformat(row["date"]),
            value,
            available_at or retrieved_at,
            retrieved_at,
            vintage,
        )
        # 요청한 실시간 기간은 실제 최초 발표 시각과 같지 않다.
        item["realtime_start"] = row.get("realtime_start")
        item["realtime_end"] = row.get("realtime_end")
        parsed.append(item)
    return parsed


def redact(value, api_key):
    """Defense against keys echoed by upstream metadata; URLs are never logged."""
    if isinstance(value, dict):
        return {
            redact(k, api_key): ("[REDACTED]" if k.lower() == "api_key" else redact(v, api_key))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v, api_key) for v in value]
    if isinstance(value, str) and api_key:
        for representation in {api_key, quote(api_key, safe=""), quote_plus(api_key)}:
            value = value.replace(representation, "[REDACTED]")
        return value
    return value


def validate_period(payload, vintage):
    try:
        start = date.fromisoformat(payload["realtime_start"])
        end = date.fromisoformat(payload["realtime_end"])
        if not start <= date.fromisoformat(vintage) <= end:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise DataError("FRED 실시간 기간이 요청 vintage를 포함하지 않음/누락") from None


def fred_snapshot(api_key, as_of=None, raw_sink=None):
    if not api_key or not api_key.strip():
        raise DataError("FRED_API_KEY 환경변수를 설정하세요. 키를 대화에 붙여 넣지 마세요.")
    api_key = api_key.strip()
    if any(character.isspace() for character in api_key):
        raise DataError("API 키 안에 공백/줄바꿈이 있습니다. 키만 다시 입력하세요.")
    started = datetime.now(UTC)
    if as_of is not None and (as_of.tzinfo is None or as_of > started):
        raise DataError("과거 조회에는 시간대가 있는 현재 이전 판단 시각이 필요합니다.")
    historical = as_of is not None
    cutoff = as_of or started
    # Date-only vintage: use the previous completed Chicago calendar day.
    vintage_day = cutoff.astimezone(ZoneInfo("America/Chicago")).date() - timedelta(days=1)
    vintage = vintage_day.isoformat()
    gate = datetime.combine(
        vintage_day + timedelta(days=1), datetime.min.time(), ZoneInfo("America/Chicago")
    )
    start = (vintage_day - timedelta(days=365 * 10)).isoformat()
    series, errors, raw = {}, {}, {}
    for index, sid in enumerate(INDICATORS, 1):
        stage = "메타데이터"
        timer = monotonic()

        def progress(message):
            print(f"[{index}/{len(INDICATORS)}] {sid} {message}", file=sys.stderr, flush=True)

        def fetch(endpoint, params, name):
            payload = redact(request_json(endpoint, params, api_key), api_key)
            envelope = {
                "endpoint": endpoint,
                "params": {**params, "file_type": "json"},
                "retrieved_at": datetime.now(UTC).isoformat(),
                "response": payload,
            }
            raw.setdefault(sid, {})[name] = envelope
            if raw_sink is not None:
                raw_sink(sid, name, envelope)
            return payload

        progress("메타데이터 요청 중 (연결/읽기 타임아웃 30초)")
        params = {"series_id": sid, "realtime_start": vintage, "realtime_end": vintage}
        try:
            metadata_payload = fetch("series", params, "metadata")
            metadata = metadata_payload["seriess"][0]
            if metadata["id"] != sid:
                raise DataError("FRED 지표 ID 불일치.")
            if historical:
                validate_period(metadata_payload, vintage)
                validate_period(metadata, vintage)
            stage = "관측값"
            progress("관측값 요청 중 (최근 약 10년)")
            payload = fetch(
                "series/observations",
                {
                    **params,
                    "observation_start": start,
                    "observation_end": vintage,
                    "sort_order": "asc",
                    "limit": 100000,
                    "units": "lin",
                    "output_type": 1,
                },
                "observations",
            )
            retrieved = datetime.fromisoformat(raw[sid]["observations"]["retrieved_at"])
            series[sid] = {
                "source": f"https://fred.stlouisfed.org/series/{sid}",
                "metadata": metadata,
                "availability_policy": (
                    (
                        "과거 vintage: 미국 중부 다음날 00:00부터 사용; "
                        "보수적 날짜 규칙; 발표시각 미확인"
                    )
                    if historical
                    else "수집 시각부터 사용; 전일 vintage; 실제 발표시각 미확인"
                ),
                "observations": parse_rows(
                    payload, retrieved, vintage, gate if historical else None, historical
                ),
                "raw_response": payload,
            }
            progress(
                f"수집 완료: {len(series[sid]['observations'])}개 / {monotonic() - timer:.1f}초"
            )
        except DataError as exc:
            errors[sid] = f"{stage}: {redact(str(exc), api_key)}"
            progress(f"실패 / {monotonic() - timer:.1f}초 / {errors[sid]}")
        except (KeyError, IndexError, TypeError, ValueError, OverflowError):
            errors[sid] = f"{stage}: FRED 응답 필드 또는 날짜/숫자 형식 오류."
            progress(f"실패 / {monotonic() - timer:.1f}초 / {errors[sid]}")
    return {
        "mode": "fred",
        "as_of": (as_of or datetime.now(UTC)).isoformat(),
        "acquisition": "historical_vintage" if historical else "current_collection",
        "time_policy": "chicago-prior-day-v1" if historical else "collected-at-v1",
        "vintage_date": vintage,
        "retrieval_started_at": started.isoformat(),
        "retrieval_finished_at": datetime.now(UTC).isoformat(),
        "series": series,
        "errors": errors,
        "raw": raw,
    }
