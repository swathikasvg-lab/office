# reports/url/rpt_1004.py

from .pdf_1004 import PdfUrlReport
from .excel_1004 import ExcelUrlReport


class UrlPerformanceReport:
    """
    Entry point for Report 1004.
    """

    def run(self, urls, start: str, end: str, fmt: str):
        """
        urls: list of selected "server" values (URLs) from the form
        start, end: datetime strings from form
        fmt: "pdf" or "excel"
        """
        if isinstance(urls, str):
            urls = [urls]

        if fmt == "pdf":
            generator = PdfUrlReport()
            return generator.generate(urls, start, end)

        if fmt == "excel":
            generator = ExcelUrlReport()
            return generator.generate(urls, start, end)

        raise ValueError(f"Unsupported format: {fmt}")

