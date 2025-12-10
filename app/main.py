from fastapi import FastAPI, Request, UploadFile, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from typing import Optional
from fastapi.staticfiles import StaticFiles
import os
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from sqlalchemy.orm import Session 
from datetime import datetime

from app.pdf_generator import generate_invoice_pdf
from app import models, schemas, supabase_client, crud
from app.database import get_db, engine

@asynccontextmanager
async def lifespan(app: FastAPI):
    # La pornire
    supabase_client.init_supabase_client()
    yield
    # La oprire (nu avem nevoie de nimic aici deocamdată)

# Creează tabelele în baza de date la pornire
models.Base.metadata.create_all(bind=engine)

# --- JSON Encoder personalizat pentru a gestiona tipul Decimal ---
from json import JSONEncoder

class CustomJSONEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj) # Convertim în float doar pentru afișare JSON, calculele rămân precise
        return JSONEncoder.default(self, obj)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(os.path.dirname(BASE_DIR), "static")
INVOICES_DIR = os.path.join(STATIC_DIR, "invoices")
TEMPLATE_DIR = os.path.join(os.path.dirname(BASE_DIR), "templates")

# Asigură-te că directorul static există
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(INVOICES_DIR, exist_ok=True)

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Suprascrie encoder-ul JSON implicit al FastAPI
app.json_encoder = CustomJSONEncoder
templates = Jinja2Templates(directory=TEMPLATE_DIR)

@app.middleware("http")
async def refresh_token_middleware(request: Request, call_next):
    """
    Acest middleware verifică fiecare cerere. Dacă token-ul de acces este expirat,
    încearcă să-l reîmprospăteze și setează noile cookie-uri pe răspuns.
    """
    # O variabilă pentru a semnala dacă trebuie să ștergem cookie-urile
    clear_cookies = False
    try:
        # get_current_user poate returna User, None, sau poate ridica SessionExpiredError
        request.state.user = await supabase_client.get_current_user(request)
    except supabase_client.SessionExpiredError:
        # Sesiunea a expirat complet (access și refresh token invalide).
        # Setăm user-ul ca None și marcăm cookie-urile pentru ștergere.
        request.state.user = None
        clear_cookies = True

    response = await call_next(request)

    if clear_cookies:
        # Ștergem cookie-urile dacă sesiunea a expirat
        response.delete_cookie("access_token")
        response.delete_cookie("refresh_token")
    elif hasattr(request.state, 'user') and request.state.user and request.state.user.new_session:
        # Dacă sesiunea a fost reîmprospătată, setăm noile cookie-uri
        set_auth_cookies(response, request.state.user.new_session.access_token, request.state.user.new_session.refresh_token)
    return response

# Obține cheile Supabase din variabilele de mediu
SUPABASE_URL_ENV = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY_ENV = os.getenv("SUPABASE_ANON_KEY")

# Determină mediul de rulare. Implicit este "development".
APP_ENV = os.getenv("APP_ENV", "development")

def set_auth_cookies(response: Response, access_token: str, refresh_token: str):
    """Setează cookie-urile de autentificare pe un răspuns."""
    is_production = (APP_ENV == "production")
    
    # În producție, cookie-urile trebuie să fie `secure` și `samesite='strict'`
    # În dezvoltare, `secure` trebuie să fie `False` pentru a funcționa pe HTTP.
    samesite_policy = "strict" if is_production else "lax"

    response.set_cookie(
        key="access_token", value=access_token, httponly=True, samesite=samesite_policy, secure=is_production
    )
    response.set_cookie(
        key="refresh_token", value=refresh_token, httponly=True, samesite=samesite_policy, secure=is_production
    )
def get_current_user(request: Request) -> supabase_client.User:
    """Dependență care returnează user-ul sau ridică o excepție dacă nu este logat."""
    if not request.state.user:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return request.state.user

def get_current_user_optional(request: Request) -> Optional[supabase_client.User]:
    """Dependență care returnează user-ul dacă este logat, sau None."""
    return request.state.user

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, user: Optional[supabase_client.User] = Depends(get_current_user_optional)):
    return templates.TemplateResponse("index.html", {"request": request, "user": user})

