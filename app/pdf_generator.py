import io
import uuid
import os
from datetime import datetime
from typing import List, Dict, Any, Tuple
from decimal import Decimal, ROUND_HALF_UP
import xml.etree.ElementTree as ET

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, ArrayObject, TextStringObject, StreamObject
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, Frame, PageTemplate

# --- Constante ---
BASE_DIR = os.path.dirname(__file__)
FONT_PATH = os.path.join(BASE_DIR, 'DejaVuSans.ttf')
ICC_PROFILE_PATH = os.path.join(BASE_DIR, 'sRGB_IEC61966-2-1_black_scaled.icc')

# --- Functii Ajutatoare ---

def _calculate_totals(items: List[Dict[str, Any]], is_small_business: bool = False) -> Tuple[Decimal, Decimal, Decimal, Dict[Decimal, Dict[str, Decimal]]]:
    """
    Calculează totalurile pe baza articolelor, gestionând multiple cote de TVA.
    Returnează: total_netto, total_vat, total_brutto, vat_summary.
    vat_summary are formatul: {rate: {'basis': basis_amount, 'vat': vat_amount}}
    """
    TWO_PLACES = Decimal("0.01")
    total_netto = Decimal("0.0")
    vat_summary = {}

    for item in items:
        quantity = Decimal(str(item.get("quantity", "0") or "0"))
        unit_price = Decimal(str(item.get("unit_price", "0") or "0"))
        vat_rate = Decimal("0.0") if is_small_business else Decimal(str(item.get("vat_rate", "0") or "0"))
        
        line_netto = quantity * unit_price
        total_netto += line_netto
        
        if vat_rate not in vat_summary:
            vat_summary[vat_rate] = {'basis': Decimal("0.0"), 'vat': Decimal("0.0")}
        
        vat_summary[vat_rate]['basis'] += line_netto
        # Rotunjim TVA-ul per linie, o practică comună
        vat_summary[vat_rate]['vat'] += (line_netto * (vat_rate / Decimal("100.0"))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    total_vat = sum(summary['vat'] for summary in vat_summary.values())
    total_brutto = total_netto + total_vat
    
    return total_netto, total_vat, total_brutto, vat_summary

def _get_tax_category_code(vat_rate_str: str) -> str:
    """Returnează codul categoriei de taxă ZUGFeRD."""
    rate = Decimal(vat_rate_str)
    if rate > 0:
        return "S"  # Standard rate
    # Conform EN 16931, pentru scutiri se folosește "O" (Outside scope of tax)
    return "O"

# --- Generare PDF Vizual (ReportLab) ---

def _build_reportlab_pdf(buffer: io.BytesIO, form_data: Dict[str, Any], items: List[Dict[str, Any]], logo_path: str = None):
    """Construiește partea vizuală a PDF-ului folosind ReportLab."""
    if os.path.exists(FONT_PATH):
        pdfmetrics.registerFont(TTFont('DejaVuSans', FONT_PATH))
        main_font = 'DejaVuSans'
    else:
        print("Warning: DejaVuSans.ttf not found. Font will not be embedded.")
        main_font = 'Helvetica'
    
    # Define a footer drawing function
    def _draw_header_and_footer(canvas, doc):
        # --- Desenează Subsolul (Footer) ---
        canvas.saveState()
        canvas.setFont(main_font, 8)
        
        footer_lines = []
        is_small_business_footer = form_data.get('is_small_business') == 'true'
        notes = form_data.get('notes', '')
        if is_small_business_footer:
            notes = "Gemäß § 19 UStG wird keine Umsatzsteuer berechnet.\n" + notes
        if notes.strip():
            footer_lines.append(f"<b>Anmerkungen / Zahlungsbedingungen:</b><br/>{notes.strip().replace(chr(10), '<br/>')}")

        details_lines = []
        if form_data.get('sender_iban'): details_lines.append(f"<b>IBAN:</b> {form_data.get('sender_iban')}")
        if form_data.get('sender_tax_id'): details_lines.append(f"<b>Steuernummer:</b> {form_data.get('sender_tax_id')}")
        if form_data.get('register_court'): details_lines.append(f"<b>Amtsgericht:</b> {form_data.get('register_court')}")
        if form_data.get('register_number'): details_lines.append(f"<b>Registernummer:</b> {form_data.get('register_number')}")
        if form_data.get('managing_director'): details_lines.append(f"<b>Geschäftsführer:</b> {form_data.get('managing_director')}")

        notes_paragraph = Paragraph("<br/>".join(footer_lines), styles['Normal'])
        details_paragraph = Paragraph("<br/>".join(details_lines), styles['Normal'])
        footer_table = Table([[notes_paragraph, details_paragraph]], colWidths=[doc.width/2, doc.width/2])
        footer_table.wrapOn(canvas, doc.width, doc.bottomMargin)
        footer_table.drawOn(canvas, doc.leftMargin, 20 * mm)
        canvas.restoreState()

        # --- Desenează Antetul (Header) ---
        canvas.saveState()
        # Logo
        if logo_path:
            try:
                canvas.drawImage(logo_path, 15 * mm, A4[1] - 30 * mm, width=40*mm, height=20*mm, preserveAspectRatio=True, anchor='n')
            except Exception as e:
                print(f"Error drawing logo: {e}")
        
        # Adresa destinatar (plic cu fereastră)
        receiver_address = f"""{form_data.get('receiver_name', '')}<br/>{form_data.get('receiver_address', '')}<br/>{form_data.get('receiver_zip', '')} {form_data.get('receiver_city', '')}"""
        p = Paragraph(receiver_address, styles['Address'])
        p.wrapOn(canvas, 85 * mm, 40 * mm)
        p.drawOn(canvas, 20 * mm, A4[1] - 45 * mm - p.height) # Poziționare corectă DIN 5008

        # Adresa expeditor (deasupra adresei destinatarului, pentru retur)
        sender_line = f"{form_data.get('sender_name', '')} - {form_data.get('sender_address', '')} - {form_data.get('sender_zip', '')} {form_data.get('sender_city', '')}"
        canvas.setFont(main_font, 8)
        canvas.drawString(20 * mm, A4[1] - 42 * mm, sender_line)

        # Blocul de informații (Rechnungsnummer, Datum etc.) în dreapta
        invoice_details = f"""<b>Rechnungsnummer:</b> {form_data.get('invoice_number', '')}<br/><b>Rechnungsdatum:</b> {form_data.get('invoice_date', '')}<br/><b>Lieferdatum:</b> {form_data.get('delivery_date', '')}<br/><b>Fälligkeitsdatum:</b> {form_data.get('due_date', '')}"""
        p_details = Paragraph(invoice_details, styles["Normal"])
        p_details.wrapOn(canvas, 80 * mm, 40 * mm)
        p_details.drawOn(canvas, A4[0] - doc.rightMargin - 80 * mm, A4[1] - 60 * mm)
        canvas.restoreState()

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='RightAlign', alignment=2, fontName=main_font))
    styles.add(ParagraphStyle(name='SenderLine', fontName=main_font, fontSize=8, leading=10, alignment=2))
    styles['Normal'].fontName = main_font
    styles.add(ParagraphStyle(name='Address', fontName=main_font, fontSize=10, leading=12))
    styles['h3'].fontName = main_font
    styles['h3'].fontSize = 10
    styles['Normal'].fontSize = 8
    styles['Normal'].leading = 10

    # Definim marginile pentru conținutul principal, lăsând spațiu sus pentru antet
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=65 * mm, leftMargin=20 * mm, rightMargin=20 * mm, bottomMargin=45 * mm)

    story = []

    # Am eliminat detaliile din fluxul principal, deoarece sunt acum desenate în antet.

    # Tabel Articole
    def format_currency(value):
        return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    is_small_business = form_data.get('is_small_business') == 'true'
    total_netto, total_vat, total_brutto, vat_summary_details = _calculate_totals(items, is_small_business)

    data = [["Beschreibung", "Menge", "Einzelpreis", "Gesamt"]]
    for item in items:
        menge, preis = Decimal(str(item.get("quantity", "0") or "0")), Decimal(str(item.get("unit_price", "0") or "0"))
        vat_rate = Decimal("0.0") if is_small_business else Decimal(str(item.get("vat_rate", "0") or "0"))
        gesamt = (menge * preis) * (Decimal("1") + vat_rate / Decimal("100.0"))
        data.append([
            item.get("description", ""),
            str(menge),
            f"{format_currency(preis)} €",
            f"{format_currency(gesamt)} €"
        ])

    # Adaugă rândurile de total
    data.append(["", "", Paragraph("Netto:", styles['RightAlign']), f"{format_currency(total_netto)} €"])
    
    # Afișează TVA-ul doar dacă nu este Kleinunternehmer și există TVA
    if not is_small_business:
        for rate, summary in sorted(vat_summary_details.items()):
            if summary['vat'] > 0:
                data.append(["", "", Paragraph(f"{rate}% MwSt:", styles['RightAlign']), f"{format_currency(summary['vat'])} €"])

    data.append(["", "", Paragraph("<b>Gesamt:</b>", styles['RightAlign']), Paragraph(f"<b>{format_currency(total_brutto)} €</b>", styles['RightAlign'])])

    item_table = Table(data, colWidths=[90 * mm, 25 * mm, 30 * mm, 30 * mm])
    item_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#BCBEC0")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -4), 0.5, colors.grey),
        ('ALIGN', (1, 1), (-1, -1), 'RIGHT'), # Span Netto
        ('SPAN', (0, -len(vat_summary_details)-2), (1, -len(vat_summary_details)-2)),
        ('SPAN', (0, -1), (1, -1)), # Span Gesamt
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
    ]))
    story.append(item_table)
    story.append(Spacer(1, 10 * mm))

    # Build the document with the footer function
    doc.build(story, onFirstPage=_draw_header_and_footer, onLaterPages=_draw_header_and_footer)

