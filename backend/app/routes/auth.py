
"""
AETHER AI Gateway - Authentication System

POST /auth/register - Create customer account
POST /auth/login    - Login and generate JWT
GET  /auth/me       - Verify JWT and view profile
POST /auth/logout   - Revoke JWT and logout
"""

import hashlib
from datetime import datetime, timezone

import jwt

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from fastapi.security import (
    HTTPBearer,
    HTTPAuthorizationCredentials,
)

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.revoked_token import RevokedToken

from app.schemas.auth import (
    TokenResponse,
    UserLogin,
    UserRegister,
    UserResponse,
)

from app.services.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_and_rehash,
)


# =====================================================
# ROUTER
# =====================================================

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)

bearer_scheme = HTTPBearer(auto_error=False)


# =====================================================
# 1. CUSTOMER REGISTRATION
# =====================================================

@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_user(
    data: UserRegister,
    db: Session = Depends(get_db),
):

    # Normalize customer email
    email = str(data.email).strip().lower()

    # Check whether the email already exists
    existing_user = db.scalar(
        select(User.id).where(User.email == email)
    )

    if existing_user is not None:
        raise HTTPException(
            status_code=409,
            detail="Email already registered",
        )

    # Store password securely using a hash
    new_user = User(
        email=email,
        password_hash=hash_password(data.password),
    )

    db.add(new_user)

    try:
        db.commit()

    except IntegrityError:
        db.rollback()

        raise HTTPException(
            status_code=409,
            detail="Email already registered",
        )

    except Exception:
        db.rollback()
        raise

    db.refresh(new_user)

    return new_user


# =====================================================
# 2. CUSTOMER LOGIN
# =====================================================

@router.post(
    "/login",
    response_model=TokenResponse,
)
def login_user(
    data: UserLogin,
    db: Session = Depends(get_db),
):

    email = str(data.email).strip().lower()

    # Find customer in PostgreSQL
    user = db.scalar(
        select(User).where(User.email == email)
    )

    # Verify customer password
    is_valid, new_hash = verify_and_rehash(
        data.password,
        user.password_hash if user else None,
    )

    if user is None or not is_valid:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Block disabled customer accounts
    if not user.is_active:
        raise HTTPException(
            status_code=403,
            detail="Account is disabled",
        )

    # Upgrade password hash if needed
    if new_hash:
        user.password_hash = new_hash
        db.commit()

    # Generate a JWT access token
    access_token = create_access_token(user.id)

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
    )


# =====================================================
# 3. JWT VERIFICATION
# =====================================================

def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    ),
    db: Session = Depends(get_db),
):

    # Check whether a Bearer token was provided
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
    ):
        raise HTTPException(
            status_code=401,
            detail="Please log in first",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # Verify JWT signature and expiry
    try:
        payload = decode_access_token(token)

        user_id = int(payload["sub"])

        if user_id <= 0:
            raise ValueError("Invalid user ID")

    except (
        jwt.PyJWTError,
        ValueError,
        KeyError,
        TypeError,
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Generate a SHA-256 fingerprint of the JWT
    token_hash = hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()

    # Check whether the token was revoked
    revoked = db.scalar(
        select(RevokedToken.id).where(
            RevokedToken.token_hash == token_hash
        )
    )

    if revoked is not None:
        raise HTTPException(
            status_code=401,
            detail="Token has been logged out",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Find customer using the JWT user ID
    user = db.get(User, user_id)

    # Check whether customer account is active
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=401,
            detail="Account not available",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


# =====================================================
# 4. CUSTOMER PROFILE
# =====================================================

@router.get(
    "/me",
    response_model=UserResponse,
)
def get_my_profile(
    current_user: User = Depends(get_current_user),
):

    return current_user


# =====================================================
# 5. CUSTOMER LOGOUT
# =====================================================

@router.post("/logout")
def logout_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    # The JWT has already been validated
    token = credentials.credentials

    # Generate a fingerprint instead of storing raw JWT
    token_hash = hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()

    # Read the expiry timestamp from the JWT
    payload = decode_access_token(token)

    expires_at = datetime.fromtimestamp(
        payload["exp"],
        tz=timezone.utc,
    )

    # Create revoked-token record
    revoked_token = RevokedToken(
        token_hash=token_hash,
        expires_at=expires_at,
    )

    db.add(revoked_token)

    try:
        db.commit()

    except IntegrityError:
        db.rollback()

        raise HTTPException(
            status_code=401,
            detail="Token already revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # The customer must now obtain a new valid JWT
    return {
        "message": "Logged out successfully"
    }
