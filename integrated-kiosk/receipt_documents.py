"""Two-page registration sample and populated original TTT receipt."""
from __future__ import annotations

import io
import os
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, RectangleObject
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

TEMPLATE = Path(__file__).resolve().parent / 'assets' / 'ttt-receipt.pdf'


# ---- the emailed copy: page 2 only, without the "Customer Signature" block ------------------------------------------
# Nobody can sign an email, so the emailed receipt drops the signature heading, its short line and the legal paragraphs.
# In the original TTT template all of those sit below SIGNATURE_CUTOFF (points up from the bottom of the page) and the
# last line of the fee table sits above it. They are REMOVED from the page, not just covered up, and then the page
# is cropped to just below the table so the email doesn't show a big empty space.
SIGNATURE_CUTOFF = 214
EMAIL_COPY_BOTTOM = 185
_PAINT = {b'S', b's', b'f', b'F', b'f*', b'B', b'B*', b'b', b'b*'}
_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _times(m, n):
    """PDF matrix product m x n, each written as (a, b, c, d, e, f)."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)


def _page_y(matrix, x, y):
    """Where a point really sits on the page (the template moves its coordinates around with transforms)."""
    return matrix[1] * x + matrix[3] * y + matrix[5]


def _remove_signature_block(page):
    content = ContentStream(page.get_contents(), page.pdf)
    ctm, saved = _IDENTITY, []
    kept, text, text_matrix, text_y = [], None, _IDENTITY, None
    path, path_ys, clip, removed = [], [], False, 0
    for operands, op in content.operations:
        item = (operands, op)
        if op == b'q':
            saved.append(ctm)
        elif op == b'Q':
            ctm = saved.pop() if saved else _IDENTITY
        elif op == b'cm':
            ctm = _times(tuple(float(v) for v in operands), ctm)
        if text is not None:                      # inside BT ... ET: keep or drop the whole text block at ET
            text.append(item)
            if op == b'Tm':
                text_matrix = tuple(float(v) for v in operands)
            elif op in (b'Td', b'TD'):
                text_matrix = _times((1.0, 0.0, 0.0, 1.0, float(operands[0]), float(operands[1])), text_matrix)
            elif op in (b'Tj', b'TJ', b"'", b'"') and text_y is None:
                text_y = _page_y(ctm, text_matrix[4], text_matrix[5])
            elif op == b'ET':
                if text_y is not None and text_y < SIGNATURE_CUTOFF:
                    removed += 1
                else:
                    kept.extend(text)
                text = None
            continue
        if op == b'BT':
            text, text_matrix, text_y = [item], _IDENTITY, None
        elif op in (b'm', b'l'):
            path.append(item)
            path_ys.append(_page_y(ctm, float(operands[0]), float(operands[1])))
        elif op in (b'c', b'v', b'y'):
            path.append(item)
            coords = [float(v) for v in operands]
            path_ys += [_page_y(ctm, coords[i], coords[i + 1]) for i in range(0, len(coords), 2)]
        elif op == b're':
            path.append(item)
            x, y, w, h = (float(v) for v in operands)
            path_ys += [_page_y(ctm, cx, cy) for cx in (x, x + w) for cy in (y, y + h)]
        elif op == b'h':
            path.append(item)
        elif op in (b'W', b'W*'):
            path.append(item)
            clip = True                           # a clipping box: always kept, it only limits what is drawn inside it
        elif path and (op in _PAINT or op == b'n'):
            if op != b'n' and not clip and path_ys and max(path_ys) < SIGNATURE_CUTOFF:
                removed += 1                      # a line or box of the signature block: drop it with its paint step
            else:
                kept.extend(path)
                kept.append(item)
            path, path_ys, clip = [], [], False
        else:
            kept.append(item)
    if text is not None:
        kept.extend(text)
    if not removed:
        raise ValueError('The signature block was not found in the TTT receipt template. Update SIGNATURE_CUTOFF for the new template.')
    content.operations = kept
    page.replace_contents(content)


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


def build_documents(data, payment=None, *, today=None, email_copy=False):
    # email_copy=True gives the one-page emailed receipt: no sticker page, no signature block, no "2 / 2"
    # The public printing wrapper validates data; keep this internal API reusable for previews/tests.
    today = today or datetime.now(ZoneInfo(os.getenv('KIOSK_TIMEZONE', 'America/Chicago'))).date()
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
    pdf.drawRightString(564, 49, '1 / 2')
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
    footer_y = EMAIL_COPY_BOTTOM + 12 if email_copy else 25     # the emailed copy is cropped shorter, so its footer moves up with it
    pdf.drawString(125.45, footer_y, 'Test receipt; no real money charged.' if test_payment else ('No payment processed.' if not payment else ''))
    if not email_copy:
        pdf.drawRightString(537, 25, '2 / 2')
    pdf.showPage()
    pdf.save()

    template = PdfReader(TEMPLATE)
    if len(template.pages) != 1:
        raise ValueError('The TTT receipt template must contain exactly one page.')
    writer = PdfWriter()
    if not email_copy:
        writer.add_page(PdfReader(io.BytesIO(sticker_buffer.getvalue())).pages[0])
    receipt = writer.add_page(template.pages[0])
    if email_copy:
        _remove_signature_block(receipt)          # from the template only, before our own values go on top of it
    receipt.merge_page(PdfReader(io.BytesIO(overlay_buffer.getvalue())).pages[0])
    if email_copy:
        box = RectangleObject([0, EMAIL_COPY_BOTTOM, 612, 792])
        receipt.mediabox = receipt.cropbox = receipt.trimbox = receipt.bleedbox = receipt.artbox = box
    writer.add_metadata({'/Title': 'Texas Tag & Title receipt' if email_copy else 'Registration sample and TTT receipt',
                         '/Author': 'Registration Nation'})
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
