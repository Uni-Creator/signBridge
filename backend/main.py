# main.py
"""
SignBridge Backend - FastAPI + WebSocket Server
================================================
Endpoints:
  POST /register        -> Firebase user registration
  POST /login            -> Firebase user login
  POST /logout            -> Firebase user logout
  POST /forgot-password   -> Send password reset email
  POST /update-password   -> Update password (auth required)
  GET  /history           -> Retrieve translation history
  POST /history/store     -> Store a translation
  DELETE /history/<id>    -> Delete a translation
  DELETE /history/clear   -> Delete all translations
  WS   /ws                -> Real-time sign detection

Migrated from Flask + flask-sock + flask-limiter to FastAPI + native
ASGI WebSockets + slowapi.
"""
from datetime import datetime
import os
import logging
import threading
import concurrent.futures
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request, Header, Depends
from fastapi import Cookie, WebSocket, WebSocketException,WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError, HTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.concurrency import run_in_threadpool

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from pydantic import BaseModel, Field, EmailStr, ConfigDict

#  App setup 
from authentication import register_account, login_account, logout_user, forgot_password, update_password
from history import retrieve_history, store_translation, delete_translation, delete_all_translations
from websocket_handler import handle_websocket
from model import ISLModelAPI
from firebase_admin_init import admin_auth


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
model_api = ISLModelAPI(top_k=1)

CLIP_LENGTH = 16
FRAME_DELAY = 0.08
RESIZE_DIM = 224

_SAVE_TEST_VIDEOS = os.environ.get("SAVE_TEST_VIDEOS", "0") == "1"


#  Rate limiting 
# The rate-limit key is the authenticated Firebase uid when available
# (set on request.state.user_id by the require_auth dependency), and
# falls back to the client's remote address for unauthenticated routes.
def get_user_id(request: Request) -> str:
    return getattr(request.state, "user_id", None) or get_remote_address(request)


limiter = Limiter(
    key_func=get_user_id, 
    default_limits = [
        "100/minute", 
        "5000/day"
        ]
    )


#  Lifespan (startup/shutdown) 
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Wake up the HF Space in the background at startup.
    threading.Thread(
        target=model_api.check_health, 
        daemon=True
        ).start()
    yield
    executor.shutdown(wait=False)


app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

allowed_origins_str = os.environ.get(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://localhost:8080,http://127.0.0.1:3000,app://signbridge"
)
allowed_origins = [origin.strip() for origin in allowed_origins_str.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


#  Request models
class AuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class ForgotPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=6, max_length=128)


class StoreTranslationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    translation: str = Field(min_length=1, max_length=5000)


#  Auth dependency 
#  Header-based auth dependency (primary)
async def require_auth(
    request: Request,
    authorization: str = Header(default=""),
) -> str:
    if not authorization.startswith("Bearer "):
        raise StarletteHTTPException(
            status_code=401, 
            detail="Missing or invalid token"
            )

    token = authorization.split(" ", 1)[1]

    if not token:
        raise StarletteHTTPException(
            status_code=401, 
            detail="Missing or invalid token"
            )

    try:

        decoded = await run_in_threadpool(
            admin_auth.verify_id_token, token, check_revoked=True
        )
    except admin_auth.ExpiredIdTokenError:
        raise StarletteHTTPException(
            status_code=401, 
            detail="Token expired"
            )
    except admin_auth.RevokedIdTokenError:
        raise StarletteHTTPException(
            status_code=401, 
            detail="Token revoked"
            )
    except Exception:
        raise StarletteHTTPException(
            status_code=401, 
            detail="Invalid token"
            )

    request.state.user_id = decoded["uid"]
    return decoded["uid"]

#  Cookie-based auth dependency (legacy support)
async def require_ws_auth(
    websocket: WebSocket,
    authorization: str = Header(default=""),
) -> str:

    if not authorization.startswith("Bearer "):
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Missing or invalid token",
        )

    token = authorization.split(" ", 1)[1].strip()

    if not token:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Missing or invalid token",
        )

    try:
        decoded = await run_in_threadpool(
            admin_auth.verify_id_token,
            token,
            check_revoked=True,
        )

        return decoded["uid"]

    except admin_auth.ExpiredIdTokenError:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Token expired",
        )

    except admin_auth.RevokedIdTokenError:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Token revoked",
        )

    except admin_auth.InvalidIdTokenError:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid token",
        )

    except Exception:
        logger.exception("WebSocket authentication failed")
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Authentication failed",
        )


