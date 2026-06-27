from report_service.report_generator import (
    ReportGenerator
)

from src.pdf_report import (
    save_report_as_pdf
)


class ReportService:

    def __init__(self):

        self.generator = (
            ReportGenerator()
        )

    def generate_pdf_report(self):

        report_text = (
            self.generator
            .generate_text_report()
        )

        pdf_path = (
            save_report_as_pdf(
                report_text
            )
        )

        return pdf_path