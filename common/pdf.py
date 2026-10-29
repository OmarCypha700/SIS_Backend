"""
Server-generated PDF documents (registration slips, result slips, transcripts) with ReportLab.

Every document carries the institution header, a generation timestamp, a reference and page
numbers. Slips and downloaded records are marked unofficial. Official transcripts are issued
through apps.transcripts: they carry a serial number, a verification QR code and a signature block.
"""
import io

from django.http import HttpResponse
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.institutions.models import InstitutionProfile

_styles = getSampleStyleSheet()
STYLE_TITLE = ParagraphStyle("SisTitle", parent=_styles["Title"], fontSize=14, spaceAfter=2)
STYLE_SUB = ParagraphStyle("SisSub", parent=_styles["Normal"], alignment=TA_CENTER, fontSize=9, textColor=colors.grey)
STYLE_H2 = ParagraphStyle("SisH2", parent=_styles["Heading3"], fontSize=10.5, spaceBefore=8, spaceAfter=4)
STYLE_BODY = ParagraphStyle("SisBody", parent=_styles["Normal"], fontSize=9, leading=12)
STYLE_SMALL = ParagraphStyle("SisSmall", parent=_styles["Normal"], fontSize=7.5, textColor=colors.grey)
GRID = colors.HexColor("#d4d4d8")
HEAD = colors.HexColor("#f4f4f5")


def _escape(value):
    return ("" if value is None else str(value)).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _header(profile, title):
    parts = []
    if profile.logo:
        try:
            logo = Image(profile.logo.path, width=18 * mm, height=18 * mm, kind="proportional")
            logo.hAlign = "CENTER"
            parts.append(logo)
        except (OSError, ValueError):
            pass  # a missing logo file must not break the document
    parts.append(Paragraph(_escape(profile.name), STYLE_TITLE))
    contact = " · ".join(p for p in [profile.physical_address.replace("\n", ", "), profile.phone, profile.email] if p)
    if contact:
        parts.append(Paragraph(_escape(contact), STYLE_SUB))
    parts.append(Spacer(1, 3 * mm))
    parts.append(Paragraph(f"<b>{_escape(title).upper()}</b>", ParagraphStyle("t", parent=STYLE_SUB, fontSize=10,
                                                                               textColor=colors.black)))
    parts.append(Spacer(1, 4 * mm))
    return parts


def _details(pairs):
    """Two-column label/value table for student details."""
    cells = [[Paragraph(f"<font color='grey'>{_escape(k)}</font>", STYLE_BODY), Paragraph(_escape(v), STYLE_BODY)]
             for k, v in pairs]
    half = (len(cells) + 1) // 2
    left, right = cells[:half], cells[half:]
    rows = [l + (right[i] if i < len(right) else ["", ""]) for i, l in enumerate(left)]
    table = Table(rows, colWidths=[30 * mm, 55 * mm, 30 * mm, 55 * mm])
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    return table


def _data_table(header, rows, widths, align_right=(), footer=None):
    data = [[Paragraph(f"<b>{_escape(h)}</b>", STYLE_BODY) for h in header]]
    data += [[Paragraph(_escape(c), STYLE_BODY) for c in row] for row in rows]
    if footer:
        data.append([Paragraph(f"<b>{_escape(c)}</b>", STYLE_BODY) for c in footer])
    table = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("BACKGROUND", (0, 0), (-1, 0), HEAD),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if footer:
        style.append(("BACKGROUND", (0, -1), (-1, -1), HEAD))
    for column in align_right:
        style.append(("ALIGN", (column, 0), (column, -1), "RIGHT"))
    table.setStyle(TableStyle(style))
    return table


def _render(story, *, reference, footer_text="", watermark=""):
    buffer = io.BytesIO()
    generated = timezone.localtime().strftime("%d %b %Y %H:%M")

    def on_page(canvas, doc):
        canvas.saveState()
        if watermark:
            canvas.setFont("Helvetica-Bold", 64)
            canvas.setFillColor(colors.Color(0.85, 0.85, 0.85, alpha=0.35))
            canvas.translate(A4[0] / 2, A4[1] / 2)
            canvas.rotate(40)
            canvas.drawCentredString(0, 0, watermark)
            canvas.rotate(-40)
            canvas.translate(-A4[0] / 2, -A4[1] / 2)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.grey)
        text = f"Generated {generated} · Reference {reference}"
        if footer_text:
            text = f"{footer_text[:120]} · {text}"
        canvas.drawString(15 * mm, 10 * mm, text)
        canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=12 * mm,
                            bottomMargin=18 * mm, title=reference, author=InstitutionProfile.load().name)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buffer.getvalue()


