# ============================================================================
# File:         receipt_documents_BV.py
# Written by:   BV (Blas Vargas)
# What it does: Draws the two page print-out: the mock sticker and the Texas Tag
#               & Title receipt.
# Team-written: Logic written by BV (Blas Vargas). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  pypdf; ReportLab; assets/ttt-receipt.pdf is Texas Tag & Title's
#               own receipt form, provided by the sponsor.
# ============================================================================
"""Two-page registration sample and populated original TTT receipt."""
from __future__ import annotations

import io
import os
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

TEMPLATE = Path(__file__).resolve().parent / 'assets' / 'ttt-receipt.pdf'


def _box(pdf, value, x, baseline, width, height=24, size=10, bold=False):
    """Wrap long values within their reserved area; never silently cut text off."""
    value = escape(' '.join(str(value or '').split()))
    for font_size in (size, 9, 8, 7):
        if font_size > size:
            continue
        style = ParagraphStyle('value', fontName='Helvetica-Bold' if bold else 'Helvetica',
                               fontSize=font_size, leading=font_size + 2,
                               textColor=black, splitLongWords=True)
        paragraph = Paragraph(value, style)
        _, used_height = paragraph.wrap(width, height)
        if used_height <= height:
            paragraph.drawOn(pdf, x, baseline + font_size - used_height + 2)
            return
    raise ValueError('A receipt value is too long to fit. Shorten the field before printing.')


