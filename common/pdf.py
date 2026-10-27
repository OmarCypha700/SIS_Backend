"""
Server-generated PDF documents (registration slips, result slips, transcripts) with ReportLab.

Every document carries the institution header, a generation timestamp, a reference and page
numbers. They are marked as unofficial unless an institution adds its own verification process.
"""
import io

from django.http import HttpResponse
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
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


def _render(story, *, reference, footer_text=""):
    buffer = io.BytesIO()
    generated = timezone.localtime().strftime("%d %b %Y %H:%M")

    def on_page(canvas, doc):
        canvas.saveState()
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