def pdf_response(content, filename):
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# --- Documents ---------------------------------------------------------------

def registration_slip(registration):
    profile = InstitutionProfile.load()
    student = registration.student
    courses = [c for c in registration.courses.all() if c.status == "ACTIVE"]
    story = _header(profile, "Course registration slip")
    story.append(_details([
        ("Name", student.full_name), ("Student number", student.student_number),
        ("Programme", f"{registration.programme.name} ({registration.programme.code})"), ("Level", registration.level.name),
        ("Semester", str(registration.semester)), ("Status", registration.get_status_display()),
        ("Submitted", timezone.localtime(registration.submitted_at).strftime("%d %b %Y %H:%M") if registration.submitted_at else "—"),
    ]))
    story.append(Spacer(1, 5 * mm))
    story.append(_data_table(
        ["Code", "Course", "Category", "Credits"],
        [[c.offering.course.code, c.offering.course.title + (" (retake)" if c.is_retake else ""),
          c.category.title(), c.offering.course.credits] for c in courses],
        [25 * mm, 95 * mm, 30 * mm, 20 * mm], align_right=(3,),
        footer=["", "Total credits", "", registration.total_credits],
    ))
    story.append(Spacer(1, 12 * mm))
    story.append(_details([("Student's signature", "...................................."),
                           ("Registry", "....................................")]))
    return _render(story, reference=f"REG-{registration.pk}", footer_text="Unofficial copy")


def _semester_table(semester):
    rows = []
    for c in semester["courses"]:
        notes = [n for n, flag in (("retake", c["is_retake"]), ("resit", c.get("is_resit")),
                                   ("capped", c.get("grade_capped")), ("amended", c["is_amended"]),
                                   ("unpublished", c.get("is_published") is False)) if flag]
        rows.append([c["course_code"], c["course_title"] + (f" ({', '.join(notes)})" if notes else ""),
                     c["credits"], c["total"], c["grade"], c["grade_point"]])
    return _data_table(
        ["Code", "Course", "Credits", "Score", "Grade", "Points"], rows,
        [22 * mm, 88 * mm, 16 * mm, 16 * mm, 14 * mm, 14 * mm], align_right=(2, 3, 5),
        footer=["", f"Credits attempted {semester['credits_attempted']} · earned {semester['credits_earned']}", "",
                "", "GPA", semester["gpa"] or "—"],
    )


def _student_pairs(record):
    s = record["student"]
    return [("Name", s["full_name"]), ("Student number", s["student_number"]),
            ("Programme", f"{s['programme']} ({s['programme_code']})"), ("Level", s["level"]),
            ("Admission year", s["admission_year"]), ("CGPA", record["summary"]["cgpa"] or "—")]


def result_slip(record, semester):
    profile = InstitutionProfile.load()
    story = _header(profile, "Semester result slip")
    story.append(_details(_student_pairs(record)))
    story.append(Paragraph(f"{_escape(semester['academic_year'])} · {_escape(semester['semester'])}", STYLE_H2))
    story.append(_semester_table(semester))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(f"Cumulative GPA after this semester: <b>{_escape(semester['cgpa'] or '—')}</b>", STYLE_BODY))
    if profile.result_slip_footer:
        story += [Spacer(1, 4 * mm), Paragraph(_escape(profile.result_slip_footer), STYLE_SMALL)]
    return _render(story, reference=f"RS-{record['student']['student_number']}-{semester['semester_id']}",
                   footer_text="Unofficial copy")


def transcript(record, *, title="Academic transcript", include_unpublished=False):
    profile = InstitutionProfile.load()
    story = _header(profile, title)
    story.append(_details(_student_pairs(record)))
    if not record["semesters"]:
        story.append(Paragraph("No results recorded.", STYLE_BODY))
    for semester in record["semesters"]:
        story.append(KeepTogether([
            Paragraph(f"{_escape(semester['academic_year'])} · {_escape(semester['semester'])} "
                      f"<font color='grey'>(CGPA {_escape(semester['cgpa'] or '—')})</font>", STYLE_H2),
            _semester_table(semester),
        ]))
    summary = record["summary"]
    story += [Spacer(1, 4 * mm), Paragraph(
        f"Credits attempted {summary['credits_attempted']} · credits earned {summary['credits_earned']}"
        + (f" of {summary['required_credits']} required" if summary.get("required_credits") else "")
        + f" · cumulative GPA <b>{_escape(summary['cgpa'] or '—')}</b>", STYLE_BODY)]
    if include_unpublished:
        story.append(Paragraph("Includes results that are not yet published. For internal use only.", STYLE_SMALL))
    return _render(story, reference=f"TR-{record['student']['student_number']}",
                   footer_text="Unofficial copy" + (" – internal" if include_unpublished else ""))


