import os
import base64
import importlib
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta, date
from dateutil import tz
from flask import Flask, jsonify, request, render_template, send_from_directory, abort, make_response, url_for
from io import BytesIO
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
import ssl
# Importaciones de Twilio (carga dinamica para evitar errores cuando falta la libreria)
TWILIO_AVAILABLE = False
Client = None
_twilio_rest_spec = importlib.util.find_spec("twilio.rest")
if _twilio_rest_spec:
    try:
        _twilio_rest_module = importlib.import_module("twilio.rest")
    except ImportError:
        _twilio_rest_module = None
    else:
        Client = getattr(_twilio_rest_module, "Client", None)
        TWILIO_AVAILABLE = Client is not None
if not TWILIO_AVAILABLE:
    print("Twilio no esta disponible. El envio por WhatsApp no funcionara.")

try:
    from dotenv import load_dotenv  # type: ignore
except ImportError:
    load_dotenv = None

# Importaciones de ReportLab (importación dinámica para evitar errores de linter)
REPORTLAB_AVAILABLE = False
try:
    import reportlab  # noqa: F401
    REPORTLAB_AVAILABLE = True
except ImportError:
    print("ReportLab no está disponible. Las funciones de PDF no funcionarán.")

WEASYPRINT_AVAILABLE = False
try:
    from weasyprint import HTML  # type: ignore
    WEASYPRINT_AVAILABLE = True
except (ImportError, OSError):
    print("WeasyPrint no esta disponible. Se usara el generador basico de PDF.")
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Date, ForeignKey, Numeric, Text, UniqueConstraint
)
from sqlalchemy import inspect
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, scoped_session
from sqlalchemy.exc import IntegrityError, OperationalError

# --------------------
# Configuración básica
# --------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if load_dotenv:
    load_dotenv(os.path.join(BASE_DIR, ".env"))
DB_PATH = os.path.join(BASE_DIR, "inventario.db")
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False, future=True)
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True))

Base = declarative_base()

app = Flask(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587") or 587)
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_USE_TLS = (os.getenv("SMTP_USE_TLS", "true").lower() != "false")
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Ciclo Variedades Sisi").strip()
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", SMTP_USERNAME).strip()

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "").strip()
TWILIO_SEND_MEDIA = os.getenv("TWILIO_SEND_MEDIA", "false").lower() == "true"
DEFAULT_COUNTRY_CODE = os.getenv("DEFAULT_COUNTRY_CODE", "57").lstrip("+")

# Helpers de decimales
def D(x):
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))

def money(x):
    return D(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def smtp_configured():
    return all([SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM_EMAIL])

def send_email_with_pdf(to_email, subject, body, pdf_bytes, filename):
    if not smtp_configured():
        raise RuntimeError("La configuración SMTP no está completa. Define SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD y SMTP_FROM_EMAIL.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((SMTP_FROM_NAME, SMTP_FROM_EMAIL))
    msg["To"] = to_email
    msg.set_content(body)
    msg.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename=filename,
    )

    context = ssl.create_default_context()
    if SMTP_USE_TLS:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
    else:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)

def _sanitize_whatsapp_sender(sender_raw:str|None) -> str|None:
    """Normaliza el remitente de Twilio aceptando formatos con o sin 'whatsapp:'."""
    if not sender_raw:
        return None
    sender = sender_raw.strip()
    if not sender:
        return None
    if sender.lower().startswith("whatsapp:"):
        sender = sender.split(":", 1)[1].strip()
    digits = "".join(ch for ch in sender if ch in "+0123456789")
    if not digits:
        return None
    if not digits.startswith("+"):
        digits = f"+{digits.lstrip('+')}"
    return f"whatsapp:{digits}"

def twilio_configured():
    return TWILIO_AVAILABLE and all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, _sanitize_whatsapp_sender(TWILIO_WHATSAPP_FROM)])

def normalize_phone_number(raw_phone:str):
    if not raw_phone:
        return None
    raw_phone = raw_phone.strip()
    if not raw_phone:
        return None
    if raw_phone.startswith("+"):
        digits = "+" + "".join(ch for ch in raw_phone if ch.isdigit())
        return digits if len(digits) > 1 else None
    digits = "".join(ch for ch in raw_phone if ch.isdigit())
    if not digits:
        return None
    country = DEFAULT_COUNTRY_CODE or ""
    if country and digits.startswith(country):
        return f"+{digits}"
    if country:
        digits = digits.lstrip("0")
        return f"+{country}{digits}"
    return f"+{digits}"

def send_whatsapp_message(to_phone:str, body:str, media_url:str|None=None):
    if not twilio_configured():
        raise RuntimeError("La configuración de Twilio no está completa.")
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    from_number = _sanitize_whatsapp_sender(TWILIO_WHATSAPP_FROM)
    if not from_number:
        raise RuntimeError("El n�mero remitente de Twilio no es v�lido. Usa un formato como '+573001234567'.")
    to_number = f"whatsapp:{to_phone}" if not to_phone.startswith("whatsapp:") else to_phone
    message_kwargs = {
        "body": body,
        "from_": from_number,
        "to": to_number,
    }
    if media_url and TWILIO_SEND_MEDIA:
        if media_url.startswith(("http://", "https://")):
            message_kwargs["media_url"] = [media_url]
        else:
            print(f"[Twilio] Se ignoro media_url no valida: {media_url}")
    client.messages.create(**message_kwargs)

def number_to_spanish_words(value):
    """Convierte un número a su representación en letras (solo parte entera)."""
    n = int(D(value).to_integral_value(rounding=ROUND_HALF_UP))
    if n == 0:
        return "CERO"

    unidades = ["", "UNO", "DOS", "TRES", "CUATRO", "CINCO", "SEIS", "SIETE", "OCHO", "NUEVE"]
    especiales = ["DIEZ", "ONCE", "DOCE", "TRECE", "CATORCE", "QUINCE"]
    decenas_lista = ["", "", "VEINTE", "TREINTA", "CUARENTA", "CINCUENTA", "SESENTA", "SETENTA", "OCHENTA", "NOVENTA"]
    centenas_lista = ["", "CIENTO", "DOSCIENTOS", "TRESCIENTOS", "CUATROCIENTOS", "QUINIENTOS", "SEISCIENTOS", "SETECIENTOS", "OCHOCIENTOS", "NOVECIENTOS"]

    def decenas(num):
        if num < 10:
            return unidades[num]
        if 10 <= num < 16:
            return especiales[num - 10]
        if 16 <= num < 20:
            return "DIECI" + unidades[num - 10]
        if num == 20:
            return "VEINTE"
        if 21 <= num < 30:
            return "VEINTI" + unidades[num - 20]
        d = num // 10
        u = num % 10
        texto = decenas_lista[d]
        if u:
            texto = f"{texto} Y {unidades[u]}"
        return texto

    def centenas(num):
        if num == 0:
            return ""
        if num == 100:
            return "CIEN"
        c = num // 100
        resto = num % 100
        partes = []
        if c:
            partes.append(centenas_lista[c])
        if resto:
            partes.append(decenas(resto))
        return " ".join(partes)

    def seccion(num, divisor, singular, plural):
        cantidad = num // divisor
        resto = num % divisor
        if cantidad == 0:
            return "", resto
        if cantidad == 1:
            texto = singular
        else:
            texto = f"{convertir(cantidad)} {plural}"
        return texto, resto

    def convertir(num):
        if num < 100:
            return decenas(num)
        if num < 1000:
            return centenas(num)
        miles_texto, resto = seccion(num, 1000, "MIL", "MIL")
        partes = [miles_texto] if miles_texto else []
        if resto:
            partes.append(convertir(resto))
        return " ".join(p for p in partes if p).strip()

    millones_texto, resto_millones = seccion(n, 1_000_000, "UN MILLÓN", "MILLONES")
    partes_finales = [millones_texto] if millones_texto else []
    if resto_millones:
        miles_texto, resto_miles = seccion(resto_millones, 1000, "MIL", "MIL")
        if miles_texto:
            partes_finales.append(miles_texto)
        if resto_miles:
            partes_finales.append(convertir(resto_miles))
    return " ".join(p for p in partes_finales if p).strip()

def build_invoice_template_context(invoice, *, for_pdf=False):
    """Prepara el contexto común usado por la plantilla de facturas."""
    total_amount = invoice.total if invoice.total is not None else invoice.subtotal_excl_vat
    total_amount = D(total_amount or 0)

    display_items = []
    for idx, item in enumerate(invoice.items, 1):
        quantity = item.quantity or 0
        qty_decimal = D(quantity)
        total_with_vat = money(item.total_incl_vat or 0)
        if qty_decimal:
            unit_with_vat = money(D(total_with_vat) / qty_decimal)
        else:
            unit_with_vat = money(0)
        display_items.append({
            "index": idx,
            "product": item.product,
            "quantity": quantity,
            "unit_price": unit_with_vat,
            "total": unit_with_vat,
            "raw": item,
        })

    context = {
        "invoice": invoice,
        "D": D,
        "money": money,
        "total_en_letras": f"{number_to_spanish_words(total_amount)} PESOS M/L",
        "logo_data_uri": None,
        "invoice_items_display": display_items,
        "items_fillers": max(0, 10 - len(display_items)),
        "total_to_pay": money(total_amount),
    }

    if for_pdf:
        logo_path = os.path.join(BASE_DIR, "static", "img", "ciclovariedadessisi.jpg")
        if os.path.exists(logo_path):
            try:
                with open(logo_path, "rb") as logo_file:
                    encoded = base64.b64encode(logo_file.read()).decode("ascii")
                    context["logo_data_uri"] = f"data:image/jpeg;base64,{encoded}"
            except OSError as exc:
                print(f"No fue posible cargar el logo para el PDF: {exc}")
    return context

