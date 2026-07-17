from report_service.report_generator import (
    ReportGenerator
)

from src.pdf_report import (
    save_report_as_pdf
)

from src.services.job_tracker_service import (
    JobTrackerService
)

from src.services.job_execution_service import (
    JobExecutionService
)

from src.core.metrics import (
    REPORT_GENERATED,
    JOB_DURATION
)


class ReportGenerationJob:

    def __init__(self):

        self.generator = ReportGenerator()

        self.tracker = JobTrackerService()

        self.execution_service = JobExecutionService()

    def run(self):

        started_at = self.execution_service.start()

        with JOB_DURATION.labels(
            job_name="report_job"
        ).time():

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

                self.tracker.mark_success(
                    "report_job"
                )

                self.execution_service.finish_success(

                    job_name="report_job",

                    started_at=started_at,

                    records_processed=1

                )

                print(
                    f"Report Generated: {pdf_path}"
                )

                return pdf_path

            except Exception as e:

                self.tracker.mark_failed(
                    "report_job"
                )

                self.execution_service.finish_failed(

                    job_name="report_job",

                    started_at=started_at,

                    error_message=str(e)

                )

                raise


if __name__ == "__main__":

    ReportGenerationJob().run()