# --- Official transcripts ------------------------------------------------------

def _qr(url, size=26 * mm):
    widget = QrCodeWidget(url, barBorder=0)
    x1, y1, x2, y2 = widget.getBounds()
    drawing = Drawing(size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0])
    drawing.add(widget)
    return drawing


def _private_image(field, width, height):
    if not field:
        return None
    try:
        return Image(field.path, width=width, height=height, kind="proportional")
    except (OSError, ValueError, NotImplementedError):
        return None


def _attestation(profile, transcript):
    """Signature and seal: the uploaded images, or blank spaces to sign and stamp by hand."""
    digital = transcript.signing == "DIGITAL"
    signature = _private_image(profile.transcript_signature, 45 * mm, 16 * mm) if digital else None
    seal = _private_image(profile.transcript_seal, 30 * mm, 30 * mm) if digital else None
    name = profile.transcript_signatory_name or ("" if digital else "." * 40)
    left = [
        signature or Spacer(1, 16 * mm),
        Paragraph("_" * 42, STYLE_BODY),
        Paragraph("Signature", STYLE_SMALL),
        Paragraph(f"Name: {_escape(name)}", STYLE_BODY),
        Paragraph(f"Title: {_escape(profile.transcript_signatory_title)}", STYLE_BODY),
        Paragraph("Date: " + (timezone.localtime(transcript.issued_at).strftime("%d %B %Y") if digital
                              else "." * 40), STYLE_BODY),
    ]
    stamp = seal or Table([[Paragraph("Official stamp / seal", ParagraphStyle(
        "stamp", parent=STYLE_SMALL, alignment=TA_CENTER))]], colWidths=[40 * mm], rowHeights=[30 * mm],
        style=TableStyle([("BOX", (0, 0), (-1, -1), 0.6, colors.grey), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                          ("LINESTYLE", (0, 0), (-1, -1), 0.6)]))
    table = Table([[left, stamp]], colWidths=[110 * mm, 70 * mm])
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("ALIGN", (1, 0), (1, 0), "CENTER")]))
    return table


def official_transcript(transcript, *, verify_url, copy_number=1):
    profile = InstitutionProfile.load()
    record = transcript.snapshot
    title = "Official academic transcript" + (" (interim)" if transcript.is_interim else "")
    story = _header(profile, title)
    issued = timezone.localtime(transcript.issued_at).strftime("%d %B %Y")
    story.append(_details(_student_pairs(record) + [("Serial number", transcript.serial), ("Date of issue", issued)]))
    if transcript.is_interim:
        story += [Spacer(1, 2 * mm), Paragraph(
            f"<b>Interim transcript.</b> {_escape(transcript.interim_reason)}. It lists published results up to the "
            "date of issue.", STYLE_BODY)]
    for semester in record["semesters"]:
        story.append(KeepTogether([
            Paragraph(f"{_escape(semester['academic_year'])} · {_escape(semester['semester'])} "
                      f"<font color='grey'>(CGPA {_escape(semester['cgpa'] or '—')})</font>", STYLE_H2),
            _semester_table(semester),
        ]))
    summary = record["summary"]
    story += [Spacer(1, 4 * mm), Paragraph(
        f"Credits attempted {summary['credits_attempted']} · credits earned {summary['credits_earned']}"
        + (f" of {summary['required_credits']} required" if summary.get("required_credits") else "")
        + f" · cumulative GPA <b>{_escape(summary['cgpa'] or '—')}</b>", STYLE_BODY)]

    verify_text = Paragraph(
        "<b>Verify this transcript</b><br/>Scan the code or visit<br/>"
        f"<font size=8>{_escape(verify_url.rsplit('/', 1)[0])}</font><br/>"
        f"and enter <b>{_escape(transcript.display_code)}</b>.<br/>"
        "<font color='grey' size=7>The verification page shows whether this transcript is valid or has been "
        "revoked. Any alteration makes this document invalid.</font>", STYLE_BODY)
    closing = [Spacer(1, 8 * mm), _attestation(profile, transcript), Spacer(1, 6 * mm),
               Table([[_qr(verify_url), verify_text]], colWidths=[32 * mm, 148 * mm],
                     style=TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))]
    if profile.transcript_footer:
        closing += [Spacer(1, 3 * mm), Paragraph(_escape(profile.transcript_footer), STYLE_SMALL)]
    story.append(KeepTogether(closing))
    footer = "Official transcript" + (f" · Reprint, copy {copy_number}" if copy_number > 1 else "")
    return _render(story, reference=transcript.serial, footer_text=footer,
                   watermark="INTERIM" if transcript.is_interim else "")
