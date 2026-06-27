import time

from report_service.report_generator import (
    ReportGenerator
)

from src.pdf_report import (
    save_report_as_pdf
)

from src.services.job_tracker_service import (
    JobTrackerService
)

from src.core.metrics import (
    REPORT_GENERATED,
    JOB_DURATION
)


class ReportGenerationJob:

    def __init__(self):

        self.generator = ReportGenerator()

        self.tracker = JobTrackerService()

    def run(self):

        start = time.time()

        try:

            report_text = (

                self.generator
                .generate_text_report()

            )

            pdf_path = (

                save_report_as_pdf(
                    report_text
                )

            )

            REPORT_GENERATED.inc()

            JOB_DURATION.labels(
                "report_job"
            ).observe(

                time.time() - start

            )

            self.tracker.mark_success(

                "report_job"

            )

            print(

                f"Report Generated: {pdf_path}"

            )

            return pdf_path

        except Exception:

            self.tracker.mark_failed(

                "report_job"

            )

            raise


if __name__ == "__main__":

    ReportGenerationJob().run()