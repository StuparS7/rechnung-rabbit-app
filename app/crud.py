from sqlalchemy.orm import Session, joinedload
from . import models, schemas

# Aici se pot adăuga funcții CRUD pentru facturi, clienți etc.

# --- Company Profile ---
def get_company_profile(db: Session, user_id: str):
    return db.query(models.CompanyProfile).filter(models.CompanyProfile.owner_id == user_id).first()

def create_or_update_company_profile(db: Session, profile: schemas.CompanyProfileCreate, user_id: str, logo_filename: str = None):
    db_profile = get_company_profile(db, user_id)
    if db_profile:
        # Update
        for var, value in vars(profile).items():
            setattr(db_profile, var, value) if value is not None else None
        if logo_filename:
            db_profile.logo_path = logo_filename
    else:
        # Create
        db_profile = models.CompanyProfile(**profile.dict(), owner_id=user_id, logo_path=logo_filename)
        db.add(db_profile)
    db.commit()
    db.refresh(db_profile)
    return db_profile

# --- Clients ---
def get_client(db: Session, client_id: int, owner_id: str):
    return db.query(models.Client).filter(models.Client.id == client_id, models.Client.owner_id == owner_id).first()

def get_clients_by_owner(db: Session, owner_id: str, skip: int = 0, limit: int = 100):
    return db.query(models.Client).filter(models.Client.owner_id == owner_id).offset(skip).limit(limit).all()

def create_client(db: Session, client: schemas.ClientCreate, owner_id: str):
    db_client = models.Client(**client.dict(), owner_id=owner_id)
    db.add(db_client)
    db.commit()
    db.refresh(db_client)
    return db_client

def delete_client(db: Session, client_id: int, owner_id: str):
    db_client = db.query(models.Client).filter(models.Client.id == client_id, models.Client.owner_id == owner_id).first()
    if db_client:
        db.delete(db_client)
        db.commit()

def update_client(db: Session, client_id: int, client_data: schemas.ClientCreate, owner_id: str):
    db_client = get_client(db, client_id=client_id, owner_id=owner_id)
    if not db_client:
        return None

    # Actualizează câmpurile clientului cu datele primite
    update_data = client_data.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_client, key, value)
    
    db.commit()
    db.refresh(db_client)
    return db_client

# --- Invoices ---
def create_invoice(db: Session, invoice: schemas.InvoiceCreate, line_items_data: list, owner_id: str, client_id: int = None):
    db_invoice = models.Invoice(**invoice.dict(), owner_id=owner_id, client_id=client_id)
    db.add(db_invoice)
    db.commit() # Commit to get the invoice ID
    db.refresh(db_invoice)

    for item_data in line_items_data:
        db_item = models.LineItem(**item_data, invoice_id=db_invoice.id)
        db.add(db_item)
    
    db.commit()
    db.refresh(db_invoice)
    return db_invoice

def get_invoices_by_owner(db: Session, owner_id: str, skip: int = 0, limit: int = 100):
    return db.query(models.Invoice).filter(models.Invoice.owner_id == owner_id).order_by(models.Invoice.invoice_date.desc()).offset(skip).limit(limit).all()

def get_invoice_by_id(db: Session, invoice_id: int, owner_id: str):
    return db.query(models.Invoice).options(joinedload(models.Invoice.client)).filter(models.Invoice.id == invoice_id, models.Invoice.owner_id == owner_id).first()

def update_invoice(db: Session, invoice_id: int, invoice_data: schemas.InvoiceCreate, line_items_data: list, owner_id: str, client_id: int = None):
    db_invoice = get_invoice_by_id(db, invoice_id=invoice_id, owner_id=owner_id)
    if not db_invoice:
        return None

    # Update invoice fields
    db_invoice.invoice_number = invoice_data.invoice_number
    db_invoice.invoice_date = invoice_data.invoice_date
    db_invoice.total_amount = invoice_data.total_amount
    db_invoice.pdf_file_path = invoice_data.pdf_file_path
    db_invoice.client_id = client_id

    # Delete old line items
    db.query(models.LineItem).filter(models.LineItem.invoice_id == invoice_id).delete()

    # Add new line items
    for item_data in line_items_data:
        db_item = models.LineItem(**item_data, invoice_id=invoice_id)
        db.add(db_item)

    db.commit()
    db.refresh(db_invoice)
    return db_invoice

def delete_invoice(db: Session, invoice_id: int):
    db.query(models.Invoice).filter(models.Invoice.id == invoice_id).delete()
    db.commit()