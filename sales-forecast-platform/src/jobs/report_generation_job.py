"""Legacy job orchestration; operational collaborators require host composition."""
from src.runtime.bootstrap import Runtime, require_runtime


class ReportGenerationJob:
    def __init__(self, *, runtime=None):
        self._runtime = require_runtime(runtime) if runtime is not None else Runtime()

    def run(self):
        generator = self._runtime.get('report_generator')
        save_report_as_pdf = self._runtime.get('pdf_writer')
        tracker = self._runtime.get('job_tracker')
        execution_service = self._runtime.get('job_execution')
        metrics = self._runtime.get('job_metrics')
        REPORT_GENERATED = metrics.REPORT_GENERATED
        JOB_DURATION = metrics.JOB_DURATION

        started_at = execution_service.start()

        with JOB_DURATION.labels(
            job_name="report_job"
        ).time():

            try:

                report_text = (
                    generator
                    .generate_text_report()
                )

                pdf_path = (
                    save_report_as_pdf(
                        report_text
                    )
                )

                REPORT_GENERATED.inc()

                tracker.mark_success(
                    "report_job"
                )

                execution_service.finish_success(

                    job_name="report_job",

                    started_at=started_at,

                    records_processed=1

                )

                print(
                    f"Report Generated: {pdf_path}"
                )

                return pdf_path

            except Exception as e:

                tracker.mark_failed(
                    "report_job"
                )

                execution_service.finish_failed(

                    job_name="report_job",

                    started_at=started_at,

                    error_message=str(e)

                )

                raise


if __name__ == "__main__":

    ReportGenerationJob().run()