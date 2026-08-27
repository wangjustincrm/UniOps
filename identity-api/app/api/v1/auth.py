"""Auth endpoints — moved from epms-api (Phase 0-B4), contracts unchanged.

Additions over the epms original:
- audit_log rows on login / login_failed / password_changed / mfa toggle
  (FIN-AUD-001; append-only, ≥6y retention)
- CompanyConfig is read-only here (never get_or_create — epms owns that table;
  missing row ⇒ mfa on, env-fallback SMTP)
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Union

from fastapi import APIRouter, HTTPException, status
from jose import JWTError
from sqlalchemy import select

from app.core.deps import CurrentUserPayload, RedisDep, SessionDep
from app.core.security import (
    create_access_token,
    create_mfa_token,
    create_refresh_token,
    decode_token,
    generate_otp,
    hash_password,
    otp_redis_key,
    verify_password,
)
from app.crud import user as user_crud
from app.models.audit import AuditLog
from app.models.config import CompanyConfig
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LogoutRequest,
    MfaChallengeRequest,
    MfaRequiredResponse,
    RefreshRequest,
    RegisterRequest,
    ResendOtpRequest,
    TokenPair,
    UpdateMeRequest,
    UserResponse,
)
from app.services.email import send_mfa_otp

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

_REFRESH_TTL_SECONDS = 7 * 24 * 3600


def _audit(db, action: str, *, actor_id=None, actor_email=None, detail=None) -> None:
    db.add(AuditLog(service="identity", action=action,
                    actor_id=actor_id, actor_email=actor_email, detail=detail))


async def _get_config(db) -> CompanyConfig | None:
    return (await db.execute(select(CompanyConfig).limit(1))).scalar_one_or_none()


def _password_expired(user, cfg: CompanyConfig | None) -> bool:
    """True when the account's password is older than the configured expiry
    window. NULL expiry policy or unknown change date ⇒ never expired."""
    days = cfg.password_expiry_days if cfg else None
    if not days or user.password_changed_at is None:
        return False
    changed = user.password_changed_at
    if changed.tzinfo is None:
        changed = changed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - changed).days >= days


def _smtp_kwargs(cfg: CompanyConfig | None) -> dict:
    if cfg is None:
        return {}
    return {
        "smtp_host": cfg.smtp_host,
        "smtp_port": cfg.smtp_port,
        "smtp_user": cfg.smtp_user,
        "smtp_password": cfg.smtp_password,
        "smtp_use_tls": cfg.smtp_use_tls,
        "smtp_from": cfg.smtp_from,
    }


# ── Register ───────────────────────────────────────────────────────────────────

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: SessionDep):
    if await user_crud.get_by_email(db, body.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    user = await user_crud.create(db, body)
    _audit(db, "user_registered", actor_id=user.id, actor_email=user.email)
    return user


# ── Login ──────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=Union[TokenPair, MfaRequiredResponse])
async def login(body: LoginRequest, db: SessionDep, redis: RedisDep):
    user = await user_crud.authenticate(db, body.email, body.password)
    if user is None:
        # audit must survive the 401 — commit before raising (get_db rolls
        # back on exceptions, which would otherwise erase the trail)
        _audit(db, "login_failed", actor_email=body.email.lower())
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    cfg = await _get_config(db)

    # Password-expiry enforcement (Admin → Security → Password Expiry). When the
    # password is past its expiry window, flag the account so the frontend forces
    # a change after sign-in (surfaced via /auth/me → must_change_password).
    if not user.must_change_password and _password_expired(user, cfg):
        user.must_change_password = True
        _audit(db, "password_expired", actor_id=user.id, actor_email=user.email)
        await db.flush()

    needs_mfa = user.mfa_enabled or (cfg.mfa_enabled if cfg else True)

    if needs_mfa:
        otp = generate_otp()
        await redis.setex(otp_redis_key(str(user.id)), 600, otp)
        try:
            await send_mfa_otp(user.email, otp, **_smtp_kwargs(cfg))
        except Exception:
            logger.warning("SMTP unavailable — OTP for %s: %s", user.email, otp)
        return MfaRequiredResponse(mfa_token=create_mfa_token(str(user.id)))

    _audit(db, "login", actor_id=user.id, actor_email=user.email)
    return TokenPair(
        access_token=create_access_token(str(user.id), user.role),
        refresh_token=create_refresh_token(str(user.id)),
    )


# ── MFA challenge ───────────────────────────────────────────────────────────────

@router.post("/mfa/challenge", response_model=TokenPair)
async def mfa_challenge(body: MfaChallengeRequest, db: SessionDep, redis: RedisDep):
    invalid_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired MFA token",
    )
    try:
        payload = decode_token(body.mfa_token)
    except JWTError:
        raise invalid_exc
    if payload.get("type") != "mfa_challenge":
        raise invalid_exc

    user = await user_crud.get_by_id(db, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise invalid_exc

    key = otp_redis_key(str(user.id))
    stored_otp = await redis.get(key)
    if stored_otp is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Verification code has expired. Please sign in again.",
        )
    stored_str = stored_otp.decode() if isinstance(stored_otp, bytes) else stored_otp
    if body.code != stored_str:
        _audit(db, "mfa_failed", actor_id=user.id, actor_email=user.email)
        await db.commit()  # audit survives the 401 (see login_failed note)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect verification code.",
        )
    await redis.delete(key)

    _audit(db, "login", actor_id=user.id, actor_email=user.email, detail={"mfa": True})
    return TokenPair(
        access_token=create_access_token(str(user.id), user.role),
        refresh_token=create_refresh_token(str(user.id)),
    )


# ── Resend OTP ─────────────────────────────────────────────────────────────────

@router.post("/mfa/resend", status_code=status.HTTP_204_NO_CONTENT)
async def mfa_resend(body: ResendOtpRequest, db: SessionDep, redis: RedisDep):
    try:
        payload = decode_token(body.mfa_token)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid MFA token")
    if payload.get("type") != "mfa_challenge":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid MFA token")

    user = await user_crud.get_by_id(db, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid MFA token")

    key = otp_redis_key(str(user.id))
    ttl = await redis.ttl(key)
    if ttl > 540:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Please wait before requesting a new code.")

    cfg = await _get_config(db)
    otp = generate_otp()
    await redis.setex(key, 600, otp)
    try:
        await send_mfa_otp(user.email, otp, **_smtp_kwargs(cfg))
    except Exception:
        logger.warning("SMTP unavailable — OTP for %s: %s", user.email, otp)


# ── Refresh ────────────────────────────────────────────────────────────────────

@router.post("/refresh", response_model=TokenPair)
async def refresh_tokens(body: RefreshRequest, db: SessionDep, redis: RedisDep):
    invalid_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
    )
    try:
        payload = decode_token(body.refresh_token)
    except JWTError:
        raise invalid_exc
    if payload.get("type") != "refresh":
        raise invalid_exc

    blacklist_key = f"bl:rt:{body.refresh_token}"
    if await redis.exists(blacklist_key):
        raise invalid_exc

    user = await user_crud.get_by_id(db, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise invalid_exc

    await redis.setex(blacklist_key, _REFRESH_TTL_SECONDS, "1")
    return TokenPair(
        access_token=create_access_token(str(user.id), user.role),
        refresh_token=create_refresh_token(str(user.id)),
    )


# ── Logout ─────────────────────────────────────────────────────────────────────

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: LogoutRequest, redis: RedisDep):
    try:
        payload = decode_token(body.refresh_token)
        if payload.get("type") == "refresh":
            await redis.setex(f"bl:rt:{body.refresh_token}", _REFRESH_TTL_SECONDS, "1")
    except JWTError:
        pass


# ── Current user ───────────────────────────────────────────────────────────────

@router.get("/me", response_model=UserResponse)
async def get_me(payload: CurrentUserPayload, db: SessionDep):
    user = await user_crud.get_by_id(db, uuid.UUID(payload["sub"]))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.patch("/me", response_model=UserResponse)
async def update_me(body: UpdateMeRequest, payload: CurrentUserPayload, db: SessionDep):
    user = await user_crud.get_by_id(db, uuid.UUID(payload["sub"]))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return await user_crud.update_me(
        db, user,
        full_name=body.full_name,
        teams_account=body.teams_account,
        notification_channel=body.notification_channel,
        signature_image=body.signature_image,
    )


# ── MFA enable / disable ───────────────────────────────────────────────────────

@router.post("/mfa/enable", status_code=status.HTTP_204_NO_CONTENT)
async def mfa_enable(payload: CurrentUserPayload, db: SessionDep):
    user = await user_crud.get_by_id(db, uuid.UUID(payload["sub"]))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    await user_crud.enable_mfa(db, user)
    _audit(db, "mfa_enabled", actor_id=user.id, actor_email=user.email)


@router.post("/mfa/disable", status_code=status.HTTP_204_NO_CONTENT)
async def mfa_disable(payload: CurrentUserPayload, db: SessionDep):
    user = await user_crud.get_by_id(db, uuid.UUID(payload["sub"]))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    await user_crud.disable_mfa(db, user)
    _audit(db, "mfa_disabled", actor_id=user.id, actor_email=user.email)


# ── Change password ────────────────────────────────────────────────────────────

@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(body: ChangePasswordRequest, payload: CurrentUserPayload, db: SessionDep):
    user = await user_crud.get_by_id(db, uuid.UUID(payload["sub"]))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    user.hashed_password = hash_password(body.new_password)
    user.must_change_password = False
    user.password_changed_at = datetime.now(timezone.utc)
    _audit(db, "password_changed", actor_id=user.id, actor_email=user.email)
    await db.flush()
