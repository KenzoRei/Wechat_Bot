from fastapi import Header, HTTPException
import config


async def verify_admin_key(x_admin_key: str = Header(...)):
    """
    FastAPI dependency. Injected into every admin route via Depends().
    Raises 401 if header is missing or does not match ADMIN_API_KEY.
    """
    if x_admin_key != config.ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing admin key")