# --- Generare XML ZUGFeRD ---

def _generate_zugferd_xml(form_data: Dict[str, Any], items: List[Dict[str, Any]]) -> bytes:
    """Generează XML-ul ZUGFeRD (Factur-X) folosind profilul BASIC pentru compatibilitate maximă."""
    invoice_date_str = form_data.get('invoice_date', datetime.now().strftime('%Y-%m-%d'))
    invoice_date_obj = datetime.strptime(invoice_date_str, '%Y-%m-%d')
    
    TWO_PLACES = Decimal("0.01")
    FOUR_PLACES = Decimal("0.0001")

    is_small_business = form_data.get('is_small_business') == 'true'
    total_netto, total_vat, total_brutto, vat_summary = _calculate_totals(items, is_small_business)
    
    ns = {
        'rsm': "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
        'ram': "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
        'udt': "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"
    }

    ET.register_namespace('rsm', ns['rsm'])
    ET.register_namespace('ram', ns['ram'])
    ET.register_namespace('udt', ns['udt'])

    root = ET.Element(f"{{{ns['rsm']}}}CrossIndustryInvoice")

    # Context - Folosim profilul BASIC (Sigur pentru validare)
    context = ET.SubElement(root, f"{{{ns['rsm']}}}ExchangedDocumentContext")
    param = ET.SubElement(context, f"{{{ns['ram']}}}GuidelineSpecifiedDocumentContextParameter")
    # ID-ul oficial pentru ZUGFeRD 2.2 / Factur-X 1.0 BASIC Profile
    ET.SubElement(param, f"{{{ns['ram']}}}ID").text = "urn:cen.eu:en16931:2017#compliant#urn:xoev-de:kosit:standard:xrechnung_2.2"

    # Header Document
    doc_header = ET.SubElement(root, f"{{{ns['rsm']}}}ExchangedDocument")
    ET.SubElement(doc_header, f"{{{ns['ram']}}}ID").text = form_data.get('invoice_number', f'INV-{uuid.uuid4().hex[:6].upper()}')
    ET.SubElement(doc_header, f"{{{ns['ram']}}}TypeCode").text = "380" # Commercial Invoice
    issue_date = ET.SubElement(doc_header, f"{{{ns['ram']}}}IssueDateTime")
    ET.SubElement(issue_date, f"{{{ns['udt']}}}DateTimeString", format="102").text = invoice_date_obj.strftime('%Y%m%d')

    # Tranzactie
    transaction = ET.SubElement(root, f"{{{ns['rsm']}}}SupplyChainTradeTransaction")

    # Linii Articole
    for i, item in enumerate(items, 1):
        menge = Decimal(str(item.get("quantity", "0") or "0"))
        preis = Decimal(str(item.get("unit_price", "0") or "0"))
        vat_rate = Decimal("0.0") if is_small_business else Decimal(str(item.get("vat_rate", "0") or "0"))
        line_total = (menge * preis).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        line_item = ET.SubElement(transaction, f"{{{ns['ram']}}}IncludedSupplyChainTradeLineItem")
        line_doc = ET.SubElement(line_item, f"{{{ns['ram']}}}AssociatedDocumentLineDocument")
        ET.SubElement(line_doc, f"{{{ns['ram']}}}LineID").text = str(i)
        
        product = ET.SubElement(line_item, f"{{{ns['ram']}}}SpecifiedTradeProduct")
        ET.SubElement(product, f"{{{ns['ram']}}}Name").text = item.get("description", "Beschreibung")

        delivery = ET.SubElement(line_item, f"{{{ns['ram']}}}SpecifiedLineTradeDelivery")
        ET.SubElement(delivery, f"{{{ns['ram']}}}BilledQuantity", unitCode="C62").text = f"{menge.quantize(FOUR_PLACES)}"

        settlement = ET.SubElement(line_item, f"{{{ns['ram']}}}SpecifiedLineTradeSettlement")
        tax = ET.SubElement(settlement, f"{{{ns['ram']}}}ApplicableTradeTax")
        ET.SubElement(tax, f"{{{ns['ram']}}}TypeCode").text = "VAT"
        category_code = _get_tax_category_code(str(vat_rate))
        ET.SubElement(tax, f"{{{ns['ram']}}}CategoryCode").text = category_code
        if category_code == "O":
            ET.SubElement(tax, f"{{{ns['ram']}}}ExemptionReasonCode").text = "VATEX-EU-DE"
        ET.SubElement(tax, f"{{{ns['ram']}}}RateApplicablePercent").text = f"{vat_rate.quantize(TWO_PLACES)}"

        summation = ET.SubElement(settlement, f"{{{ns['ram']}}}SpecifiedTradeSettlementLineMonetarySummation")
        ET.SubElement(summation, f"{{{ns['ram']}}}LineTotalAmount").text = f"{line_total.quantize(TWO_PLACES)}"

        trade_agreement = ET.SubElement(line_item, f"{{{ns['ram']}}}SpecifiedLineTradeAgreement")
        trade_price = ET.SubElement(trade_agreement, f"{{{ns['ram']}}}NetPriceProductTradePrice") 
        ET.SubElement(trade_price, f"{{{ns['ram']}}}ChargeAmount").text = f"{preis.quantize(FOUR_PLACES)}"

    # Acord Comercial (Vânzător / Cumpărător)
    agreement = ET.SubElement(transaction, f"{{{ns['ram']}}}ApplicableHeaderTradeAgreement")
    seller = ET.SubElement(agreement, f"{{{ns['ram']}}}SellerTradeParty")
    ET.SubElement(seller, f"{{{ns['ram']}}}Name").text = form_data.get('sender_name', 'Verkaeufer')
    seller_addr = ET.SubElement(seller, f"{{{ns['ram']}}}PostalTradeAddress")
    ET.SubElement(seller_addr, f"{{{ns['ram']}}}PostcodeCode").text = form_data.get('sender_zip', '')
    ET.SubElement(seller_addr, f"{{{ns['ram']}}}LineOne").text = form_data.get('sender_address', '')
    ET.SubElement(seller_addr, f"{{{ns['ram']}}}CityName").text = form_data.get('sender_city', '')
    ET.SubElement(seller_addr, f"{{{ns['ram']}}}CountryID").text = "DE"
    
    # Tax ID Vanzator (Steuernummer)
    if form_data.get('sender_tax_id'):
        seller_tax = ET.SubElement(seller, f"{{{ns['ram']}}}SpecifiedTaxRegistration")
        ET.SubElement(seller_tax, f"{{{ns['ram']}}}ID", schemeID="FC").text = form_data.get('sender_tax_id') # FC = Tax Number

    # VAT ID Vanzator (USt-IdNr.)
    if form_data.get('sender_vat_id'):
        seller_vat = ET.SubElement(seller, f"{{{ns['ram']}}}SpecifiedTaxRegistration")
        ET.SubElement(seller_vat, f"{{{ns['ram']}}}ID", schemeID="VA").text = form_data.get('sender_vat_id') # VA = VAT Number

    buyer = ET.SubElement(agreement, f"{{{ns['ram']}}}BuyerTradeParty")
    # Leitweg-ID (BT-10), esențial pentru facturile către autorități publice (XRechnung)
    if form_data.get('leitweg_id'):
        ET.SubElement(buyer, f"{{{ns['ram']}}}BuyerReference").text = form_data.get('leitweg_id')

    ET.SubElement(buyer, f"{{{ns['ram']}}}Name").text = form_data.get('receiver_name', 'Kunde')
    buyer_addr = ET.SubElement(buyer, f"{{{ns['ram']}}}PostalTradeAddress")
    ET.SubElement(buyer_addr, f"{{{ns['ram']}}}PostcodeCode").text = form_data.get('receiver_zip', '')
    ET.SubElement(buyer_addr, f"{{{ns['ram']}}}LineOne").text = form_data.get('receiver_address', '')
    ET.SubElement(buyer_addr, f"{{{ns['ram']}}}CityName").text = form_data.get('receiver_city', '')
    ET.SubElement(buyer_addr, f"{{{ns['ram']}}}CountryID").text = "DE"

    # Decontare (Totale)
    header_settlement = ET.SubElement(transaction, f"{{{ns['ram']}}}ApplicableHeaderTradeSettlement")
    ET.SubElement(header_settlement, f"{{{ns['ram']}}}InvoiceCurrencyCode").text = "EUR"
    
    if form_data.get('sender_iban'):
        payment_means = ET.SubElement(header_settlement, f"{{{ns['ram']}}}SpecifiedTradeSettlementPaymentMeans")
        ET.SubElement(payment_means, f"{{{ns['ram']}}}TypeCode").text = "30" # Transfer bancar
        account = ET.SubElement(payment_means, f"{{{ns['ram']}}}PayeePartyCreditorFinancialAccount")
        ET.SubElement(account, f"{{{ns['ram']}}}IBANID").text = form_data.get('sender_iban')

    # Taxe Totale
    for rate, summary in vat_summary.items():
        trade_tax = ET.SubElement(header_settlement, f"{{{ns['ram']}}}ApplicableTradeTax")
        ET.SubElement(trade_tax, f"{{{ns['ram']}}}CalculatedAmount").text = f"{summary['vat'].quantize(TWO_PLACES)}"
        ET.SubElement(trade_tax, f"{{{ns['ram']}}}TypeCode").text = "VAT"
        ET.SubElement(trade_tax, f"{{{ns['ram']}}}BasisAmount").text = f"{summary['basis'].quantize(TWO_PLACES)}"
        ET.SubElement(trade_tax, f"{{{ns['ram']}}}CategoryCode").text = _get_tax_category_code(str(rate.quantize(TWO_PLACES)))
        # Adaugă motivul scutirii dacă este cazul (esențial pentru codul 'O')
        if _get_tax_category_code(str(rate.quantize(TWO_PLACES))) == "O":
            ET.SubElement(trade_tax, f"{{{ns['ram']}}}ExemptionReasonCode").text = "VATEX-EU-DE" # Cod recomandat pentru scutiri naționale germane, inclusiv §19 UStG
        ET.SubElement(trade_tax, f"{{{ns['ram']}}}RateApplicablePercent").text = f"{rate.quantize(TWO_PLACES)}"
    
    header_summation = ET.SubElement(header_settlement, f"{{{ns['ram']}}}SpecifiedTradeSettlementHeaderMonetarySummation")
    ET.SubElement(header_summation, f"{{{ns['ram']}}}LineTotalAmount").text = f"{total_netto.quantize(TWO_PLACES)}"
    ET.SubElement(header_summation, f"{{{ns['ram']}}}TaxBasisTotalAmount").text = f"{total_netto.quantize(TWO_PLACES)}"
    ET.SubElement(header_summation, f"{{{ns['ram']}}}TaxTotalAmount", currencyID="EUR").text = f"{total_vat.quantize(TWO_PLACES)}"
    ET.SubElement(header_summation, f"{{{ns['ram']}}}GrandTotalAmount").text = f"{total_brutto.quantize(TWO_PLACES)}"
    ET.SubElement(header_summation, f"{{{ns['ram']}}}DuePayableAmount").text = f"{total_brutto.quantize(TWO_PLACES)}"
    
    return ET.tostring(root, encoding='UTF-8', xml_declaration=True)