def build_remission_template_context(remission, *, for_pdf=False):
    """Prepara el contexto común usado por la plantilla de remisiones."""
    total_amount = remission.total if remission.total is not None else remission.subtotal_excl_vat
    total_amount = D(total_amount or 0)

    display_items = []
    for idx, item in enumerate(remission.items, 1):
        quantity = item.quantity or 0
        qty_decimal = D(quantity)
        total_with_vat = money(item.total_incl_vat or 0)
        if qty_decimal:
            unit_with_vat = money(D(total_with_vat) / qty_decimal)
        else:
            unit_with_vat = money(0)
        display_items.append({
            "index": idx,
            "product": item.product,
            "quantity": quantity,
            "unit_price": unit_with_vat,
            "total": unit_with_vat,
            "raw": item,
        })

    context = {
        "remission": remission,
        "D": D,
        "money": money,
        "total_en_letras": f"{number_to_spanish_words(total_amount)} PESOS M/L",
        "logo_data_uri": None,
        "remission_items_display": display_items,
        "items_fillers": max(0, 10 - len(display_items)),
        "total_to_pay": money(total_amount),
    }

    if for_pdf:
        logo_path = os.path.join(BASE_DIR, "static", "img", "ciclovariedadessisi.jpg")
        if os.path.exists(logo_path):
            try:
                with open(logo_path, "rb") as logo_file:
                    encoded = base64.b64encode(logo_file.read()).decode("ascii")
                    context["logo_data_uri"] = f"data:image/jpeg;base64,{encoded}"
            except OSError as exc:
                print(f"No fue posible cargar el logo para el PDF: {exc}")
    return context

# --------------------
# Modelos
# --------------------

class Sequence(Base):
    __tablename__ = "sequences"
    name = Column(String, primary_key=True)
    next_value = Column(Integer, default=1, nullable=False)

def next_sequence(name, start_at=1):
    db = SessionLocal()
    try:
        seq = db.get(Sequence, name)
        if not seq:
            seq = Sequence(name=name, next_value=start_at)
            db.add(seq)
            db.commit()
            db.refresh(seq)
        val = seq.next_value
        seq.next_value = val + 1
        db.add(seq)
        db.commit()
        return val
    finally:
        db.close()

class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    phone = Column(String, default="")
    email = Column(String, default="")
    address = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    purchases = relationship("Purchase", back_populates="supplier")

class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    document_number = Column(String, default="")
    phone = Column(String, default="")
    email = Column(String, default="")
    address = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    sku = Column(String, unique=True, nullable=False)
    price = Column(Numeric(10, 2), default=0)  # precio de venta sin IVA
    vat_rate = Column(Numeric(4, 2), default=Decimal("0.19"))
    low_stock_threshold = Column(Integer, default=5)
    current_stock = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class Purchase(Base):
    __tablename__ = "purchases"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)  # código de compra
    supplier_id = Column(Integer, ForeignKey("suppliers.id"))
    date = Column(DateTime, default=datetime.utcnow)
    subtotal_excl_vat = Column(Numeric(12, 2), default=0)
    vat_total = Column(Numeric(12, 2), default=0)
    total = Column(Numeric(12, 2), default=0)
    notes = Column(Text, default="")

    supplier = relationship("Supplier", back_populates="purchases")
    items = relationship("PurchaseItem", back_populates="purchase", cascade="all, delete-orphan")

class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    id = Column(Integer, primary_key=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    quantity = Column(Integer, default=0)
    unit_cost = Column(Numeric(10, 2), default=0)  # sin IVA
    vat_rate = Column(Numeric(4, 2), default=Decimal("0.19"))
    total_excl_vat = Column(Numeric(12, 2), default=0)
    vat_amount = Column(Numeric(12, 2), default=0)
    total_incl_vat = Column(Numeric(12, 2), default=0)

    purchase = relationship("Purchase", back_populates="items")
    product = relationship("Product")

class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True)
    number = Column(String, unique=True, nullable=False)  # número de factura
    date = Column(DateTime, default=datetime.utcnow)
    customer_id = Column(Integer, ForeignKey("customers.id"))
    subtotal_excl_vat = Column(Numeric(12, 2), default=0)
    vat_total = Column(Numeric(12, 2), default=0)
    total = Column(Numeric(12, 2), default=0)
    payment_method = Column(String, default="EFECTIVO")  # método de pago

    customer = relationship("Customer")
    items = relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")

class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    quantity = Column(Integer, default=0)
    unit_price = Column(Numeric(10, 2), default=0)  # sin IVA
    vat_rate = Column(Numeric(4, 2), default=Decimal("0.19"))
    total_excl_vat = Column(Numeric(12, 2), default=0)
    vat_amount = Column(Numeric(12, 2), default=0)
    total_incl_vat = Column(Numeric(12, 2), default=0)

    invoice = relationship("Invoice", back_populates="items")
    product = relationship("Product")

class Remission(Base):
    __tablename__ = "remissions"
    id = Column(Integer, primary_key=True)
    number = Column(String, unique=True, nullable=False)  # número de remisión
    date = Column(DateTime, default=datetime.utcnow)
    customer_id = Column(Integer, ForeignKey("customers.id"))
    subtotal_excl_vat = Column(Numeric(12, 2), default=0)
    vat_total = Column(Numeric(12, 2), default=0)
    total = Column(Numeric(12, 2), default=0)
    payment_method = Column(String, default="EFECTIVO")  # método de pago

    customer = relationship("Customer")
    items = relationship("RemissionItem", back_populates="remission", cascade="all, delete-orphan")

