from pydantic import BaseModel, EmailStr
from typing import List, Optional
from datetime import date
from decimal import Decimal

class LineItemBase(BaseModel):
    description: str
    quantity: Decimal
    unit_price: Decimal
    vat_rate: Optional[Decimal] = None

class LineItemCreate(LineItemBase):
    pass

class LineItem(LineItemBase):
    id: int
    invoice_id: int
    class Config:
        from_attributes = True
class CompanyProfileBase(BaseModel):
    sender_name: str
    sender_address: str
    sender_zip: str
    sender_city: str
    sender_tax_id: str
    sender_vat_id: Optional[str] = None
    register_court: Optional[str] = None
    register_number: Optional[str] = None
    managing_director: Optional[str] = None

class CompanyProfileCreate(CompanyProfileBase):
    iban: Optional[str] = None
    is_small_business: Optional[bool] = False

class CompanyProfile(CompanyProfileBase):
    id: int
    owner_id: str
    logo_path: Optional[str] = None
    is_small_business: bool
    class Config:
        from_attributes = True

class InvoiceBase(BaseModel):
    invoice_number: str
    invoice_date: date
    total_amount: Decimal
    pdf_file_path: str

class InvoiceCreate(InvoiceBase):
    pass

class Invoice(InvoiceBase):
    id: int
    owner_id: str
    line_items: List[LineItem] = []
    client: Optional['Client'] = None
    
    class Config:
        from_attributes = True


class ClientBase(BaseModel):
    name: str
    address: str
    zip_code: str
    city: str
    leitweg_id: Optional[str] = None
    vat_id: Optional[str] = None

class ClientCreate(ClientBase):
    pass

class Client(ClientBase):
    id: int
    owner_id: str

    class Config:
        from_attributes = True

Invoice.model_rebuild()