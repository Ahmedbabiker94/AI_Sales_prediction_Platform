from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from datetime import datetime


ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def save_report_as_pdf(report_text: str, filename_prefix: str = "forecast_report") -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_path = REPORTS_DIR / f"{filename_prefix}_{timestamp}.pdf"

    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    width, height = A4

    x_margin = 20 * mm
    y = height - 20 * mm
    line_height = 7 * mm

    c.setFont("Helvetica-Bold", 14)
    c.drawString(x_margin, y, "Sales Forecast Report")
    y -= 12 * mm

    c.setFont("Helvetica", 10)

    for line in report_text.splitlines():
        if y < 20 * mm:
            c.showPage()
            c.setFont("Helvetica", 10)
            y = height - 20 * mm

        safe_line = line[:120]
        c.drawString(x_margin, y, safe_line)
        y -= line_height

    c.save()
    return pdf_path