@app.get("/preise", response_class=HTMLResponse)
async def preise(request: Request, user: Optional[supabase_client.User] = Depends(get_current_user_optional)):
    return templates.TemplateResponse("preise.html", {"request": request, "user": user})

@app.get("/warum", response_class=HTMLResponse)
async def warum_page(request: Request, user: Optional[supabase_client.User] = Depends(get_current_user_optional)):
    return templates.TemplateResponse("warum.html", {"request": request, "user": user})

@app.get("/register", response_class=HTMLResponse)
async def register_form(request: Request):
    context = {"request": request, "SUPABASE_URL": SUPABASE_URL_ENV, "SUPABASE_KEY": SUPABASE_ANON_KEY_ENV}
    return templates.TemplateResponse("register.html", context)

@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    context = {"request": request, "SUPABASE_URL": SUPABASE_URL_ENV, "SUPABASE_KEY": SUPABASE_ANON_KEY_ENV}
    return templates.TemplateResponse("login.html", context)

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/")
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return response

@app.post("/auth/callback")
async def set_auth_cookie(request: Request):
    data = await request.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    response = HTMLResponse(content="Cookie set")
    if access_token and refresh_token:
        set_auth_cookies(response, access_token, refresh_token)
    return response

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db), user: supabase_client.User = Depends(get_current_user)):
    invoices = crud.get_invoices_by_owner(db, owner_id=user.id)
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user, "invoices": invoices})

@app.get("/rechnungen", response_class=HTMLResponse)
async def get_invoices_page(request: Request, db: Session = Depends(get_db), user: supabase_client.User = Depends(get_current_user)):
    invoices = crud.get_invoices_by_owner(db, owner_id=user.id)
    return templates.TemplateResponse("rechnungen.html", {"request": request, "user": user, "invoices": invoices})

@app.get("/profil", response_class=HTMLResponse)
async def get_profile_page(request: Request, db: Session = Depends(get_db), user: supabase_client.User = Depends(get_current_user)):
    profile = crud.get_company_profile(db, user_id=user.id)
    return templates.TemplateResponse("profil.html", {"request": request, "user": user, "profile": profile})

@app.post("/profil", response_class=HTMLResponse)
async def update_profile_page(
    request: Request,
    db: Session = Depends(get_db),
    user: supabase_client.User = Depends(get_current_user),
    sender_name: str = Form(...),
    sender_address: str = Form(...),
    sender_zip: str = Form(...),
    sender_city: str = Form(...),
    sender_tax_id: str = Form(...),
    sender_vat_id: Optional[str] = Form(None),
    iban: Optional[str] = Form(None),
    is_small_business: Optional[bool] = Form(False),
    register_court: Optional[str] = Form(None),
    register_number: Optional[str] = Form(None),
    managing_director: Optional[str] = Form(None),
    logo: UploadFile = None
):
    logo_path = None
    if logo and logo.filename:
        # Salvează logo-ul permanent, legat de ID-ul utilizatorului
        file_extension = os.path.splitext(logo.filename)[1]
        logo_filename = f"logo_{user.id}{file_extension}"
        logo_path = os.path.join(STATIC_DIR, logo_filename)
        with open(logo_path, "wb") as f:
            f.write(await logo.read())

    profile_data = schemas.CompanyProfileCreate(
        sender_name=sender_name, 
        sender_address=sender_address, 
        sender_zip=sender_zip, 
        sender_city=sender_city, 
        sender_tax_id=sender_tax_id, 
        sender_vat_id=sender_vat_id,
        iban=iban, 
        is_small_business=is_small_business,
        register_court=register_court, register_number=register_number, managing_director=managing_director
    )
    crud.create_or_update_company_profile(db, profile=profile_data, user_id=user.id, logo_filename=logo_filename if logo_path else None)
    
    # Re-încarcă pagina cu un mesaj de succes
    profile = crud.get_company_profile(db, user_id=user.id)
    return templates.TemplateResponse("profil.html", {"request": request, "user": user, "profile": profile, "success": "Profil erfolgreich gespeichert!"})