class RemissionItem(Base):
    __tablename__ = "remission_items"
    id = Column(Integer, primary_key=True)
    remission_id = Column(Integer, ForeignKey("remissions.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    quantity = Column(Integer, default=0)
    unit_price = Column(Numeric(10, 2), default=0)  # sin IVA
    vat_rate = Column(Numeric(4, 2), default=Decimal("0.19"))
    total_excl_vat = Column(Numeric(12, 2), default=0)
    vat_amount = Column(Numeric(12, 2), default=0)
    total_incl_vat = Column(Numeric(12, 2), default=0)

    remission = relationship("Remission", back_populates="items")
    product = relationship("Product")

class StockMovement(Base):
    __tablename__ = "stock_movements"
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    movement_type = Column(String)  # purchase, invoice, remission, adjustment, initial
    quantity_change = Column(Integer)  # + / -
    note = Column(String, default="")
    reference_type = Column(String, default="")  # e.g. purchase, invoice
    reference_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    product = relationship("Product")

class MaintenanceReminder(Base):
    __tablename__ = "maintenance_reminders"
    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.id"))
    due_date = Column(Date, nullable=False)
    notes = Column(String, default="")
    reference_type = Column(String, default="")  # invoice/remission
    reference_id = Column(Integer, nullable=True)
    notified = Column(Integer, default=0)  # 0 false, 1 true
    created_at = Column(DateTime, default=datetime.utcnow)

    customer = relationship("Customer")

# --------------
# Inicialización
# --------------
Base.metadata.create_all(bind=engine)
inspector = inspect(engine)
try:
    customer_columns = [col["name"] for col in inspector.get_columns("customers")]
except OperationalError:
    customer_columns = []
if "email" not in customer_columns:
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE customers ADD COLUMN email VARCHAR DEFAULT """)
    except OperationalError:
        pass

# --------------------
# Funciones de PDF
# --------------------
def generate_invoice_pdf(invoice):
    """Genera un PDF de factura reutilizando la misma plantilla mostrada en pantalla."""
    context = build_invoice_template_context(invoice, for_pdf=True)
    context["is_pdf"] = True

    if WEASYPRINT_AVAILABLE:
        try:
            html_content = render_template("invoice.html", **context)
            return HTML(string=html_content, base_url=BASE_DIR).write_pdf()
        except Exception as exc:
            print(f"Error generando PDF con WeasyPrint: {exc}. Se intentará con ReportLab.")

    if not REPORTLAB_AVAILABLE:
        print("ReportLab no está disponible. No se puede generar PDF.")
        return None
    
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
            Image,
        )
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=12 * mm,
            leftMargin=12 * mm,
            topMargin=12 * mm,
            bottomMargin=12 * mm,
        )

        line_color = colors.HexColor('#1d1d1d')
        thin_color = colors.HexColor('#cfcfcf')
        header_bg = colors.HexColor('#e9f0ff')

        styles = getSampleStyleSheet()
        company_style = ParagraphStyle(
            'Company', parent=styles['Normal'], fontSize=11, leading=14, alignment=TA_CENTER
        )
        small_style = ParagraphStyle(
            'Small', parent=styles['Normal'], fontSize=9, leading=11
        )
        label_style = ParagraphStyle(
            'Label', parent=small_style, alignment=TA_LEFT, textColor=colors.HexColor('#333333')
        )
        value_style = ParagraphStyle('Value', parent=small_style, alignment=TA_LEFT)
        bold_value = ParagraphStyle('BoldValue', parent=value_style, fontName='Helvetica-Bold')
        pay_style = ParagraphStyle('PayStyle', parent=small_style, fontSize=12, leading=14, fontName='Helvetica-Bold')
        right_bold = ParagraphStyle('RightBold', parent=small_style, alignment=TA_RIGHT, fontName='Helvetica-Bold', fontSize=12, leading=14)

        story = []

        # Cabecera
        logo_path = os.path.join(BASE_DIR, 'static', 'img', 'ciclovariedadessisi.jpg')
        if os.path.exists(logo_path):
            logo_img = Image(logo_path, width=28 * mm, height=28 * mm)
        else:
            logo_img = Spacer(1, 28 * mm)

        company_lines = [
            'FREDY ALEXANDER GIRALDO GIRALDO',
            'NIT: 1143933804-9',
            'RÉGIMEN: SIMPLIFICADO',
            'CAR. 46 # 49 - 26',
            'CALI - COLOMBIA',
            'TEL: 3152441736',
        ]
        company_table = Table([[Paragraph(line, company_style)] for line in company_lines], colWidths=[80 * mm])
        company_table.setStyle(TableStyle([('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))

        social_box = Table([[Paragraph('Instagram: @ciclovariedades_sisi<br/>Facebook: ciclo variedades sisi', small_style)]], colWidths=[55 * mm])
        social_box.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), 0.5, thin_color), ('INNERPADDING', (0, 0), (-1, -1), 4), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))

        meta_table = Table(
            [
                [Paragraph('Factura No.', label_style), Paragraph(invoice.number, bold_value)],
                [Paragraph('Fecha factura', label_style), Paragraph(invoice.date.strftime('%d/%b/%Y').upper(), value_style)],
            ],
            colWidths=[30 * mm, 25 * mm],
        )
        meta_table.setStyle(
            TableStyle([
                ('BOX', (0, 0), (-1, -1), 0.6, line_color),
                ('INNERGRID', (0, 0), (-1, -1), 0.3, thin_color),
                ('BACKGROUND', (0, 0), (-1, -1), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ])
        )

        right_panel = Table([[social_box], [Spacer(1, 4 * mm)], [meta_table]], colWidths=[60 * mm])
        right_panel.setStyle(TableStyle([('ALIGN', (0, 0), (-1, -1), 'RIGHT'), ('VALIGN', (0, 0), (-1, -1), 'TOP')]))

        header_table = Table([[logo_img, company_table, right_panel]], colWidths=[32 * mm, 86 * mm, 60 * mm])
        header_table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('ALIGN', (1, 0), (1, 0), 'CENTER'), ('LINEBELOW', (0, 0), (-1, 0), 1, line_color)]))
        story.append(header_table)
        story.append(Spacer(1, 6 * mm))

        dash = '—'
        client_left_data = [
            [Paragraph('SEÑORES:', label_style), Paragraph(invoice.customer.name or dash, value_style)],
            [Paragraph('DIRECCIÓN:', label_style), Paragraph(invoice.customer.address or dash, value_style)],
            [Paragraph('CIUDAD:', label_style), Paragraph('CALI-VALLE', value_style)],
        ]
        client_right_data = [
            [Paragraph('NIT/CC:', label_style), Paragraph(invoice.customer.document_number or dash, value_style)],
            [Paragraph('TELÉFONO:', label_style), Paragraph(invoice.customer.phone or dash, value_style)],
            [Paragraph('EMAIL:', label_style), Paragraph(getattr(invoice.customer, 'email', dash) or dash, value_style)],
            [Paragraph('VENDEDOR:', label_style), Paragraph('ALEXANDER GIRALDO', value_style)],
        ]

        left_table = Table(client_left_data, colWidths=[35 * mm, 60 * mm])
        left_table.setStyle(TableStyle([('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f6f6f6')), ('BOX', (0, 0), (-1, -1), 0.6, thin_color), ('INNERGRID', (0, 0), (-1, -1), 0.4, thin_color), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6)]))

        right_table = Table(client_right_data, colWidths=[35 * mm, 60 * mm])
        right_table.setStyle(TableStyle([('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f6f6f6')), ('BOX', (0, 0), (-1, -1), 0.6, thin_color), ('INNERGRID', (0, 0), (-1, -1), 0.4, thin_color), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6)]))

        client_section = Table([[left_table, right_table]], colWidths=[95 * mm, 95 * mm])
        client_section.setStyle(TableStyle([('LINEABOVE', (0, 0), (-1, -1), 0.8, line_color), ('LINEBELOW', (0, 0), (-1, -1), 0.8, line_color), ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
        story.append(client_section)
        story.append(Spacer(1, 6 * mm))

        items_data = [['ITEM', 'CÓDIGO', 'DESCRIPCIÓN', 'CANTIDAD', 'VALOR UNI.', 'VALOR TOTAL']]
        for row in context["invoice_items_display"]:
            items_data.append([
                str(row["index"]),
                row["product"].sku,
                row["product"].name,
                f"{row['quantity']}",
                f"${row['unit_price']:,.0f}",
                f"${row['total']:,.0f}",
            ])

        for _ in range(context["items_fillers"]):
            items_data.append(['', '', '', '', '', ''])

        col_widths = [12 * mm, 24 * mm, 72 * mm, 19 * mm, 29 * mm, 32 * mm]
        items_table = Table(items_data, colWidths=col_widths, repeatRows=1)
        items_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), header_bg),
            ('TEXTCOLOR', (0, 0), (-1, 0), line_color),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('TOPPADDING', (0, 0), (-1, 0), 6),
            ('ALIGN', (0, 1), (0, -1), 'RIGHT'),
            ('ALIGN', (3, 1), (-1, -1), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, line_color),
        ]))
        story.append(items_table)
        story.append(Spacer(1, 6 * mm))

        story.append(Paragraph(f"TOTAL EN LETRAS: {number_to_spanish_words(context["total_to_pay"])} PESOS M/L", small_style))
        story.append(Paragraph(f"MEDIO DE PAGO: {invoice.payment_method or 'EFECTIVO'}", pay_style))
        story.append(Spacer(1, 6 * mm))

        totals_table = Table([[Paragraph('TOTAL A PAGAR', bold_value), Paragraph(f"${context['total_to_pay']:,.0f}", right_bold)]], colWidths=[50 * mm, 30 * mm])
        totals_table.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), 0.8, line_color), ('INNERGRID', (0, 0), (-1, -1), 0.5, thin_color), ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fafafa')), ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6), ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]))
        totals_wrapper = Table([['', totals_table]], colWidths=[None, 80 * mm])
        totals_wrapper.setStyle(TableStyle([('ALIGN', (1, 0), (1, 0), 'RIGHT')]))
        story.append(totals_wrapper)
        story.append(Spacer(1, 6 * mm))

        story.append(Paragraph('OBSERVACIONES: ESTA FACTURA DE VENTA SE ASIMILA EN TODOS SUS EFECTOS LEGALES A UNA LETRA DE CAMBIO SEGÚN ART. 774 DEL CÓDIGO DE COMERCIO.', small_style))

        doc.build(story)
        buffer.seek(0)
        return buffer.getvalue()
    except Exception as exc:
        print(f"Error generando PDF de factura: {exc}")
        return None



def generate_remission_pdf(remission):
    """Genera un PDF de remisión reutilizando la misma plantilla mostrada en pantalla."""
    context = build_remission_template_context(remission, for_pdf=True)
    context["is_pdf"] = True

    if WEASYPRINT_AVAILABLE:
        try:
            html_content = render_template("remission.html", **context)
            return HTML(string=html_content, base_url=BASE_DIR).write_pdf()
        except Exception as exc:
            print(f"Error generando PDF de remisión con WeasyPrint: {exc}. Se intentará con ReportLab.")

    if not REPORTLAB_AVAILABLE:
        print("ReportLab no está disponible. No se puede generar PDF.")
        return None

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
            Image,
        )
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=12 * mm,
            leftMargin=12 * mm,
            topMargin=12 * mm,
            bottomMargin=12 * mm,
        )

        line_color = colors.HexColor("#1d1d1d")
        thin_color = colors.HexColor("#cfcfcf")
        header_bg = colors.HexColor("#e9f0ff")

        styles = getSampleStyleSheet()
        company_style = ParagraphStyle(
            "Company", parent=styles["Normal"], fontSize=11, leading=14, alignment=TA_CENTER
        )
        small_style = ParagraphStyle(
            "Small", parent=styles["Normal"], fontSize=9, leading=11
        )
        label_style = ParagraphStyle(
            "Label", parent=small_style, alignment=TA_LEFT, textColor=colors.HexColor("#333333")
        )
        value_style = ParagraphStyle("Value", parent=small_style, alignment=TA_LEFT)
        bold_value = ParagraphStyle("BoldValue", parent=value_style, fontName="Helvetica-Bold")
        pay_style = ParagraphStyle("PayStyle", parent=small_style, fontSize=12, leading=14, fontName="Helvetica-Bold")
        right_bold = ParagraphStyle("RightBold", parent=small_style, alignment=TA_RIGHT, fontName="Helvetica-Bold", fontSize=12, leading=14)

        story = []

        logo_path = os.path.join(BASE_DIR, "static", "img", "ciclovariedadessisi.jpg")
        if os.path.exists(logo_path):
            logo_img = Image(logo_path, width=28 * mm, height=28 * mm)
        else:
            logo_img = Spacer(1, 28 * mm)

        company_lines = [
            "FREDY ALEXANDER GIRALDO GIRALDO",
            "NIT: 1143933804-9",
            "RÉGIMEN: SIMPLIFICADO",
            "CAR. 46 # 49 - 26",
            "CALI - COLOMBIA",
            "TEL: 3152441736",
        ]
        company_table = Table([[Paragraph(line, company_style)] for line in company_lines], colWidths=[80 * mm])
        company_table.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))

        social_box = Table([[Paragraph("Instagram: @ciclovariedades_sisi<br/>Facebook: ciclo variedades sisi", small_style)]], colWidths=[55 * mm])
        social_box.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, thin_color),
            ("INNERPADDING", (0, 0), (-1, -1), 4),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))

        meta_table = Table(
            [
                [Paragraph("Remisión No.", label_style), Paragraph(remission.number, bold_value)],
                [Paragraph("Fecha remisión", label_style), Paragraph(remission.date.strftime("%d/%b/%Y").upper(), value_style)],
            ],
            colWidths=[30 * mm, 25 * mm],
        )
        meta_table.setStyle(
            TableStyle([
                ("BOX", (0, 0), (-1, -1), 0.6, line_color),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, thin_color),
                ("BACKGROUND", (0, 0), (-1, -1), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ])
        )

        right_panel = Table([[social_box], [Spacer(1, 4 * mm)], [meta_table]], colWidths=[60 * mm])
        right_panel.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))

        header_table = Table([[logo_img, company_table, right_panel]], colWidths=[32 * mm, 86 * mm, 60 * mm])
        header_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (1, 0), (1, 0), "CENTER"),
            ("LINEBELOW", (0, 0), (-1, 0), 1, line_color),
        ]))
        story.append(header_table)
        story.append(Spacer(1, 6 * mm))

        dash = "—"
        client_left_data = [
            [Paragraph("SEÑORES:", label_style), Paragraph(remission.customer.name or dash, value_style)],
            [Paragraph("DIRECCIÓN:", label_style), Paragraph(remission.customer.address or dash, value_style)],
            [Paragraph("CIUDAD:", label_style), Paragraph("CALI-VALLE", value_style)],
        ]
        client_right_data = [
            [Paragraph("NIT/CC:", label_style), Paragraph(remission.customer.document_number or dash, value_style)],
            [Paragraph("TELÉFONO:", label_style), Paragraph(remission.customer.phone or dash, value_style)],
            [Paragraph("EMAIL:", label_style), Paragraph(getattr(remission.customer, "email", dash) or dash, value_style)],
            [Paragraph("VENDEDOR:", label_style), Paragraph("ALEXANDER GIRALDO", value_style)],
        ]

        left_table = Table(client_left_data, colWidths=[35 * mm, 60 * mm])
        left_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f6f6f6")),
            ("BOX", (0, 0), (-1, -1), 0.6, thin_color),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, thin_color),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))

        right_table = Table(client_right_data, colWidths=[35 * mm, 60 * mm])
        right_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f6f6f6")),
            ("BOX", (0, 0), (-1, -1), 0.6, thin_color),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, thin_color),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))

        client_section = Table([[left_table, right_table]], colWidths=[95 * mm, 95 * mm])
        client_section.setStyle(TableStyle([
            ("LINEABOVE", (0, 0), (-1, -1), 0.8, line_color),
            ("LINEBELOW", (0, 0), (-1, -1), 0.8, line_color),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(client_section)
        story.append(Spacer(1, 6 * mm))

        items_data = [["ITEM", "CÓDIGO", "DESCRIPCIÓN", "CANTIDAD", "VALOR UNI.", "VALOR TOTAL"]]
        for row in context["remission_items_display"]:
            items_data.append([
                str(row["index"]),
                row["product"].sku,
                row["product"].name,
                f"{row['quantity']}",
                f"${row['unit_price']:,.0f}",
                f"${row['total']:,.0f}",
            ])

        for _ in range(context["items_fillers"]):
            items_data.append(["", "", "", "", "", ""])

        col_widths = [12 * mm, 24 * mm, 72 * mm, 19 * mm, 29 * mm, 32 * mm]
        items_table = Table(items_data, colWidths=col_widths, repeatRows=1)
        items_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), header_bg),
            ("TEXTCOLOR", (0, 0), (-1, 0), line_color),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ("TOPPADDING", (0, 0), (-1, 0), 6),
            ("ALIGN", (0, 1), (0, -1), "RIGHT"),
            ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, line_color),
        ]))
        story.append(items_table)
        story.append(Spacer(1, 6 * mm))

        story.append(Paragraph(f"TOTAL EN LETRAS: {number_to_spanish_words(context['total_to_pay'])} PESOS M/L", small_style))
        story.append(Paragraph(f"MEDIO DE PAGO: {remission.payment_method or 'EFECTIVO'}", pay_style))
        story.append(Spacer(1, 6 * mm))

        totals_table = Table([[Paragraph("TOTAL A PAGAR", bold_value), Paragraph(f"${context['total_to_pay']:,.0f}", right_bold)]], colWidths=[50 * mm, 30 * mm])
        totals_table.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.8, line_color),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, thin_color),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fafafa")),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        totals_wrapper = Table([["", totals_table]], colWidths=[None, 80 * mm])
        totals_wrapper.setStyle(TableStyle([
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ]))
        story.append(totals_wrapper)
        story.append(Spacer(1, 6 * mm))

        story.append(Paragraph("OBSERVACIONES: ESTA REMISIÓN DE VENTA SE ASIMILA EN TODOS SUS EFECTOS LEGALES A UNA LETRA DE CAMBIO SEGÚN ART. 774 DEL CÓDIGO DE COMERCIO.", small_style))

        doc.build(story)
        buffer.seek(0)
        return buffer.getvalue()
    except Exception as exc:
        print(f"Error generando PDF de remisión: {exc}")
        return None
def adjust_stock(db, product_id:int, delta:int, movement_type:str, note:str="", reference_type:str="", reference_id:int=None):
    product = db.get(Product, product_id)
    if not product:
        raise ValueError("Producto no encontrado")
    product.current_stock = int(product.current_stock or 0) + int(delta)
    move = StockMovement(
        product_id=product_id,
        movement_type=movement_type,
        quantity_change=delta,
        note=note,
        reference_type=reference_type,
        reference_id=reference_id
    )
    db.add(move)
    db.add(product)
    db.flush()  # Solo flush, no commit
    return product.current_stock

def recalc_totals_from_items(target, items):
    """Recalcula subtotales, IVA y total a partir de un conjunto de items."""
    subtotal = Decimal("0.00")
    vat_total = Decimal("0.00")
    total = Decimal("0.00")

    for item in items:
        subtotal += D(getattr(item, "total_excl_vat", 0) or 0)
        vat_total += D(getattr(item, "vat_amount", 0) or 0)
        total += D(getattr(item, "total_incl_vat", 0) or 0)

    target.subtotal_excl_vat = money(subtotal)
    target.vat_total = money(vat_total)
    target.total = money(total)
    return target

def delete_product_associations(db, product_id:int):
    """
    Elimina los registros asociados a un producto (compras, facturas y remisiones)
    y recalcula los totales de los documentos afectados.
    """
    summary = {
        "purchase_items_removed": 0,
        "invoice_items_removed": 0,
        "remission_items_removed": 0,
    }

    purchase_items = db.query(PurchaseItem).filter(PurchaseItem.product_id == product_id).all()
    affected_purchases = {item.purchase_id for item in purchase_items if item.purchase_id}
    for item in purchase_items:
        db.delete(item)
    summary["purchase_items_removed"] = len(purchase_items)
    if purchase_items:
        db.flush()
        for purchase_id in affected_purchases:
            purchase = db.get(Purchase, purchase_id)
            if not purchase:
                continue
            remaining_items = db.query(PurchaseItem).filter(PurchaseItem.purchase_id == purchase_id).all()
            recalc_totals_from_items(purchase, remaining_items)
            db.add(purchase)

    invoice_items = db.query(InvoiceItem).filter(InvoiceItem.product_id == product_id).all()
    affected_invoices = {item.invoice_id for item in invoice_items if item.invoice_id}
    for item in invoice_items:
        db.delete(item)
    summary["invoice_items_removed"] = len(invoice_items)
    if invoice_items:
        db.flush()
        for invoice_id in affected_invoices:
            invoice = db.get(Invoice, invoice_id)
            if not invoice:
                continue
            remaining_items = db.query(InvoiceItem).filter(InvoiceItem.invoice_id == invoice_id).all()
            recalc_totals_from_items(invoice, remaining_items)
            db.add(invoice)

    remission_items = db.query(RemissionItem).filter(RemissionItem.product_id == product_id).all()
    affected_remissions = {item.remission_id for item in remission_items if item.remission_id}
    for item in remission_items:
        db.delete(item)
    summary["remission_items_removed"] = len(remission_items)
    if remission_items:
        db.flush()
        for remission_id in affected_remissions:
            remission = db.get(Remission, remission_id)
            if not remission:
                continue
            remaining_items = db.query(RemissionItem).filter(RemissionItem.remission_id == remission_id).all()
            recalc_totals_from_items(remission, remaining_items)
            db.add(remission)

    return summary

def ensure_customer(db, payload):
    name = (payload.get("name") or "").strip()
    if not name:
        raise ValueError("Nombre del cliente es obligatorio")
    doc = (payload.get("document_number") or "").strip()
    phone = (payload.get("phone") or "").strip()
    email = (payload.get("email") or "").strip()
    address = (payload.get("address") or "").strip()

    # Busca uno existente por documento+teléfono, si aplica
    q = db.query(Customer)
    if doc:
        q = q.filter(Customer.document_number == doc)
    elif phone:
        q = q.filter(Customer.phone == phone)
    else:
        q = q.filter(Customer.name == name)
    existing = q.first()
    if existing:
        # Actualiza datos básicos
        existing.name = name or existing.name
        existing.phone = phone or existing.phone
        if hasattr(existing, "email"):
            existing.email = email or existing.email
        existing.address = address or existing.address
        db.add(existing)
        db.flush()
        db.refresh(existing)
        return existing
    kwargs = dict(name=name, document_number=doc, phone=phone, address=address)
    if "email" in Customer.__table__.c:
        kwargs["email"] = email
    c = Customer(**kwargs)
    db.add(c)
    db.flush()
    db.refresh(c)
    return c

# --------------------
# Serializadores simples
# --------------------
def product_to_dict(p: Product):
    # Calcular precio con IVA
    price_with_vat = float(p.price or 0) * (1 + float(p.vat_rate or 0))
    
    # Calcular valor del IVA en pesos
    vat_amount = float(p.price or 0) * float(p.vat_rate or 0)
    
    return {
        "id": p.id,
        "name": p.name,
        "sku": p.sku,
        "price": float(p.price or 0),
        "price_with_vat": price_with_vat,
        "vat_rate": float(p.vat_rate or 0),
        "vat_amount": vat_amount,
        "low_stock_threshold": p.low_stock_threshold,
        "current_stock": p.current_stock,
        "created_at": p.created_at.isoformat() if p.created_at else None
    }

def product_to_dict_with_details(p: Product, db_session):
    """Función avanzada que incluye información del proveedor y unidades vendidas"""
    # Calcular precio con IVA
    price_with_vat = float(p.price or 0) * (1 + float(p.vat_rate or 0))
    
    # Calcular valor del IVA en pesos
    vat_amount = float(p.price or 0) * float(p.vat_rate or 0)
    
    # Obtener el último proveedor que vendió este producto
    last_purchase_item = db_session.query(PurchaseItem).filter(
        PurchaseItem.product_id == p.id
    ).order_by(PurchaseItem.id.desc()).first()
    
    supplier_name = "Sin proveedor"
    if last_purchase_item and last_purchase_item.purchase and last_purchase_item.purchase.supplier:
        supplier_name = last_purchase_item.purchase.supplier.name
    
    # Calcular unidades vendidas (facturas + remisiones)
    invoice_sales = db_session.query(InvoiceItem).filter(
        InvoiceItem.product_id == p.id
    ).with_entities(InvoiceItem.quantity).all()
    
    remission_sales = db_session.query(RemissionItem).filter(
        RemissionItem.product_id == p.id
    ).with_entities(RemissionItem.quantity).all()
    
    total_sold = sum([item.quantity for item in invoice_sales]) + sum([item.quantity for item in remission_sales])
    
    return {
        "id": p.id,
        "name": p.name,
        "sku": p.sku,
        "price": float(p.price or 0),
        "price_with_vat": price_with_vat,
        "vat_rate": float(p.vat_rate or 0),
        "vat_amount": vat_amount,
        "low_stock_threshold": p.low_stock_threshold,
        "current_stock": p.current_stock,
        "supplier_name": supplier_name,
        "total_sold": total_sold,
        "created_at": p.created_at.isoformat() if p.created_at else None
    }

def supplier_to_dict(s: Supplier):
    return {
        "id": s.id, "name": s.name, "phone": s.phone, "email": s.email, "address": s.address
    }

def customer_to_dict(c: Customer):
    return {
        "id": c.id,
        "name": c.name,
        "document_number": c.document_number,
        "phone": c.phone,
        "email": (getattr(c, "email", None) or ""),
        "address": c.address
    }

# --------------
# Rutas de páginas
# --------------
@app.get("/")
def index():
    return render_template("index.html")


@app.get("/invoice/<int:invoice_id>")
def invoice_view(invoice_id:int):
    db = SessionLocal()
    try:
        inv = db.get(Invoice, invoice_id)
        if not inv:
            abort(404)
        # Eager load items, products, and customer
        _ = [(it.product.name, it.quantity) for it in inv.items]
        _ = inv.customer.name  # Eager load customer
        _ = inv.customer.address  # Eager load customer address
        _ = inv.customer.document_number  # Eager load customer document
        _ = inv.customer.phone  # Eager load customer phone
        db.expunge_all()
        context = build_invoice_template_context(inv)
        context["is_pdf"] = False
        context["whatsapp_enabled"] = twilio_configured()
        return render_template("invoice.html", **context)
    finally:
        db.close()

@app.post("/invoice/<int:invoice_id>/send_email")
def invoice_send_email(invoice_id:int):
    if not smtp_configured():
        return jsonify({"error": "No hay configuración SMTP. Define SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD y SMTP_FROM_EMAIL."}), 500

    payload = request.get_json(silent=True) or {}
    to_email = (payload.get("email") or "").strip()
    if not to_email:
        return jsonify({"error": "El cliente no tiene correo electrónico configurado."}), 400

    db = SessionLocal()
    try:
        inv = db.get(Invoice, invoice_id)
        if not inv:
            return jsonify({"error": "Factura no encontrada."}), 404

        _ = [(it.product.name, it.quantity) for it in inv.items]
        _ = inv.customer.name
        _ = inv.customer.address
        _ = inv.customer.document_number
        _ = inv.customer.phone
        db.expunge_all()

        pdf_bytes = generate_invoice_pdf(inv)
        if not pdf_bytes:
            return jsonify({"error": "No fue posible generar el PDF de la factura."}), 500

        context = build_invoice_template_context(inv)
        total_display = f"${context['total_to_pay']:,.0f}"
        subject = f"Factura {inv.number} - {SMTP_FROM_NAME}"
        body = (
            f"Estimado/a {inv.customer.name or 'cliente'},\n\n"
            f"Adjuntamos la factura {inv.number} emitida el {inv.date.strftime('%d/%m/%Y')} "
            f"por un valor de {total_display}.\n\n"
            "Gracias por su compra.\n\n"
            f"{SMTP_FROM_NAME}"
        )

        try:
            send_email_with_pdf(
                to_email=to_email,
                subject=subject,
                body=body,
                pdf_bytes=pdf_bytes,
                filename=f"factura_{inv.number}.pdf",
            )
        except Exception as exc:
            return jsonify({"error": f"No se pudo enviar el correo: {exc}"}), 500

        return jsonify({"success": True})
    finally:
        db.close()

@app.post("/invoice/<int:invoice_id>/send_whatsapp")
def invoice_send_whatsapp(invoice_id:int):
    if not twilio_configured():
        return jsonify({"error": "No hay configuración de Twilio para WhatsApp."}), 500

    payload = request.get_json(silent=True) or {}
    requested_phone = (payload.get("phone") or "").strip()

    db = SessionLocal()
    try:
        inv = db.get(Invoice, invoice_id)
        if not inv:
            return jsonify({"error": "Factura no encontrada."}), 404

        _ = [(it.product.name, it.quantity) for it in inv.items]
        _ = inv.customer.name
        _ = inv.customer.address
        _ = inv.customer.document_number
        _ = inv.customer.phone
        db.expunge_all()

        phone_candidate = requested_phone or (inv.customer.phone or "")
        normalized_phone = normalize_phone_number(phone_candidate)
        if not normalized_phone:
            return jsonify({"error": "El cliente no tiene un número de teléfono válido."}), 400

        pdf_url = url_for("invoice_pdf", invoice_id=invoice_id, _external=True)
        context = build_invoice_template_context(inv)
        total_display = f"${context['total_to_pay']:,.0f}"
        body = (
            f"Hola {inv.customer.name or 'cliente'}!\n\n"
            f"Te envío la factura {inv.number} por un valor de {total_display}.\n"
            f"Fecha: {inv.date.strftime('%d/%m/%Y')}\n"
            f"Método de pago: {inv.payment_method or 'EFECTIVO'}\n\n"
            f"Puedes descargar el PDF aquí: {pdf_url}\n\n"
            f"{SMTP_FROM_NAME}"
        )

        try:
            send_whatsapp_message(
                to_phone=normalized_phone,
                body=body,
                media_url=pdf_url if TWILIO_SEND_MEDIA else None,
            )
        except Exception as exc:
            return jsonify({"error": f"No se pudo enviar el mensaje: {exc}"}), 500

        return jsonify({"success": True})
    finally:
        db.close()

@app.get("/remission/<int:remission_id>")
def remission_view(remission_id:int):
    db = SessionLocal()
    try:
        rem = db.get(Remission, remission_id)
        if not rem:
            abort(404)
        # Eager load items, products, and customer
        _ = [(it.product.name, it.quantity) for it in rem.items]
        _ = rem.customer.name  # Eager load customer
        _ = rem.customer.address  # Eager load customer address
        _ = rem.customer.document_number  # Eager load customer document
        _ = rem.customer.phone  # Eager load customer phone
        db.expunge_all()
        context = build_remission_template_context(rem)
        context["is_pdf"] = False
        context["whatsapp_enabled"] = twilio_configured()
        return render_template("remission.html", **context)
    finally:
        db.close()

@app.post("/remission/<int:remission_id>/send_email")
def remission_send_email(remission_id:int):
    if not smtp_configured():
        return jsonify({"error": "No hay configuración SMTP. Define SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD y SMTP_FROM_EMAIL."}), 500

    payload = request.get_json(silent=True) or {}
    to_email = (payload.get("email") or "").strip()
    if not to_email:
        return jsonify({"error": "El cliente no tiene correo electrónico configurado."}), 400

    db = SessionLocal()
    try:
        rem = db.get(Remission, remission_id)
        if not rem:
            return jsonify({"error": "Remisión no encontrada."}), 404

        _ = [(it.product.name, it.quantity) for it in rem.items]
        _ = rem.customer.name
        _ = rem.customer.address
        _ = rem.customer.document_number
        _ = rem.customer.phone
        db.expunge_all()

        pdf_bytes = generate_remission_pdf(rem)
        if not pdf_bytes:
            return jsonify({"error": "No fue posible generar el PDF de la remisión."}), 500

        context = build_remission_template_context(rem)
        total_display = f"${context['total_to_pay']:,.0f}"
        subject = f"Remisión {rem.number} - {SMTP_FROM_NAME}"
        body = (
            f"Estimado/a {rem.customer.name or 'cliente'},\n\n"
            f"Adjuntamos la remisión {rem.number} emitida el {rem.date.strftime('%d/%m/%Y')} "
            f"por un valor de {total_display}.\n\n"
            "Gracias por su confianza.\n\n"
            f"{SMTP_FROM_NAME}"
        )

        try:
            send_email_with_pdf(
                to_email=to_email,
                subject=subject,
                body=body,
                pdf_bytes=pdf_bytes,
                filename=f"remision_{rem.number}.pdf",
            )
        except Exception as exc:
            return jsonify({"error": f"No se pudo enviar el correo: {exc}"}), 500

        return jsonify({"success": True})
    finally:
        db.close()

@app.post("/remission/<int:remission_id>/send_whatsapp")
def remission_send_whatsapp(remission_id:int):
    if not twilio_configured():
        return jsonify({"error": "No hay configuración de Twilio para WhatsApp."}), 500

    payload = request.get_json(silent=True) or {}
    requested_phone = (payload.get("phone") or "").strip()

    db = SessionLocal()
    try:
        rem = db.get(Remission, remission_id)
        if not rem:
            return jsonify({"error": "Remisión no encontrada."}), 404

        _ = [(it.product.name, it.quantity) for it in rem.items]
        _ = rem.customer.name
        _ = rem.customer.address
        _ = rem.customer.document_number
        _ = rem.customer.phone
        db.expunge_all()

        phone_candidate = requested_phone or (rem.customer.phone or "")
        normalized_phone = normalize_phone_number(phone_candidate)
        if not normalized_phone:
            return jsonify({"error": "El cliente no tiene un número de teléfono válido."}), 400

        pdf_url = url_for("remission_pdf", remission_id=remission_id, _external=True)
        context = build_remission_template_context(rem)
        total_display = f"${context['total_to_pay']:,.0f}"
        body = (
            f"Hola {rem.customer.name or 'cliente'}!\n\n"
            f"Te envío la remisión {rem.number} por un valor de {total_display}.\n"
            f"Fecha: {rem.date.strftime('%d/%m/%Y')}\n"
            f"Método de pago: {rem.payment_method or 'EFECTIVO'}\n\n"
            f"Puedes descargar el PDF aquí: {pdf_url}\n\n"
            f"{SMTP_FROM_NAME}"
        )

        try:
            send_whatsapp_message(
                to_phone=normalized_phone,
                body=body,
                media_url=pdf_url if TWILIO_SEND_MEDIA else None,
            )
        except Exception as exc:
            return jsonify({"error": f"No se pudo enviar el mensaje: {exc}"}), 500

        return jsonify({"success": True})
    finally:
        db.close()

@app.get("/invoice/<int:invoice_id>/pdf")
def invoice_pdf(invoice_id:int):
    """Genera y descarga PDF de la factura"""
    db = SessionLocal()
    try:
        inv = db.get(Invoice, invoice_id)
        if not inv:
            abort(404)
        
        # Eager load items, products, and customer
        _ = [(it.product.name, it.quantity) for it in inv.items]
        _ = inv.customer.name
        _ = inv.customer.address
        _ = inv.customer.document_number
        _ = inv.customer.phone
        db.expunge_all()
        
        # Generar PDF
        pdf_bytes = generate_invoice_pdf(inv)
        
        if not pdf_bytes:
            abort(500)
        
        # Crear respuesta con PDF
        response = make_response(pdf_bytes)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=factura_{inv.number}.pdf'
        
        return response
    finally:
        db.close()

@app.get("/remission/<int:remission_id>/pdf")
def remission_pdf(remission_id:int):
    """Genera y descarga PDF de la remisión"""
    db = SessionLocal()
    try:
        rem = db.get(Remission, remission_id)
        if not rem:
            abort(404)
        
        # Eager load items, products, and customer
        _ = [(it.product.name, it.quantity) for it in rem.items]
        _ = rem.customer.name
        _ = rem.customer.address
        _ = rem.customer.document_number
        _ = rem.customer.phone
        db.expunge_all()
        
        # Generar PDF
        pdf_bytes = generate_remission_pdf(rem)
        
        if not pdf_bytes:
            abort(500)
        
        # Crear respuesta con PDF
        response = make_response(pdf_bytes)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=remision_{rem.number}.pdf'
        
        return response
    finally:
        db.close()

@app.get("/history/invoices")
def invoices_history_view():
    """Página de historial de facturas"""
    return render_template("invoices_history.html")

@app.get("/history/remissions")
def remissions_history_view():
    """Página de historial de remisiones"""
    return render_template("remissions_history.html")

@app.get("/history/purchases")
def purchases_history_view():
    """Página de historial de compras"""
    return render_template("purchases_history.html")

# --------------
# API JSON
# --------------
@app.get("/api/products")
def api_products_list():
    db = SessionLocal()
    try:
        items = db.query(Product).order_by(Product.name.asc()).all()
        return jsonify([product_to_dict_with_details(p, db) for p in items])
    finally:
        db.close()

@app.post("/api/products")
def api_products_create():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    sku = (data.get("sku") or "").strip()
    price = money(data.get("price") or 0)
    vat_rate = money(data.get("vat_rate") or Decimal("0.19"))
    low_stock_threshold = int(data.get("low_stock_threshold") or 5)

    if not name or not sku:
        return jsonify({"error":"name y sku son obligatorios"}), 400

    db = SessionLocal()
    try:
        p = Product(name=name, sku=sku, price=price, vat_rate=vat_rate, low_stock_threshold=low_stock_threshold, current_stock=0)
        db.add(p)
        db.commit()
        return jsonify(product_to_dict(p)), 201
    except IntegrityError:
        db.rollback()
        return jsonify({"error":"SKU ya existe"}), 400
    finally:
        db.close()

@app.delete("/api/products/<int:product_id>")
def api_products_delete(product_id):
    db = SessionLocal()
    try:
        product = db.get(Product, product_id)
        if not product:
            return jsonify({"error": "Producto no encontrado"}), 404
        
        # Elimina registros asociados y recalcula totales
        association_summary = delete_product_associations(db, product.id)

        # Eliminar movimientos de stock asociados al producto
        stock_movements_deleted = db.query(StockMovement).filter(StockMovement.product_id == product_id).delete()
        
        # Eliminar el producto
        db.delete(product)
        db.commit()
        return jsonify({
            "message": "Producto eliminado junto con registros relacionados.",
            "removed": association_summary,
            "stock_movements_removed": stock_movements_deleted or 0
        }), 200
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@app.post("/api/inventory/adjust")
def api_inventory_adjust():
    data = request.get_json(force=True)
    product_id = int(data.get("product_id"))
    quantity = int(data.get("quantity"))
    reason = (data.get("reason") or "ajuste").strip()

    db = SessionLocal()
    try:
        new_stock = adjust_stock(db, product_id, quantity, "adjustment", reason, "adjustment", None)
        db.commit()
        return jsonify({"ok": True, "new_stock": new_stock})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        db.close()

@app.get("/api/suppliers")
def api_suppliers_list():
    db = SessionLocal()
    try:
        items = db.query(Supplier).order_by(Supplier.name.asc()).all()
        return jsonify([supplier_to_dict(s) for s in items])
    finally:
        db.close()

@app.post("/api/suppliers")
def api_suppliers_create():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error":"name es obligatorio"}), 400
    db = SessionLocal()
    s = Supplier(
        name=name,
        phone=(data.get("phone") or "").strip(),
        email=(data.get("email") or "").strip(),
        address=(data.get("address") or "").strip()
    )
    db.add(s)
    db.commit()
    out = supplier_to_dict(s)
    db.close()
    return jsonify(out), 201

@app.post("/api/purchases")
def api_purchases_create():
    """
    Crea una compra e ingresa mercancía al inventario.
    JSON esperado:
    {
        "supplier": {"name": "...", "phone": "...", "email": "...", "address": "...", "id": optional},
        "items": [{"product_id": 1, "quantity": 5, "unit_cost": 10000, "vat_rate": 0.19}, ...],
        "notes": "opcional"
    }
    """
    payload = request.get_json(force=True)
    db = SessionLocal()
    try:
        # proveedor
        sup_payload = payload.get("supplier") or {}
        supplier_id = sup_payload.get("id")
        if supplier_id:
            supplier = db.get(Supplier, int(supplier_id))
            if not supplier:
                return jsonify({"error":"Proveedor no encontrado"}), 400
        else:
            name = (sup_payload.get("name") or "").strip()
            if not name:
                return jsonify({"error":"Nombre de proveedor es obligatorio"}), 400
            supplier = Supplier(
                name=name,
                phone=(sup_payload.get("phone") or "").strip(),
                email=(sup_payload.get("email") or "").strip(),
                address=(sup_payload.get("address") or "").strip()
            )
            db.add(supplier)
            db.flush()

        code = f"COMP-{datetime.utcnow().strftime('%Y%m%d')}-{next_sequence('purchase', start_at=1001)}"

        purchase = Purchase(code=code, supplier_id=supplier.id, notes=(payload.get("notes") or "").strip())
        db.add(purchase)
        db.flush()

        subtotal = Decimal("0.00")
        vat_total = Decimal("0.00")
        total = Decimal("0.00")

        items = payload.get("items") or []
        if not items:
            return jsonify({"error":"Debes incluir items en la compra"}), 400

        for it in items:
            product_id = int(it.get("product_id"))
            qty = int(it.get("quantity"))
            unit_cost = money(it.get("unit_cost"))
            vat_rate = D(it.get("vat_rate") or Decimal("0.19"))
            if qty <= 0:
                return jsonify({"error":"Cantidad debe ser > 0"}), 400
            product = db.get(Product, product_id)
            if not product:
                return jsonify({"error":f"Producto {product_id} no existe"}), 400

            total_excl = money(unit_cost * qty)
            vat_amount = money(total_excl * vat_rate)
            total_incl = money(total_excl + vat_amount)

            pi = PurchaseItem(
                purchase_id=purchase.id, product_id=product_id,
                quantity=qty, unit_cost=unit_cost, vat_rate=vat_rate,
                total_excl_vat=total_excl, vat_amount=vat_amount, total_incl_vat=total_incl
            )
            db.add(pi)

            subtotal += total_excl
            vat_total += vat_amount
            total += total_incl

            # Ingreso a inventario
            # Nota: un movimiento por item mantiene el historial
            db.flush()
            adjust_stock(db, product.id, qty, "purchase", f"Compra {code}", "purchase", purchase.id)

        purchase.subtotal_excl_vat = money(subtotal)
        purchase.vat_total = money(vat_total)
        purchase.total = money(total)

        db.commit()
        return jsonify({
            "id": purchase.id,
            "code": purchase.code,
            "supplier": supplier_to_dict(supplier),
            "subtotal_excl_vat": float(purchase.subtotal_excl_vat),
            "vat_total": float(purchase.vat_total),
            "total": float(purchase.total)
        }), 201
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        db.close()

@app.get("/api/customers")
def api_customers_list():
    db = SessionLocal()
    try:
        items = db.query(Customer).order_by(Customer.created_at.desc()).limit(50).all()
        return jsonify([customer_to_dict(c) for c in items])
    finally:
        db.close()

@app.post("/api/remissions")
def api_remissions_create():
    """
    Crea una remisión (salida de inventario) con datos del cliente.
    JSON esperado:
    {
        "customer": {"name": "...", "document_number": "...", "phone": "...", "email": "...", "address": "..."},
        "items": [{"product_id":1, "quantity":2, "unit_price": 50000, "vat_rate": 0.19}, ...],
        "maintenance_days": 180
    }
    """
    payload = request.get_json(force=True)
    db = SessionLocal()
    try:
        # cliente
        customer_payload = payload.get("customer") or {}
        customer = ensure_customer(db, customer_payload)
        customer_id = customer.id  # Guardar el ID antes de cualquier otra operación
        # Refrescar el objeto para asegurar que esté vinculado antes de serializar
        db.refresh(customer)
        customer_data = customer_to_dict(customer)  # Serializar inmediatamente

        remission_seq = next_sequence("remission", start_at=1)
        number = f"REM-{datetime.utcnow().strftime('%Y%m%d')}-{remission_seq:03d}"
        payment_method = (payload.get("payment_method") or "EFECTIVO").strip()
        remission = Remission(number=number, customer_id=customer_id, payment_method=payment_method)
        db.add(remission)
        db.flush()

        subtotal = Decimal("0.00")
        vat_total = Decimal("0.00")
        total = Decimal("0.00")

        for it in (payload.get("items") or []):
            product_id = int(it.get("product_id"))
            qty = int(it.get("quantity"))
            unit_price = money(it.get("unit_price"))
            vat_rate = Decimal("0.00")
            if qty <= 0:
                return jsonify({"error":"Cantidad debe ser > 0"}), 400
            product = db.get(Product, product_id)
            if not product:
                return jsonify({"error":f"Producto {product_id} no existe"}), 400
            if product.current_stock < qty:
                return jsonify({"error":f"Stock insuficiente para {product.name}"}), 400

            total_excl = money(unit_price * qty)
            vat_amount = money(0)
            total_incl = total_excl

            ri = RemissionItem(
                remission_id=remission.id, product_id=product_id,
                quantity=qty, unit_price=unit_price, vat_rate=vat_rate,
                total_excl_vat=total_excl, vat_amount=vat_amount, total_incl_vat=total_incl
            )
            db.add(ri)

            subtotal += total_excl
            vat_total += vat_amount
            total += total_incl

            adjust_stock(db, product.id, -qty, "remission", f"Remisión {number}", "remission", remission.id)

        remission.subtotal_excl_vat = money(subtotal)
        remission.vat_total = money(vat_total)
        remission.total = money(total)

        # Recordatorio de mantenimiento
        days = int(payload.get("maintenance_days") or 0)
        if days > 0:
            due = (datetime.utcnow() + timedelta(days=days)).date()
            rem = MaintenanceReminder(
                customer_id=customer_id,
                due_date=due,
                notes=f"Recordatorio por remisión {number}",
                reference_type="remission",
                reference_id=remission.id
            )
            db.add(rem)

        db.commit()
        return jsonify({
            "id": remission.id,
            "number": remission.number,
            "customer": customer_data,
            "subtotal_excl_vat": float(remission.subtotal_excl_vat),
            "vat_total": float(remission.vat_total),
            "total": float(remission.total)
        }), 201
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        db.close()

@app.post("/api/invoices")
def api_invoices_create():
    """
    Genera una factura (salida de inventario) con numeración automática.
    JSON esperado:
    {
        "customer": {"name": "...", "document_number": "...", "phone": "...", "email": "...", "address": "..."},
        "items": [{"product_id":1, "quantity":2, "unit_price": 50000, "vat_rate": 0.19}, ...],
        "maintenance_days": 180
    }
    """
    payload = request.get_json(force=True)
    db = SessionLocal()
    try:
        customer_payload = payload.get("customer") or {}
        customer = ensure_customer(db, customer_payload)
        customer_id = customer.id  # Guardar el ID antes de cualquier otra operación
        # Refrescar el objeto para asegurar que esté vinculado antes de serializar
        db.refresh(customer)
        customer_data = customer_to_dict(customer)  # Serializar inmediatamente

        invoice_seq = next_sequence("invoice", start_at=1)
        number = f"FAC-{invoice_seq:03d}"
        payment_method = (payload.get("payment_method") or "EFECTIVO").strip()
        invoice = Invoice(number=number, customer_id=customer_id, payment_method=payment_method)
        db.add(invoice)
        db.flush()

        subtotal = Decimal("0.00")
        vat_total = Decimal("0.00")
        total = Decimal("0.00")

        for it in (payload.get("items") or []):
            product_id = int(it.get("product_id"))
            qty = int(it.get("quantity"))
            unit_price = money(it.get("unit_price"))
            vat_rate = Decimal("0.00")
            if qty <= 0:
                return jsonify({"error":"Cantidad debe ser > 0"}), 400
            product = db.get(Product, product_id)
            if not product:
                return jsonify({"error":f"Producto {product_id} no existe"}), 400
            if product.current_stock < qty:
                return jsonify({"error":f"Stock insuficiente para {product.name}"}), 400

            total_excl = money(unit_price * qty)
            vat_amount = money(0)
            total_incl = total_excl

            ii = InvoiceItem(
                invoice_id=invoice.id, product_id=product_id,
                quantity=qty, unit_price=unit_price, vat_rate=vat_rate,
                total_excl_vat=total_excl, vat_amount=vat_amount, total_incl_vat=total_incl
            )
            db.add(ii)

            subtotal += total_excl
            vat_total += vat_amount
            total += total_incl

            adjust_stock(db, product.id, -qty, "invoice", f"Factura {number}", "invoice", invoice.id)

        invoice.subtotal_excl_vat = money(subtotal)
        invoice.vat_total = money(vat_total)
        invoice.total = money(total)

        # Recordatorio de mantenimiento si aplica
        days = int(payload.get("maintenance_days") or 0)
        if days > 0:
            due = (datetime.utcnow() + timedelta(days=days)).date()
            rem = MaintenanceReminder(
                customer_id=customer_id,
                due_date=due,
                notes=f"Recordatorio por factura {number}",
                reference_type="invoice",
                reference_id=invoice.id
            )
            db.add(rem)

        db.commit()
        return jsonify({
            "id": invoice.id,
            "number": invoice.number,
            "customer": customer_data,
            "subtotal_excl_vat": float(invoice.subtotal_excl_vat),
            "vat_total": float(invoice.vat_total),
            "total": float(invoice.total)
        }), 201
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        db.close()

@app.get("/api/alerts/low-stock")
def api_alerts_low_stock():
    db = SessionLocal()
    try:
        items = db.query(Product).all()
        low = [product_to_dict(p) for p in items if (p.current_stock or 0) <= (p.low_stock_threshold or 0)]
        return jsonify(low)
    finally:
        db.close()

@app.get("/api/alerts/maintenance")
def api_alerts_maintenance():
    # próximas 2 semanas
    today = date.today()
    horizon = today + timedelta(days=14)
    db = SessionLocal()
    try:
        items = db.query(MaintenanceReminder).filter(MaintenanceReminder.due_date <= horizon).order_by(MaintenanceReminder.due_date.asc()).all()
        out = []
        for m in items:
            # Serializar el cliente mientras la sesión está activa
            customer_data = customer_to_dict(m.customer)
            out.append({
                "id": m.id,
                "customer": customer_data,
                "due_date": m.due_date.isoformat(),
                "notes": m.notes
            })
        return jsonify(out)
    finally:
        db.close()

def _complete_maintenance(reminder_id: int):
    db = SessionLocal()
    try:
        reminder = db.get(MaintenanceReminder, reminder_id)
        if not reminder:
            return jsonify({"error": "Recordatorio no encontrado"}), 404
        app.logger.info("Marcando mantenimiento completado: %s", reminder_id)
        db.delete(reminder)
        db.commit()
        return jsonify({"status": "ok"})
    except Exception as exc:
        db.rollback()
        return jsonify({"error": str(exc)}), 400
    finally:
        db.close()

@app.delete("/api/alerts/maintenance/<int:reminder_id>")
def api_alerts_maintenance_complete(reminder_id: int):
    """Marca un recordatorio de mantenimiento como atendido (elimínalo de la lista de alertas)."""
    return _complete_maintenance(reminder_id)

@app.route("/api/alerts/maintenance/complete", methods=["POST", "GET"])
def api_alerts_maintenance_complete_json():
    """Permite completar un mantenimiento usando JSON o querystring."""
    if request.method == "GET":
        reminder_id = request.args.get("id")
    else:
        payload = request.get_json(silent=True) or {}
        reminder_id = payload.get("id")
    if reminder_id is None:
        return jsonify({"error": "Falta el identificador del recordatorio"}), 400
    try:
        reminder_id = int(reminder_id)
    except (TypeError, ValueError):
        return jsonify({"error": "Identificador inválido"}), 400
    return _complete_maintenance(reminder_id)

@app.post("/api/alerts/maintenance/<int:reminder_id>/complete")
def api_alerts_maintenance_complete_post(reminder_id: int):
    """Compatibilidad para clientes que no permitan DELETE desde el frontend."""
    return _complete_maintenance(reminder_id)

@app.get("/api/invoices/history")
def api_invoices_history():
    """Obtiene el historial de facturas"""
    db = SessionLocal()
    try:
        invoices = db.query(Invoice).order_by(Invoice.date.desc()).limit(50).all()
        out = []
        for inv in invoices:
            # Eager load customer
            customer_data = customer_to_dict(inv.customer)
            out.append({
                "id": inv.id,
                "number": inv.number,
                "date": inv.date.isoformat(),
                "customer": customer_data,
                "subtotal_excl_vat": float(inv.subtotal_excl_vat),
                "vat_total": float(inv.vat_total),
                "total": float(inv.total)
            })
        return jsonify(out)
    finally:
        db.close()

@app.get("/api/remissions/history")
def api_remissions_history():
    """Obtiene el historial de remisiones"""
    db = SessionLocal()
    try:
        remissions = db.query(Remission).order_by(Remission.date.desc()).limit(50).all()
        out = []
        for rem in remissions:
            # Eager load customer
            customer_data = customer_to_dict(rem.customer)
            out.append({
                "id": rem.id,
                "number": rem.number,
                "date": rem.date.isoformat(),
                "customer": customer_data,
                "subtotal_excl_vat": float(rem.subtotal_excl_vat),
                "vat_total": float(rem.vat_total),
                "total": float(rem.total)
            })
        return jsonify(out)
    finally:
        db.close()

@app.get("/api/products/search")
def api_products_search():
    """Busca productos por nombre o código"""
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify([])
    
    db = SessionLocal()
    try:
        # Buscar por nombre o SKU
        products = db.query(Product).filter(
            (Product.name.ilike(f'%{query}%')) | 
            (Product.sku.ilike(f'%{query}%'))
        ).limit(20).all()
        
        return jsonify([product_to_dict(p) for p in products])
    finally:
        db.close()

@app.get("/api/purchases/history")
def api_purchases_history():
    """Obtiene el historial de compras"""
    db = SessionLocal()
    try:
        purchases = db.query(Purchase).order_by(Purchase.date.desc()).limit(50).all()
        out = []
        for purchase in purchases:
            # Eager load supplier
            supplier_data = supplier_to_dict(purchase.supplier)
            out.append({
                "id": purchase.id,
                "code": purchase.code,
                "date": purchase.date.isoformat(),
                "supplier": supplier_data,
                "subtotal_excl_vat": float(purchase.subtotal_excl_vat),
                "vat_total": float(purchase.vat_total),
                "total": float(purchase.total),
                "notes": purchase.notes
            })
        return jsonify(out)
    finally:
        db.close()

@app.get("/api/purchases/<int:purchase_id>")
def api_purchase_details(purchase_id):
    """Obtiene los detalles de una compra específica"""
    db = SessionLocal()
    try:
        purchase = db.get(Purchase, purchase_id)
        if not purchase:
            return jsonify({"error": "Compra no encontrada"}), 404
        
        # Eager load supplier and items with products
        supplier_data = supplier_to_dict(purchase.supplier)
        items_data = []
        for item in purchase.items:
            items_data.append({
                "id": item.id,
                "quantity": item.quantity,
                "unit_cost": float(item.unit_cost),
                "vat_rate": float(item.vat_rate),
                "total_excl_vat": float(item.total_excl_vat),
                "vat_amount": float(item.vat_amount),
                "total_incl_vat": float(item.total_incl_vat),
                "product": product_to_dict(item.product)
            })
        
        return jsonify({
            "id": purchase.id,
            "code": purchase.code,
            "date": purchase.date.isoformat(),
            "supplier": supplier_data,
            "subtotal_excl_vat": float(purchase.subtotal_excl_vat),
            "vat_total": float(purchase.vat_total),
            "total": float(purchase.total),
            "notes": purchase.notes,
            "items": items_data
        })
    finally:
        db.close()

# -------
# Archivos estáticos (para dev en algunos entornos)
# -------
@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory(os.path.join(BASE_DIR, 'static'), path)

# --------------
# Plantillas Jinja
# --------------

# No es necesario código adicional aquí; Flask cargará automáticamente
# templates/ y static/

if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", os.getenv("DEBUG", "false")).lower() == "true"
    host = os.getenv("FLASK_RUN_HOST", os.getenv("HOST", "127.0.0.1"))
    port = int(os.getenv("PORT", os.getenv("FLASK_RUN_PORT", 5000)))
    app.run(host=host, port=port, debug=debug_mode)
