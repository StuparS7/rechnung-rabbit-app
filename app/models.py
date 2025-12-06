from sqlalchemy import Column, Integer, String, Float, Date, ForeignKey, Boolean
from sqlalchemy.orm import relationship

from .database import Base

class CompanyProfile(Base):
    __tablename__ = "company_profiles"
    id = Column(Integer, primary_key=True, index=True)
    sender_name = Column(String)
    sender_address = Column(String)
    sender_zip = Column(String)
    sender_city = Column(String)
    sender_tax_id = Column(String)
    logo_path = Column(String, nullable=True) # Store path to logo file
    iban = Column(String, nullable=True)
    owner_id = Column(String, index=True) # Supabase user ID (UUID string)
    is_small_business = Column(Boolean, default=False)
    sender_vat_id = Column(String, nullable=True)
    register_court = Column(String, nullable=True)
    register_number = Column(String, nullable=True)
    managing_director = Column(String, nullable=True)

class Client(Base):
    __tablename__ = "clients"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    address = Column(String)
    zip_code = Column(String)
    city = Column(String)
    owner_id = Column(String, index=True) # Supabase user ID (UUID string)

class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String, index=True)
    invoice_date = Column(Date)
    total_amount = Column(Float)
    pdf_file_path = Column(String) # Store path to PDF file
    owner_id = Column(String, index=True) # Supabase user ID (UUID string)
    
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    client = relationship("Client")

    line_items = relationship("LineItem", back_populates="invoice", cascade="all, delete-orphan")

class LineItem(Base):
    __tablename__ = "line_items"
    id = Column(Integer, primary_key=True, index=True)
    description = Column(String)
    quantity = Column(Float)
    unit_price = Column(Float)
    vat_rate = Column(Float) # Adaugă această linie
    invoice_id = Column(Integer, ForeignKey("invoices.id"))

    invoice = relationship("Invoice", back_populates="line_items")