def build_documents(data, payment=None, *, today=None):
    # The public printing wrapper validates data; keep this internal API reusable for previews/tests.
    today = today or datetime.now(ZoneInfo(os.getenv('KIOSK_TIMEZONE', 'America/Chicago'))).date()
    # Customer printing is limited to the mock sticker and company receipt.
    # Form details remain available for receipt fields, but are not appended.
    total_pages = 2
    month, year = today.month, today.year + 1
    if payment is not None:
        if (not isinstance(payment, dict) or type(payment.get('amount')) is not int
                or payment['amount'] < 0 or not payment.get('id')):
            raise ValueError('Verified payment details are missing or invalid.')
    amount = payment['amount'] if payment else 0
    test_payment = payment is not None and payment.get('testMode', True)

    sticker_buffer = io.BytesIO()
    pdf = canvas.Canvas(sticker_buffer, pagesize=letter)
    navy, blue = HexColor('#193858'), HexColor('#1979b8')
    pdf.setFillColor(navy)
    pdf.setFont('Helvetica-Bold', 21)
    pdf.drawString(48, 742, 'TEXAS REGISTRATION RENEWAL')
    pdf.setFont('Helvetica', 11)
    pdf.drawString(48, 721, 'Registration Nation  |  Customer copy')
    pdf.setStrokeColor(HexColor('#f17100'))
    pdf.setLineWidth(2)
    pdf.line(48, 706, 564, 706)
    pdf.setFillColor(black)
    pdf.setFont('Helvetica-Bold', 10)
    pdf.drawString(48, 675, 'Customer')
    _box(pdf, data['registrant'], 152, 675, 405, 30)
    pdf.drawString(48, 636, 'Vehicle')
    _box(pdf, ' '.join(data[k] for k in ('vehicle_year', 'vehicle_make', 'vehicle_model')),
         152, 636, 405, 52)
    pdf.setFont('Helvetica-Bold', 10)
    pdf.drawString(48, 575, 'License plate')
    _box(pdf, data['license_plate'].upper(), 152, 575, 405, 30)
    pdf.drawString(48, 536, 'VIN')
    _box(pdf, data['vin'].upper(), 152, 536, 405)
    pdf.drawString(48, 506, 'Reference')
    _box(pdf, data['confirmation_number'], 152, 506, 405)

    # A windshield-sticker inspired sample; the kiosk has no state issuance integration.
    x, y, w, h = 162, 224, 288, 222
    pdf.setFillColor(blue)
    pdf.roundRect(x, y, w, h, 8, stroke=0, fill=1)
    pdf.setFillColor(white)
    pdf.rect(x + 10, y + 54, w - 20, h - 88, stroke=0, fill=1)
    pdf.setFont('Helvetica-Bold', 17)
    pdf.drawCentredString(306, y + h - 24, 'TEXAS')
    pdf.setFillColor(black)
    pdf.setFont('Helvetica-Bold', 69)
    pdf.drawCentredString(306, y + 117, f'{month:02d}/{year % 100:02d}')
    pdf.setFont('Helvetica-Bold', 11)
    pdf.drawCentredString(306, y + 94, f'EXPIRATION: {month:02d}/{year}')
    _box(pdf, data['license_plate'].upper(), x + 22, y + 73, w - 44, 34, 10, True)
    pdf.setFillColor(white)
    pdf.setFont('Helvetica-Bold', 12)
    pdf.drawCentredString(306, y + 34, 'SAMPLE - NOT VALID')
    pdf.setFont('Helvetica', 9)
    pdf.drawCentredString(306, y + 17, 'No vehicle registration has been issued')

    pdf.setFillColor(black)
    pdf.setFont('Helvetica', 10)
    pdf.drawCentredString(306, 191, 'Current renewal month / following calendar year')
    pdf.drawCentredString(306, 174, f'Prepared {today:%m/%d/%Y}  |  Receipt follows on page 2')
    pdf.setFont('Helvetica', 9)
    pdf.drawString(48, 70, 'Demonstration copy. This page is not a state-issued registration credential.')
    pdf.drawRightString(564, 49, f'1 / {total_pages}')
    pdf.showPage()
    pdf.save()

    overlay_buffer = io.BytesIO()
    pdf = canvas.Canvas(overlay_buffer, pagesize=letter)
    _box(pdf, os.getenv('KIOSK_STORE_NAME', 'Registration Nation'), 317, 702.85, 220, 20, 9)
    _box(pdf, data['confirmation_number'], 374, 679.8, 163, 20, 8)
    _box(pdf, os.getenv('KIOSK_TITLE_CLERK', ''), 348, 656.75, 189, 20, 9)
    _box(pdf, data['registrant'], 193, 545.25, 122, 66, 10)
    _box(pdf, data['license_plate'].upper(), 389, 545.25, 148, 20, 10)
    _box(pdf, data['vin'].upper(), 355, 521.25, 182, 20, 9)

    pdf.setFillColor(HexColor('#444444'))
    pdf.setFont('Helvetica-Bold', 10)
    status = 'TEST PAYMENT - NO REAL MONEY' if test_payment else ('PAYMENT RECEIPT' if payment else 'PREVIEW - NO PAYMENT PROCESSED')
    pdf.drawString(125.45, 602, status)
    pdf.setFont('Helvetica', 9)
    pdf.drawString(125.45, 585, f'Date: {today:%m/%d/%Y}     Renewal: {month:02d}/{year}')
    # The existing checkout exposes only a total, not county/service fee components.
    pdf.setFont('Helvetica', 7.5)
    pdf.drawString(125.45, 478, '* Fee breakdown not supplied by checkout; verified total shown below.')
    pdf.setFillColor(black)
    pdf.setFont('Helvetica', 9)
    for baseline in (425.85, 400.4, 375.65):
        pdf.drawRightString(478, baseline, 'N/A*')
    rows = [(350.75, 0), (325.85, amount), (300.95, 0),
            (276.05, amount), (251.15, 0), (225.85, 0)]
    for baseline, cents in rows:
        pdf.drawRightString(478, baseline, f'{cents / 100:,.2f}')
    if payment:
        _box(pdf, 'Stripe payment reference: ' + str(payment['id']), 125.45, 569, 411, 16, 7)
    pdf.setFont('Helvetica', 7)
    pdf.drawString(125.45, 25, 'Test receipt; no real money charged.' if test_payment else ('No payment processed.' if not payment else ''))
    pdf.drawRightString(537, 25, f'2 / {total_pages}')
    pdf.showPage()
    pdf.save()

    template = PdfReader(TEMPLATE)
    if len(template.pages) != 1:
        raise ValueError('The TTT receipt template must contain exactly one page.')
    writer = PdfWriter()
    writer.add_page(PdfReader(io.BytesIO(sticker_buffer.getvalue())).pages[0])
    receipt = writer.add_page(template.pages[0])
    receipt.merge_page(PdfReader(io.BytesIO(overlay_buffer.getvalue())).pages[0])
    writer.add_metadata({'/Title': 'Registration sample and TTT receipt', '/Author': 'Registration Nation'})
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
