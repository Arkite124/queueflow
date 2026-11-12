"""Read-only exports from committed artifacts; never use client filenames as paths."""
import json
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill


def excel_report(summary):
    book = Workbook()
    sheet = book.active
    sheet.title = "처리 요약"
    sheet.append(["QueueFlow 처리 결과", "값"])
    for label, key in (("처리한 행", "processed_rows"), ("결과 CSV 행", "written_rows"),
                       ("오류가 있는 행", "error_rows")):
        sheet.append([label, summary[key]])
    sheet.append(["안내", "오류가 있는 행은 적용 규칙에 따라 결과에 포함될 수 있습니다."])
    sheet.append(["파일 안내", "cleaned.csv: 정제 결과 / errors.csv: 행별 오류 / summary.json: 상세 요약"])
    sheet.freeze_panes = "A2"
    sheet.column_dimensions["A"].width = 26
    sheet.column_dimensions["B"].width = 85
    sheet.auto_filter.ref = "A1:B4"
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor="214C3E")
        cell.font = Font(color="FFFFFF", bold=True, size=13)
    for row in sheet.iter_rows(min_row=2):
        sheet.row_dimensions[row[0].row].height = 32
        for cell in row:
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        if row[0].row <= 4:
            row[1].number_format = "#,##0"
    output = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
    try:
        book.save(output)
        output.seek(0)
        return output
    except BaseException:
        output.close()
        raise
    finally:
        book.close()


def result_export(paths, bundle):
    with paths["summary"].open(encoding="utf-8") as source:
        summary = json.load(source)
    report = excel_report(summary)
    if not bundle:
        return report
    output = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
    try:
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            for kind, name in (("cleaned", "cleaned.csv"), ("errors", "errors.csv"), ("summary", "summary.json")):
                archive.write(paths[kind], arcname=name)
            with archive.open("report.xlsx", "w") as target:
                while chunk := report.read(65536):
                    target.write(chunk)
        output.seek(0)
        return output
    except BaseException:
        output.close()
        raise
    finally:
        report.close()


def chunks(output):
    try:
        while chunk := output.read(65536):
            yield chunk
    finally:
        output.close()
