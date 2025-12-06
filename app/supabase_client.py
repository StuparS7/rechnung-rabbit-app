import os
import asyncio
from fastapi import Request, HTTPException, status, Response
from dotenv import load_dotenv
from supabase import create_client, Client
from gotrue.errors import AuthApiError
from typing import Optional

supabase: Client

# Construiește calea absolută către directorul rădăcină al proiectului (RR)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Construiește calea absolută către fișierul .env
dotenv_path = os.path.join(BASE_DIR, '.env')

# Încarcă variabilele de mediu specificând calea explicită.
load_dotenv(dotenv_path=dotenv_path, override=True)

def init_supabase_client():
    """Inițializează clientul global Supabase. Trebuie apelat la pornirea aplicației."""
    global supabase
    url: str | None = os.environ.get("SUPABASE_URL")
    key: str | None = os.environ.get("SUPABASE_ANON_KEY")

    # Verificare robustă pentru a oferi un mesaj de eroare clar
    if not url or not key:
        error_message = "Variabilele de mediu SUPABASE_URL și SUPABASE_ANON_KEY nu au fost găsite. " \
                        "Asigură-te că ai un fișier .env în directorul rădăcină al proiectului (RR/) " \
                        "și că acesta conține valorile corecte."
        raise ValueError(error_message)
    supabase = create_client(url, key)

class SessionExpiredError(Exception):
    """Excepție personalizată pentru a semnala că sesiunea a expirat complet."""
    pass

class User:
    """O clasă simplă pentru a stoca datele utilizatorului de la Supabase."""
    def __init__(self, id: str, email: str, token: str, refresh_token: str = None, new_session = None):
        self.id = id
        self.email = email
        self.token = token
        self.refresh_token = refresh_token
        # Stocăm noua sesiune dacă a fost generată, pentru a actualiza cookie-urile
        self.new_session = new_session

async def get_current_user(request: Request) -> Optional[User]:
    """
    Validează token-ul Supabase dintr-un cookie și returnează datele utilizatorului.
    Dacă token-ul de acces este expirat, încearcă să-l reîmprospăteze.
    Returnează `User` la succes, `None` la eșec (dar nu la expirare completă).
    Aruncă `SessionExpiredError` dacă și refresh token-ul este invalid.
    """
    access_token = request.cookies.get("access_token")
    refresh_token = request.cookies.get("refresh_token")

    if not access_token:
        return None # Nu este autentificat

    try:
        # Rulăm funcția sincronă într-un thread separat pentru a nu bloca bucla de evenimente
        user_response = await asyncio.to_thread(supabase.auth.get_user, jwt=access_token)
        user_data = user_response.user
        return User(id=user_data.id, email=user_data.email, token=access_token, refresh_token=refresh_token)
    except AuthApiError:
        # A apărut o eroare, cel mai probabil token-ul a expirat. Încercăm refresh.
        if not refresh_token:
            return None # Nu putem face refresh, deci utilizatorul nu este logat.
        
        try:
            # Rulăm și refresh-ul într-un thread separat
            new_session_response = await asyncio.to_thread(supabase.auth.refresh_session, refresh_token=refresh_token)
            new_session = new_session_response.session
            # Refresh-ul a avut succes. Returnăm noul utilizator.
            return User(
                id=new_session.user.id, email=new_session.user.email,
                token=new_session.access_token, refresh_token=new_session.refresh_token,
                new_session=new_session
            )
        except (AuthApiError, TypeError):
            # Refresh-ul a eșuat. Sesiunea a expirat complet.
            raise SessionExpiredError("Refresh token is invalid or expired")
    except Exception:
        # Orice altă eroare neașteptată
        return None

async def update_user_password(user: User, new_password: str):
    """Actualizează parola utilizatorului în Supabase folosind token-ul său."""
    try:
        await asyncio.to_thread(supabase.auth.update_user, user_attributes={"password": new_password}, jwt=user.token)
        return Response(content='{"message": "Passwort erfolgreich aktualisiert!"}', media_type="application/json", status_code=200)
    except AuthApiError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e.message))
    except Exception as e:
        # Prindem orice altă eroare neașteptată pentru a oferi un mesaj clar
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An unexpected error occurred: {str(e)}")