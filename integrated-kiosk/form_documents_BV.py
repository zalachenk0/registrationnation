# ============================================================================
# File:         form_documents_BV.py
# Written by:   BV (Blas Vargas)
# What it does: Builds the form details appendix page that can be added to the
#               PDF.
# Team-written: Logic written by BV (Blas Vargas). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  ReportLab
# ============================================================================
"""Validated form appendix shared by the printed and emailed company PDF."""
import io
import json
from xml.sax.saxutils import escape

GROUPS = {
    'personal': ('Personal information', {'firstName': 'First name', 'lastName': 'Last name', 'dateOfBirth': 'Date of birth', 'email': 'Email', 'phone': 'Phone', 'address': 'Street address', 'city': 'City', 'state': 'State', 'zip': 'ZIP code'}),
    'license': ("Driver's license and vehicle", {'licenseNumber': 'License number', 'licenseState': 'Issuing state', 'expirationDate': 'License expiration', 'licenseClass': 'License class', 'vehicleYear': 'Vehicle year', 'vehicleMake': 'Make', 'vehicleModel': 'Model', 'vin': 'VIN', 'licensePlate': 'License plate'}),
    'insurance': ('Insurance information', {'provider': 'Provider', 'policyNumber': 'Policy number', 'policyHolder': 'Policy holder', 'startDate': 'Start date', 'endDate': 'End date', 'coverageType': 'Coverage type', 'agentName': 'Agent name', 'agentPhone': 'Agent phone'}),
}


def parse_details(raw):
    if raw is None:
        return {}
    if isinstance(raw, str):
        if len(raw) > 16000:
            raise ValueError('Form details are too large.')
        try:
            raw = json.loads(raw)
        except ValueError:
            raise ValueError('Form details are invalid.') from None
    if not isinstance(raw, dict):
        raise ValueError('Form details must be an object.')
    result = {}
    for group, (_, fields) in GROUPS.items():
        values = raw.get(group, {})
        if not isinstance(values, dict):
            raise ValueError('Invalid form section.')
        result[group] = {}
        for key in fields:
            value = values.get(key, '')
            if not isinstance(value, str) or len(value) > 200:
                raise ValueError('Form values must contain at most 200 characters.')
            result[group][key] = ' '.join(value.split())
    return result


def appendix(data):
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
    stream = io.BytesIO()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle('Company', fontName='Helvetica-Bold', fontSize=17, leading=21, textColor=colors.HexColor('#1a3a5c'), spaceAfter=8))
    styles.add(ParagraphStyle('Section', fontName='Helvetica-Bold', fontSize=10, leading=14, textColor=colors.HexColor('#1a3a5c'), spaceBefore=14, spaceAfter=7))
    styles.add(ParagraphStyle('Value', fontName='Helvetica', fontSize=8, leading=11, wordWrap='CJK'))
    story = [Paragraph('Registration Nation', styles['Company']), Paragraph('Texas Tag &amp; Title | Registration details', styles['Normal']), Spacer(1, 6), Paragraph('Reference: ' + escape(data['confirmation_number']), styles['Value'])]
    for group, (title, fields) in GROUPS.items():
        rows = [[Paragraph(escape(label), styles['Value']), Paragraph(escape(data['form_details'].get(group, {}).get(key, '') or '-'), styles['Value'])] for key, label in fields.items()]
        table = Table(rows, colWidths=[130, 378], hAlign='LEFT')
        table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 9), ('RIGHTPADDING', (0, 0), (-1, -1), 9), ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4), ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.HexColor('#f0f3f7'), colors.white])]))
        story.extend([Paragraph(title, styles['Section']), table])
    def footer(canvas, document):
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#6b7a8d'))
        canvas.drawString(52, 28, 'Customer information supplied through the kiosk. Demonstration copy.')
        canvas.drawRightString(560, 28, f'Form details - page {document.page}')
    SimpleDocTemplate(stream, pagesize=(612, 792), leftMargin=52, rightMargin=52, topMargin=42, bottomMargin=45).build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()