#  Error handlers 
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code = exc.status_code, 
        content = {
            "error": exc.detail
            }
        )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = []
    for error in exc.errors():

        field = ".".join(str(x) for x in error["loc"] if x != "body")

        errors.append(
            {
                "field": field, 
                "message": error["msg"]
            }
        )

    return JSONResponse(
        status_code = 400, 
        content = {
            "error": "Invalid request body", 
            "details": errors
            }
        )


@app.exception_handler(RateLimitExceeded)
async def ratelimit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code = 429, 
        content = {
            "error": str(exc.detail)
            }
        )


#  REST routes 
@app.get("/")
@limiter.limit(
    "20/minute", 
    error_message="Too many requests. Please try again later."
)
async def index(request: Request):
    return {
        "message": "SignBridge API is running", 
        "version": "2.0"
        }

@app.get("/health")
@limiter.limit(
    "10/minute", 
    error_message="Too many requests. Please try again later."
)
async def health(request: Request):
    return {"status": "ok"}


@app.get("/health/deep")
@limiter.limit(
    "5/minute", 
    error_message="Too many requests. Please try again later."
)
async def deep_health(request: Request):
    database_ok = False
    model_ok = False

    try:
        admin_auth.collection("users").limit(1).get()
        database_ok = True
    except Exception:
        logger.exception("Database health check failed")

    try:
        model_result = model_api.check_health()
        model_ok = True
    except Exception:
        logger.exception("Model server health check failed")
        model_result = None

    result = {
        "status": "ok" if database_ok and model_ok else "degraded",
        "database": "ok" if database_ok else "error",
        "model_server": model_result if model_ok else "error",
    }

    if not database_ok or not model_ok:
        raise HTTPException(status_code=503, detail=result)

    return result


@app.post("/register")
@limiter.limit(
    "5/minute", 
    error_message="Too many registration attempts. Please try again later."
)
async def register(request: Request, account: AuthRequest):
    res = register_account(account.email, account.password)

    if not res:
        return JSONResponse(
            status_code = 400,
            content = {
                "id": "", 
                "token": "", 
                "error": "Registration failed"
                },
        )

    logger.info("Registered user")
    return res


@app.post("/login")
@limiter.limit(
    "5/minute", 
    error_message="Too many login attempts. Please try again later."
)
async def login(request: Request, account: AuthRequest):
    res = login_account(account.email, account.password)

    if not res:
        return JSONResponse(
            status_code = 400,
            content = {
                "id": "", 
                "token": "", 
                "error": "Login failed"
                },
        )

    logger.info("Login: user logged in using email")
    return res


@app.post("/logout")
@limiter.limit(
    "10/minute", 
    error_message="Too many logout requests. Please try again later."
)
async def logout(request: Request, user_id: str = Depends(require_auth)):

    try:

        logout_user(user_id)
        return {
            "message": "Logged out successfully"
            }

    except Exception:
        logger.exception("Failed to logout user")
        raise StarletteHTTPException(status_code=500, detail="Logout failed")


@app.post("/forgot-password")
@limiter.limit(
    "3/minute", 
    error_message="Too many forgot password attempts. Please try again later."
)
async def forgot_pwd(request: Request, account: ForgotPasswordRequest):
    forgot_password(account.email)

    logger.info("Password reset request received")

    return {
        "success": "Password reset email has been sent."
        }


@app.post("/update-password")
@limiter.limit(
    "3/minute", 
    error_message="Too many password update attempts. Please try again later."
)
async def update_pwd(
    request: Request,
    account: ResetPasswordRequest,
    user_id: str = Depends(require_auth),
):
    status = update_password(user_id, account.password)

    if not status:
        raise StarletteHTTPException(
            status_code = 500, 
            detail = "Password update failed."
            )

    logger.info("Password updated successfully.")
    
    return {
        "success": "Password has been updated."
        }


@app.get("/history")
@limiter.limit(
    "20/minute", 
    error_message="Too many history requests. Please try again later."
)
async def get_history(
    request: Request, 
    user_id: str = Depends(require_auth),
):
    
    try:
        return {
            "history": retrieve_history(user_id)
            }

    except Exception:
        logger.exception("Failed to retrieve history")

        return JSONResponse(
            status_code = 500,
            content = {
                "history": "", 
                "error": "Failed to retrieve history"
                }
            )


