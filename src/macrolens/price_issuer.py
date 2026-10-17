"""Small offline reader for the issuer's public distribution workbook, not prices."""

import io
import math
import xml.etree.ElementTree as ET
from datetime import datetime
from zipfile import BadZipFile, ZipFile

from .price_quality import AuditError

DISTRIBUTIONS_URL = (
    "https://www.ssga.com/library-content/products/fund-data/etfs/us/"
    "spdr-etf-historical-distributions.xlsx"
)
HEADERS = (
    "FUND NAME",
    "TICKER",
    "CUSIP",
    "EX-DATE",
    "RECORD DATE",
    "PAYABLE DATE",
    "DIVIDEND ($)",
    "SHORT TERM CAPITAL GAIN ($)",
    "LONG TERM CAPITAL GAIN ($)",
    "FREQUENCY",
)
NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def parse_distributions(blob, tickers, start, end):
    """Only accept the reviewed workbook format. Blank capital gains stay unknown."""
    try:
        with ZipFile(io.BytesIO(blob)) as archive:
            if sum(x.file_size for x in archive.infolist()) > 50_000_000:
                raise AuditError("공식 배당 파일 압축 해제 크기 한도 초과")
            book = ET.fromstring(archive.read("xl/workbook.xml"))
            props = book.find("x:workbookPr", NS)
            if props is not None and props.get("date1904") in ("1", "true"):
                raise AuditError("검토하지 않은 Excel 날짜 기준")
            strings = []
            if "xl/sharedStrings.xml" in archive.namelist():
                strings = [
                    "".join(x.itertext())
                    for x in ET.fromstring(archive.read("xl/sharedStrings.xml"))
                ]
            tree = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            table = []
            for row in tree.findall(".//x:sheetData/x:row", NS):
                cells = {}
                for cell in row.findall("x:c", NS):
                    value = cell.find("x:v", NS)
                    value = value.text if value is not None else ""
                    if cell.get("t") == "s":
                        value = strings[int(value)]
                    elif cell.get("t") == "inlineStr":
                        value = "".join(cell.find("x:is", NS).itertext())
                    cells["".join(c for c in cell.get("r") if c.isalpha())] = value
                table.append([cells.get(chr(65 + i), "").strip() for i in range(10)])
        if not table or table[0] != list(HEADERS):
            raise AuditError("공식 배당 파일 열 정의 변경; 추정해서 읽지 않음")
        result = {}
        for ticker in tickers:
            events, all_dates, issues = [], [], []
            for row in table[1:]:
                if row[1] != ticker:
                    continue
                day = datetime.strptime(row[3], "%m/%d/%Y").date().isoformat()
                all_dates.append(day)
                if not start <= day <= end:
                    continue
                amounts = [float(value) if value else None for value in row[6:9]]
                if amounts[0] is None or any(
                    v is not None and (not math.isfinite(v) or v < 0) for v in amounts
                ):
                    issues.append("결측/비정상 분배금: " + day)
                    continue
                events.append(
                    {
                        "ex_date": day,
                        "record_date": datetime.strptime(row[4], "%m/%d/%Y").date().isoformat(),
                        "payable_date": datetime.strptime(row[5], "%m/%d/%Y").date().isoformat(),
                        "dividend": amounts[0],
                        "short_term_gain": amounts[1],
                        "long_term_gain": amounts[2],
                        "cusip": row[2],
                    }
                )
            dates = [e["ex_date"] for e in events]
            if not dates:
                issues.append("요청 범위 배당 자료 없음; 무배당으로 해석하지 않음")
            if len(set(dates)) != len(dates):
                issues.append("중복 배당락일; 합산/제거하지 않음")
            result[ticker] = {
                "status": "held",
                "rows": 0,
                "issues": issues + ["일별 가격·공급자 사건과의 대조 미완료"],
                "distribution_rows": len(events),
                "distribution_file_range": [min(all_dates), max(all_dates)] if all_dates else None,
                "requested_distribution_range": [min(dates), max(dates)] if dates else None,
                "events_in_source_order": events,
                "blank_capital_gains": sum(
                    e[k] is None for e in events for k in ("short_term_gain", "long_term_gain")
                ),
                "nonzero_capital_gains": sum(
                    (e[k] or 0) > 0 for e in events for k in ("short_term_gain", "long_term_gain")
                ),
                "share_basis": "issuer_reported; split-restatement basis requires cross-check",
            }
        return result
    except (BadZipFile, ET.ParseError, KeyError, TypeError, ValueError, IndexError) as exc:
        if isinstance(exc, AuditError):
            raise
        raise AuditError("공식 배당 XLSX 형식 오류; 가격 또는 0원 배당으로 대체하지 않음") from None