# --- Asamblare PDF/A-3 (Atașare XML și Metadate) ---

def _make_pdfa_compliant(pdf_buffer: io.BytesIO, xml_data: bytes, form_data: Dict[str, Any]) -> io.BytesIO:
    """
    Modifică PDF-ul pentru a fi conform PDF/A-3 și atașează XML-ul conform standardului Factur-X/ZUGFeRD.
    """
    reader = PdfReader(pdf_buffer)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    # 1. Metadate XMP specifice ZUGFeRD (Obligatoriu pentru validare!)
    # Definește schema 'fx' care îi spune validatorului că e factură electronică
    xmp_metadata = f'''<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">
      <pdfaid:part>3</pdfaid:part>
      <pdfaid:conformance>B</pdfaid:conformance>
    </rdf:Description>
    <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">
      <dc:title><rdf:Alt><rdf:li xml:lang="x-default">Rechnung {form_data.get('invoice_number', '')}</rdf:li></rdf:Alt></dc:title>
      <dc:creator><rdf:Seq><rdf:li>{form_data.get('sender_name', '')}</rdf:li></rdf:Seq></dc:creator>
    </rdf:Description>
    <rdf:Description rdf:about="" xmlns:fx="urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#">
      <fx:DocumentType>INVOICE</fx:DocumentType>
      <fx:DocumentFileName>factur-x.xml</fx:DocumentFileName>
      <fx:Version>1.0</fx:Version> 
      <fx:ConformanceLevel>EN 16931</fx:ConformanceLevel>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>'''
    
    xmp_stream = StreamObject()
    xmp_stream.set_data(xmp_metadata.encode("utf-8"))
    xmp_stream.update({
        NameObject("/Type"): NameObject("/Metadata"),
        NameObject("/Subtype"): NameObject("/XML")
    })
    writer._root_object[NameObject("/Metadata")] = writer._add_object(xmp_stream)

    # 2. Output Intent (ICC Profile)
    if os.path.exists(ICC_PROFILE_PATH):
        with open(ICC_PROFILE_PATH, "rb") as f:
            icc_profile_data = f.read()
        icc_stream = StreamObject()
        icc_stream.set_data(icc_profile_data)
        icc_stream.update({
            NameObject("/N"): NameObject("3"), # RGB = 3 components
            NameObject("/Alternate"): NameObject("/DeviceRGB")
        })
        icc_stream_ref = writer._add_object(icc_stream)
        output_intent = DictionaryObject({
            NameObject("/Type"): NameObject("/OutputIntent"),
            NameObject("/S"): NameObject("/GTS_PDFA1"),
            NameObject("/OutputConditionIdentifier"): TextStringObject("sRGB IEC61966-2.1"),
            NameObject("/Info"): TextStringObject("sRGB IEC61966-2.1"),
            NameObject("/DestOutputProfile"): icc_stream_ref,
        })
        writer._root_object.setdefault(NameObject("/OutputIntents"), ArrayObject()).append(output_intent)

    # 3. Atașare Fișier XML (factur-x.xml)
    xml_filename = "factur-x.xml"
    xml_stream = StreamObject()
    xml_stream.set_data(xml_data)
    
    # Detalii critice pentru ZUGFeRD: MimeType și ModDate
    xml_stream.update({
        NameObject("/Type"): NameObject("/EmbeddedFile"),
        NameObject("/Subtype"): NameObject("/text/xml"),
        NameObject("/Params"): DictionaryObject({
            NameObject("/ModDate"): TextStringObject(f"D:{datetime.now().strftime('%Y%m%d%H%M%S')}+00'00'")
        })
    })
    xml_stream_ref = writer._add_object(xml_stream)

    filespec_obj = DictionaryObject({
        NameObject("/Type"): NameObject("/Filespec"),
        NameObject("/F"): TextStringObject(xml_filename),
        NameObject("/EF"): DictionaryObject({NameObject("/F"): xml_stream_ref}),
        NameObject("/Desc"): TextStringObject("Factur-X Invoice"),
        # RELAȚIA: /Data este necesară pentru conformitate XRechnung (B2G)
        NameObject("/AFRelationship"): NameObject("/Data"), 
    })
    filespec_ref = writer._add_object(filespec_obj)

    # Adaugare in structura Names
    embedded_files_names_array = ArrayObject([TextStringObject(xml_filename), filespec_ref])
    writer._root_object.setdefault(NameObject("/Names"), DictionaryObject()).setdefault(NameObject("/EmbeddedFiles"), DictionaryObject({NameObject("/Names"): embedded_files_names_array}))
    
    # Adaugare in AF (Associated Files)
    writer._root_object.setdefault(NameObject("/AF"), ArrayObject()).append(filespec_ref)

    output_buffer = io.BytesIO()
    writer.write(output_buffer)
    output_buffer.seek(0)
    return output_buffer


def generate_invoice_pdf(form_data: dict, items: list, logo_path: str = None) -> io.BytesIO:
    """
    Generează un fișier PDF pentru factură, incluzând un atașament XML ZUGFeRD.
    """
    # Pas 1: Generează PDF-ul vizual
    reportlab_buffer = io.BytesIO()
    _build_reportlab_pdf(reportlab_buffer, form_data, items, logo_path)
    reportlab_buffer.seek(0)

    # Pas 2: Generează XML-ul ZUGFeRD
    xml_data = _generate_zugferd_xml(form_data, items)

    # Pas 3: Combină
    pdf_with_zugferd = _make_pdfa_compliant(reportlab_buffer, xml_data, form_data)
    return pdf_with_zugferd