@app.post("/history/store", status_code=201)
@limiter.limit(
    "50/minute", 
    error_message="Too many store requests. Please try again later."
)
async def store_history(
    request: Request,
    data: StoreTranslationRequest,
    user_id: str = Depends(require_auth),
):
    try:
        item = store_translation(user_id, data.translation)
        return item

    except Exception:   
        logger.exception("Failed to store history")

        raise StarletteHTTPException(
            status_code = 500, 
            detail = "Failed to store history"
            )

@app.delete("/history/clear")
@limiter.limit(
    "10/minute", 
    error_message="Too many clear requests. Please try again later."
)
async def clear_history(
    request: Request, 
    user_id: str = Depends(require_auth),
):
    try:
        deleted = delete_all_translations(user_id)
        logger.info(deleted)

        if not deleted:
            raise StarletteHTTPException(
                status_code = 404, 
                detail = "No history found"
            )

        return {
            "message": "History deleted"
            }
            
    except StarletteHTTPException:
        raise
        
    except Exception:
        logger.exception("Failed to delete history")
        raise StarletteHTTPException(
            status_code = 500, 
            detail = "Failed to delete history"
        )


@app.delete("/history/{translation_id}")
@limiter.limit(
    "50/minute", 
    error_message="Too many delete history requests. Please try again later."
)
async def delete_history(
    request: Request,
    translation_id: str,
    user_id: str = Depends(require_auth),
):
    try:
        deleted = delete_translation(user_id, translation_id)

        if not deleted:
            raise StarletteHTTPException(
                status_code=404, 
                detail="Translation not found"
            )

        return {
            "message": "Translation deleted"
            }

    except StarletteHTTPException:
        raise

    except Exception:
        logger.exception("Failed to delete history")
        raise StarletteHTTPException(
            status_code=500, 
            detail="Failed to delete history"
        )


# SLT Model routes
@app.get("/slt/health")
@limiter.limit(
    "5/minute",
    error_message="Too many model requests. Please try again later."
)
async def slt_health(
    request: Request,
    user_id: str = Depends(require_auth)
):
    logger.info("SLT model API health check")
    health = model_api.check_health()
    
    if health:
        return {
            "status" : "healthy",
            "timestamp" : datetime.utcnow().isoformat()
        }

    raise StarletteHTTPException(
        status_code=503,
        detail="Model not ready"
    )


@app.get("/slt/health/deep")
@limiter.limit(
    "5/minute",
    error_message="Too many model requests. Please try again later."
)
async def slt_deep_health(
    request: Request,
    user_id: str = Depends(require_auth)
):
    try:
        logger.info("SLT model API health check")
        return model_api.deep_health()
    except Exception:
        logger.exception("SLT model API health check failed")
        raise StarletteHTTPException(
            status_code=503,
            detail="Model not ready"
        )

# SLT WebSocket route 
@app.websocket("/slt/ws")
async def websocket_translate(
    websocket: WebSocket,
    user_id: str = Depends(require_ws_auth),
):
    landmark_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    inference_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    await handle_websocket(
        websocket, model_api, landmark_executor, inference_executor
    )


# SLP Model routes

@app.get("/slp/health")
@limiter.limit(
    "5/minute",
    error_message="Too many model requests. Please try again later."
)
async def slp_health(
    request: Request,
    user_id: str = Depends(require_auth),
):
    ...


@app.get("/slp/health/deep")
@limiter.limit(
    "5/minute",
    error_message="Too many model requests. Please try again later."
)
async def slp_deep_health(
    request: Request,
    user_id: str = Depends(require_auth),
):
    ...


# SLP WebSocket route
@app.websocket("/slp/ws")
async def websocket_produce(
    websocket: WebSocket,
    user_id: str = Depends(require_ws_auth),
):
    ...


#  Entry point 
if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 5000))
    print(f"\n{'='*60}")
    print(f"  SignBridge Backend  ->  http://0.0.0.0:{port}/")
    print(f"  WebSocket         ->  ws://0.0.0.0:{port}/ws")
    print(f"  Android emulator  ->  use 10.0.2.2 instead of localhost")
    print(f"{'='*60}\n")
    uvicorn.run(app, host="0.0.0.0", port=port)