@app.get("/kunden", response_class=HTMLResponse)
async def get_clients_page(request: Request, db: Session = Depends(get_db), user: supabase_client.User = Depends(get_current_user)):
    db_clients = crud.get_clients_by_owner(db, owner_id=user.id)
    # Convertim obiectele SQLAlchemy în modele Pydantic, apoi în dicționare.
    # Jinja2-ul `tojson` va gestiona corect conversia în obiecte JavaScript.
    clients_data = [schemas.Client.from_orm(client).model_dump() for client in db_clients]
    return templates.TemplateResponse("kunden.html", {"request": request, "user": user, "clients": clients_data})

@app.post("/kunden", response_class=HTMLResponse)
async def create_new_client(
    request: Request, 
    db: Session = Depends(get_db), 
    user: supabase_client.User = Depends(get_current_user), 
    name: str = Form(...), 
    address: str = Form(...), 
    zip_code: str = Form(...), 
    city: str = Form(...),
    vat_id: Optional[str] = Form(None),
    leitweg_id: Optional[str] = Form(None)
):
    client_data = schemas.ClientCreate(name=name, address=address, zip_code=zip_code, city=city, vat_id=vat_id, leitweg_id=leitweg_id)
    crud.create_client(db, client=client_data, owner_id=user.id)
    # Redirecționează înapoi la pagina de clienți pentru a vedea noul client în listă
    return RedirectResponse(url="/kunden", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/kunden/edit/{client_id}")
async def edit_client_route(
    client_id: int,
    db: Session = Depends(get_db),
    user: supabase_client.User = Depends(get_current_user),
    name: str = Form(...), 
    address: str = Form(...), 
    zip_code: str = Form(...), 
    city: str = Form(...),
    vat_id: Optional[str] = Form(None),
    leitweg_id: Optional[str] = Form(None)
):
    client_data = schemas.ClientCreate(
        name=name, 
        address=address, 
        zip_code=zip_code, 
        city=city,
        vat_id=vat_id if vat_id and vat_id.strip() else None,
        leitweg_id=leitweg_id if leitweg_id and leitweg_id.strip() else None
    )
    updated_client = crud.update_client(db, client_id=client_id, client_data=client_data, owner_id=user.id)
    if not updated_client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    return RedirectResponse(url="/kunden", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/kunden/delete/{client_id}")
async def delete_client_route(
    client_id: int,
    db: Session = Depends(get_db),
    user: supabase_client.User = Depends(get_current_user)
):
    # Verifică dacă există facturi asociate acestui client
    invoices_count = db.query(models.Invoice).filter(models.Invoice.client_id == client_id, models.Invoice.owner_id == user.id).count()
    if invoices_count > 0:
        # Nu permitem ștergerea dacă există facturi
        # Aici poți returna un mesaj de eroare pe care să-l afișezi în frontend
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Dieser Kunde kann nicht gelöscht werden, da Rechnungen mit ihm verknüpft sind.")

    crud.delete_client(db, client_id=client_id, owner_id=user.id)
    return RedirectResponse(url="/kunden", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/invoice/delete/{invoice_id}")
async def delete_invoice_route(
    invoice_id: int,
    db: Session = Depends(get_db),
    user: supabase_client.User = Depends(get_current_user)
):
    # Verifică dacă factura există și aparține utilizatorului curent (important pentru securitate)
    invoice_to_delete = crud.get_invoice_by_id(db, invoice_id=invoice_id, owner_id=user.id)
    if not invoice_to_delete:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    # Șterge fișierul PDF de pe disc
    pdf_full_path = os.path.join(STATIC_DIR, invoice_to_delete.pdf_file_path)
    if os.path.exists(pdf_full_path):
        os.remove(pdf_full_path)

    # Șterge înregistrarea din baza de date
    crud.delete_invoice(db, invoice_id=invoice_id)

    return RedirectResponse(url="/rechnungen", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/rechnung-erstellen", response_class=HTMLResponse)
async def get_rechnung_erstellen_form(
    request: Request, 
    db: Session = Depends(get_db),
    user: supabase_client.User = Depends(get_current_user)
):
    company_profile = crud.get_company_profile(db, user_id=user.id)
    clients = crud.get_clients_by_owner(db, owner_id=user.id, limit=1000)
    return templates.TemplateResponse("rechnung-erstellen.html", {"request": request, "user": user, "profile": company_profile, "clients": clients})

@app.get("/rechnung-bearbeiten/{invoice_id}", response_class=HTMLResponse)
async def get_rechnung_bearbeiten_form(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: supabase_client.User = Depends(get_current_user)
):
    # Fetch the invoice to edit, ensuring it belongs to the current user
    invoice = crud.get_invoice_by_id(db, invoice_id=invoice_id, owner_id=user.id)
    if not invoice:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    
    company_profile = crud.get_company_profile(db, user_id=user.id)
    clients = crud.get_clients_by_owner(db, owner_id=user.id, limit=1000)
    return templates.TemplateResponse("rechnung-erstellen.html", {"request": request, "user": user, "profile": company_profile, "clients": clients, "invoice": invoice})

@app.get("/passwort-aendern", response_class=HTMLResponse)
async def get_password_change_page(request: Request, user: supabase_client.User = Depends(get_current_user)):
    return templates.TemplateResponse("passwort-aendern.html", {"request": request, "user": user})

@app.post("/passwort-aendern")
async def handle_password_change(request: Request, user: supabase_client.User = Depends(get_current_user)):
    # User-ul este deja validat de `Depends`.
    data = await request.json()
    new_password = data.get("new_password")
    if not new_password or len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Das Passwort muss mindestens 6 Zeichen lang sein.")
    
    return await supabase_client.update_user_password(user, new_password)

@app.get("/abonnement", response_class=HTMLResponse)
async def get_subscription_page(request: Request, user: supabase_client.User = Depends(get_current_user)):
    # Aici se va adăuga logica pentru a prelua datele abonamentului (ex: de la Stripe)
    return templates.TemplateResponse("abonnement.html", {"request": request, "user": user})

@app.post("/rechnung-erstellen")
async def create_rechnung(
    request: Request, 
    db: Session = Depends(get_db),
    user: supabase_client.User = Depends(get_current_user)
) -> Response: # Am schimbat tipul de retur pentru a permite FileResponse
    form_data = await request.form()
    form_data_dict = dict(form_data)

    # Verifică dacă este o actualizare sau o creare nouă
    invoice_id_to_update = form_data_dict.get("invoice_id")

    # --- Logic for Client Handling ---
    client_id_str = form_data_dict.get("client_select")
    client_id = int(client_id_str) if client_id_str and client_id_str.isdigit() else None

    # If no client is selected, but client data is provided, create a new client
    if not client_id and form_data_dict.get("receiver_name"):
        new_client_data = schemas.ClientCreate(
            name=form_data_dict.get("receiver_name"),
            address=form_data_dict.get("receiver_address"),
            zip_code=form_data_dict.get("receiver_zip"),
            city=form_data_dict.get("receiver_city")
        )
        new_client = crud.create_client(db, client=new_client_data, owner_id=user.id)
        client_id = new_client.id
    # --- End Client Handling ---

    # Procesează articolele (line items) din formular
    items = []
    item_data = {}
    for key, value in form_data_dict.items():
        if key.startswith("items["):
            parts = key.replace("]", "").split("[") # -> items, index, field
            index = int(parts[1])
            field = parts[2]
            
            if index not in item_data:
                item_data[index] = {}
            item_data[index][field] = value

    # Sortează și adaugă în lista finală
    for index in sorted(item_data.keys()):
        # Asigură-te că valorile numerice sunt convertite corect
        # Tratează string-urile goale ca 0.0
        item = item_data[index] # item este un dicționar
        item['quantity'] = Decimal(item.get('quantity') or '0')
        item['unit_price'] = Decimal(item.get('unit_price') or '0')
        item['vat_rate'] = Decimal(item.get('vat_rate') or '0')
        items.append(item_data[index])

    # Folosește logo-ul salvat în profilul utilizatorului, dacă există
    company_profile = crud.get_company_profile(db, user_id=user.id)
    
    # Corecție: Construiește calea absolută către logo pentru a fi citit de pe disc
    absolute_logo_path = None
    if company_profile and company_profile.logo_path:
        # company_profile.logo_path este ceva de genul "logo_uuid.png"
        absolute_logo_path = os.path.join(STATIC_DIR, company_profile.logo_path)

    # Adaugă IBAN-ul din profil în datele formularului dacă nu este deja prezent
    if 'sender_iban' not in form_data_dict and company_profile and company_profile.iban:
        form_data_dict['sender_iban'] = company_profile.iban

    # Adaugă USt-IdNr. din profil în datele formularului
    if 'sender_vat_id' not in form_data_dict and company_profile and company_profile.sender_vat_id:
        form_data_dict['sender_vat_id'] = company_profile.sender_vat_id

    # Adaugă Leitweg-ID din datele clientului, dacă a fost selectat unul
    if client_id:
        client = crud.get_client(db, client_id=client_id, owner_id=user.id)
        if client and client.leitweg_id:
            form_data_dict['leitweg_id'] = client.leitweg_id

    # Generează PDF-ul în memorie
    pdf_buffer = generate_invoice_pdf(form_data_dict, items, absolute_logo_path)

    # Verifică dacă utilizatorul a specificat un nume de fișier și îl sanitizează
    user_filename = form_data_dict.get("pdf_filename")
    if user_filename:
        # Elimină caracterele invalide și adaugă extensia .pdf
        # Înlocuiește spațiile cu underscore și elimină caracterele nesigure.
        safe_filename = user_filename.replace(" ", "_")
        safe_filename = "".join(c for c in safe_filename if c.isalnum() or c in ('-', '_'))
        
        # Dacă numele este gol după curățare, folosește fallback-ul. Altfel, folosește numele curățat.
        pdf_filename = f"{safe_filename}.pdf" if safe_filename else f"rechnung_{user.id[:8]}_{uuid.uuid4().hex[:8]}.pdf"
    else:
        # Folosește un nume generat automat dacă nu este specificat unul
        pdf_filename = f"rechnung_{user.id[:8]}_{uuid.uuid4().hex[:8]}.pdf"

    pdf_file_path = os.path.join(INVOICES_DIR, pdf_filename)
    with open(pdf_file_path, "wb") as f:
        f.write(pdf_buffer.getvalue())

    # Calculează totalul brut pentru a-l salva în DB, luând în considerare TVA-ul per articol
    total_netto = Decimal("0.0")
    total_vat = Decimal("0.0")
    for item in items:
        quantity = Decimal(str(item.get("quantity") or "0"))
        unit_price = Decimal(str(item.get("unit_price") or "0"))
        vat_rate = Decimal(str(item.get("vat_rate") or "0"))
        line_netto = quantity * unit_price
        total_netto += line_netto
        total_vat += line_netto * (vat_rate / Decimal("100.0"))
    
    total_brutto = total_netto + total_vat

    # Verifică și procesează data facturii, folosind data curentă ca alternativă
    invoice_date_str = form_data_dict.get("invoice_date")
    invoice_date_obj = datetime.strptime(invoice_date_str, "%Y-%m-%d").date() if invoice_date_str else datetime.now().date()

    # Creează înregistrarea în baza de date
    invoice_data = schemas.InvoiceCreate(
        invoice_number=form_data_dict.get("invoice_number", "N/A"),
        invoice_date=invoice_date_obj,
        total_amount=total_brutto,
        pdf_file_path=f"invoices/{pdf_filename}" # Folosim slash pentru a asigura compatibilitatea URL
    ) # Am scos line_items de aici, le vom gestiona separat

    if invoice_id_to_update:
        # Actualizează o factură existentă
        updated_invoice = crud.update_invoice(
            db, 
            invoice_id=int(invoice_id_to_update), 
            invoice_data=invoice_data, 
            line_items_data=items, 
            owner_id=user.id,
            client_id=client_id
        )
    else:
        # Creează o factură nouă
        crud.create_invoice(db, invoice=invoice_data, line_items_data=items, owner_id=user.id, client_id=client_id)

    # Returnează direct fișierul PDF pentru descărcare
    # Acest lucru va declanșa automat dialogul de salvare în browser.
    return Response(
        content=pdf_buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={pdf_filename}"}
    )

if __name__ == "__main__":
    import uvicorn
    import webbrowser
    # Rulează serverul uvicorn direct din script, ceea ce rezolvă problemele de reload pe Windows
    host = "127.0.0.1"
    port = 8001
    webbrowser.open(f"http://{host}:{port}")
    uvicorn.run("app.main:app", host=host, port=port, reload=True)