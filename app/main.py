"""
app/main.py - FastAPI Application for AutoInvoice (German Mittelstand Edition).

Provides all endpoints for:
- Authentication (Argon2id + JWT cookies)
- SQL-only Invoice Management & FTS5 Search (Task 1)
- Company & Locale Settings with DATEV SKR03/SKR04 (Task 2)
- Per-Invoice Detail Page & Editable Fields (Task 4)
- 9-Rule German Validation Engine in Python (Task 5)
- XRechnung EN 16931 XML Download (Task 6)
- Vendors & Item Price Comparison (Task 7)
- Cash Flow & Skonto Dashboard Panel (Task 8)
- EU VIES SOAP VAT Verification (Task 9)
- SEPA pain.001.001.03 Payment Run with IBAN Checksum & Human Approval (Task 10)
- Vendor IBAN Change Fraud Flag (Task 11)
- Tier 2: Month-End Checklist, Tax Summary, Validation Health, Vendor Scores,
  Duplicate Detection, Proactive Alerts, Chat Citations, and Copilot Action Approvals.
"""

import os
import re
import io
import csv
import json
import uuid
import time
import sqlite3
import logging
import datetime
import threading
import asyncio
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Request, Response, Depends, HTTPException, status, UploadFile, File, Form, Query
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

try:
    from werkzeug.utils import secure_filename
except ImportError:
    def secure_filename(filename: str) -> str:
        s = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(filename or "")).strip("._")
        return s or "upload.pdf"

from config import DEFAULT_INBOX_DIR, CHAT_DB_PATH, GROQ_API_KEY
from fx import convert_invoice_to_eur, FxRateUnavailableError
from app.core.config import settings
from app.core.database import get_db, AsyncSession
from app.core.rate_limiter import limiter, rate_limit_handler, RateLimitExceeded
from app.core.security import get_current_user
from app.models.user import User
from app.routers.auth import router as auth_router

from chat_store import (
    init_db,
    save_message,
    get_messages,
    list_conversations,
    get_or_create_conversation,
    add_dashboard_chart,
    list_dashboard_charts,
    delete_dashboard_chart,
    get_user_settings,
    update_user_settings,
)
from vector_store import (
    get_all_invoices,
    get_all_invoices_with_ids,
    get_invoice_by_identifier,
    update_invoice_record,
    delete_invoice,
    normalize_invoice_data,
    search_invoices_fts,
    parse_numeric,
    parse_german_date,
    format_eur_de,
    format_date_de,
    _user_id_variants,
)
from ocr_engine import OCRAgent
from graph_agent import GraphAgent
from orchestrator import MainOrchestrator, _line_item_rates
from validator import validate_invoice
from vies_service import check_vat_vies
from xrechnung import generate_xrechnung_xml
from zugferd import generate_zugferd_pdf
from sepa_generator import (
    validate_iban_checksum,
    preview_payment_run,
    execute_approved_payment_run,
    list_payment_runs,
    get_payment_run_xml,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fastapi_app")

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff"}
REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")

os.makedirs(DEFAULT_INBOX_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

# Initialise SQLite tables (starts empty; no legacy test data is copied)
init_db(CHAT_DB_PATH)

_orchestrator_cache: Dict[str, MainOrchestrator] = {}
_cache_lock = threading.Lock()

_CHART_LINK_RE = re.compile(
    r"\[CHART:\s*(/static/charts/[^\]]+)\]"
    r"|\[[^\]]*\]\((/static/charts/[^)]+)\)"
)


def _drop_dead_chart_links(text: str) -> str:
    if not text or "/static/charts/" not in text:
        return text
    charts_root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "static", "charts",
    )

    def _exists(url: str) -> bool:
        name = os.path.basename(url.strip())
        return bool(name) and os.path.isfile(os.path.join(charts_root, name))

    def _sub(m: "re.Match") -> str:
        url = m.group(1) or m.group(2) or ""
        return m.group(0) if _exists(url) else ""

    cleaned = _CHART_LINK_RE.sub(_sub, text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip() if cleaned != text else text


def get_orchestrator(conversation_id: str, user_id: str) -> MainOrchestrator:
    key = f"{user_id}:{conversation_id}"
    with _cache_lock:
        if key not in _orchestrator_cache:
            _orchestrator_cache[key] = MainOrchestrator(
                user_id=user_id,
                conversation_id=conversation_id,
            )
        return _orchestrator_cache[key]


def allowed_file(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def safe_path(filename: str, base_dir: str) -> Optional[str]:
    sanitised = secure_filename(filename)
    if not sanitised:
        return None
    resolved = os.path.abspath(os.path.join(base_dir, sanitised))
    base = os.path.abspath(base_dir)
    if not resolved.startswith(base + os.sep) and resolved != base:
        return None
    return resolved


def inject_system_message(conversation_id: str, user_id: str, content: str) -> None:
    try:
        get_or_create_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            db_path=CHAT_DB_PATH,
        )
        save_message(
            conversation_id=conversation_id,
            role="system",
            content=content,
            db_path=CHAT_DB_PATH,
        )
    except Exception as exc:
        logger.warning("System message injection failed (non-fatal): %s", exc)


def build_invoice_system_message(action: str, invoice_data: dict, file_name: str = "") -> str:
    if action == "uploaded":
        lines = ["[SYSTEM] Invoice uploaded and indexed into SQLite."]
        if file_name:
            lines.append(f"File: {file_name}")
        for key in [
            "vendor_name", "invoice_number", "total_amount",
            "invoice_date", "due_date", "payment_status", "currency", "validation_status",
        ]:
            val = invoice_data.get(key)
            if val is not None:
                lines.append(f"{key.replace('_', ' ').title()}: {val}")
        return "\n".join(lines)

    if action == "deleted":
        lines = ["[SYSTEM] Invoice deleted from SQLite."]
        for key in ["invoice_number", "vendor_name", "total_amount"]:
            val = invoice_data.get(key)
            if val is not None:
                lines.append(f"{key.replace('_', ' ').title()}: {val}")
        return "\n".join(lines)

    return f"[SYSTEM] Invoice {action}."


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(CHAT_DB_PATH)
    yield


app = FastAPI(
    title="AutoInvoice - Deutsche Buchhaltungs- & Rechnungsprüfung",
    description="FastAPI service for German SME Invoice Automation, 9-Rule Validation, XRechnung, DATEV, and SEPA pain.001.",
    version="3.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "error": exc.detail},
    )


app.include_router(auth_router)
app.include_router(auth_router, prefix="/api")

static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
templates_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# ─────────────────────────────────────────────────────────────────────────────
# Root Frontend Route - GET /
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root_index():
    index_file = os.path.join(templates_dir, "index.html")
    if os.path.exists(index_file):
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h2>AutoInvoice UI missing index.html</h2>")


# ─────────────────────────────────────────────────────────────────────────────
# Health Check - GET /api/health and GET /health
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/health")
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Task 1 - POST /api/chat (Thin SQL Router + Exact Aggregation + Citations)
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/chat")
async def chat(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    user_id = str(current_user.id)
    ct = request.headers.get("content-type", "")

    conversation_id = ""
    idempotency_key = None
    message = ""
    lang = "de"
    save_path = None

    if "multipart/form-data" in ct:
        form = await request.form()
        message = str(form.get("message") or "").strip()
        conversation_id = str(form.get("conversation_id") or "").strip()
        idempotency_key = str(form.get("idempotency_key") or "").strip() or None
        raw_lang = str(form.get("lang") or "de").strip().lower()
        lang = "en" if raw_lang.startswith("en") else "de"

        file_obj = form.get("file")
        if file_obj and hasattr(file_obj, "filename") and file_obj.filename:
            if not allowed_file(file_obj.filename):
                ext = os.path.splitext(file_obj.filename)[1]
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported file type '{ext}'. Allowed: pdf, png, jpg, jpeg, bmp, tiff",
                )
            safe_name = secure_filename(file_obj.filename)
            save_path = os.path.join(DEFAULT_INBOX_DIR, safe_name)
            content = await file_obj.read()
            with open(save_path, "wb") as f:
                f.write(content)
    else:
        try:
            data = await request.json()
        except Exception:
            data = {}
        message = str(data.get("message") or "").strip()
        conversation_id = str(data.get("conversation_id") or "").strip()
        idempotency_key = str(data.get("idempotency_key") or "").strip() or None
        raw_lang = str(data.get("lang") or "de").strip().lower()
        lang = "en" if raw_lang.startswith("en") else "de"

    if not message:
        raise HTTPException(status_code=400, detail="'message' is required and cannot be empty")
    if len(message) > 4000:
        raise HTTPException(status_code=400, detail="'message' must be 4000 characters or fewer")

    if not conversation_id:
        conversation_id = str(uuid.uuid4())

    file_context = f"\n[Attached file saved at: {save_path}]" if save_path else ""
    full_message = message + file_context

    try:
        orch = get_orchestrator(conversation_id=conversation_id, user_id=user_id)
        actual_conv_id = orch.conversation_id
        response_text = await asyncio.to_thread(
            orch.run,
            user_query=full_message,
            idempotency_key=idempotency_key,
            lang=lang,
        )
        response_text = _drop_dead_chart_links(response_text)
        return {
            "status": "success",
            "conversation_id": actual_conv_id,
            "response": response_text,
            "user_id": user_id,
        }
    except Exception as exc:
        logger.error("Chat error conv=%s: %s", conversation_id, exc, exc_info=True)
        err_detail = (
            "The AI assistant is temporarily busy. Please try again."
            if lang == "en"
            else "Der KI-Assistent ist momentan ausgelastet. Bitte versuchen Sie es erneut."
        )
        raise HTTPException(status_code=500, detail=err_detail)


# ─────────────────────────────────────────────────────────────────────────────
# Tier 2 - POST /api/chat/actions/{action_id}/resolve (Human Approval Step)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/chat/actions/{action_id}")
async def get_copilot_action(
    action_id: str,
    current_user: User = Depends(get_current_user),
):
    init_db(CHAT_DB_PATH)
    conn = sqlite3.connect(CHAT_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM copilot_actions WHERE action_id = ?",
            (action_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Action proposal not found")
        d = dict(row)
        d["target_invoices"] = json.loads(d.get("target_invoices_json") or "[]")
        return {"status": "success", "action": d}
    finally:
        conn.close()


@app.post("/api/chat/actions/{action_id}/resolve")
async def resolve_copilot_action(
    action_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """
    Human-in-the-loop confirmation endpoint for Copilot proposed actions
    (post_datev or mark_paid). Nothing executes until this endpoint is called
    with decision='approve'.
    """
    user_id = str(current_user.id)
    try:
        body = await request.json()
    except Exception:
        body = {}
    decision = str(body.get("decision") or "approve").lower()

    init_db(CHAT_DB_PATH)
    conn = sqlite3.connect(CHAT_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM copilot_actions WHERE action_id = ?",
            (action_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Action proposal not found")
        action = dict(row)
        if action["status"] != "pending":
            return {"status": "success", "action_status": action["status"], "message": "Aktion wurde bereits bearbeitet."}

        targets = json.loads(action.get("target_invoices_json") or "[]")
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        if decision in ("approve", "approved", "confirm"):
            act_type = action["action_type"]
            u_variants = _user_id_variants(user_id)
            placeholders = ",".join("?" for _ in u_variants)
            for inv_num in targets:
                if act_type == "mark_paid":
                    conn.execute(
                        f"UPDATE invoices SET payment_status = 'Paid' WHERE invoice_number = ? AND user_id IN ({placeholders})",
                        [inv_num, *u_variants],
                    )
                elif act_type == "post_datev":
                    conn.execute(
                        f"UPDATE invoices SET datev_booked = 1, datev_account = '1600' WHERE invoice_number = ? AND user_id IN ({placeholders})",
                        [inv_num, *u_variants],
                    )
            conn.execute(
                "UPDATE copilot_actions SET status = 'approved', resolved_at = ? WHERE action_id = ?",
                (now_iso, action_id),
            )
            conn.commit()
            return {
                "status": "success",
                "action_status": "approved",
                "action_type": act_type,
                "target_invoices": targets,
                "message": f"Freigabe bestätigt: {len(targets)} Beleg(e) erfolgreich aktualisiert.",
            }
        else:
            conn.execute(
                "UPDATE copilot_actions SET status = 'rejected', resolved_at = ? WHERE action_id = ?",
                (now_iso, action_id),
            )
            conn.commit()
            return {
                "status": "success",
                "action_status": "rejected",
                "message": "Aktion wurde vom Benutzer abgelehnt. Keine Änderungen vorgenommen.",
            }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Upload & Extraction - POST /api/invoice/upload
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/invoice/upload")
async def upload_invoice(
    file: UploadFile = File(...),
    conversation_id: Optional[str] = Form(None),
    foreign: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
):
    is_foreign = str(foreign or "").strip().lower() in ("true", "1", "yes", "on")
    if is_foreign and not GROQ_API_KEY:
        return JSONResponse(
            status_code=422,
            content={
                "status": "error",
                "error": "Foreign invoice conversion needs the Groq API key. Set GROQ_API_KEY and restart.",
            },
        )

    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    if not allowed_file(file.filename):
        ext = os.path.splitext(file.filename)[1]
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: pdf, png, jpg, jpeg, bmp, tiff",
        )

    safe_name = secure_filename(file.filename)
    save_path = os.path.join(DEFAULT_INBOX_DIR, safe_name)
    file_bytes = await file.read()
    with open(save_path, "wb") as f:
        f.write(file_bytes)

    agent = OCRAgent()
    result = await asyncio.to_thread(
        agent.process_and_index_invoice,
        save_path,
        user_id=str(current_user.id),
        force_llm=is_foreign,
    )

    if result.get("status") != "success":
        return JSONResponse(
            status_code=422,
            content={
                "status": "error",
                "error": f"File saved but OCR extraction failed: {result.get('message', 'Unknown error')}",
                "file_name": safe_name,
                "timings": result.get("timings", {}),
            },
        )

    extracted = result.get("extracted_data") or {}

    if conversation_id:
        msg = build_invoice_system_message("uploaded", extracted, safe_name)
        inject_system_message(conversation_id, str(current_user.id), msg)

    return {
        "status": "success",
        "file_name": safe_name,
        "doc_id": extracted.get("doc_id") or extracted.get("_doc_id"),
        "message": result.get("message", "Invoice uploaded, extracted, validated, and stored in SQLite."),
        "timings": result.get("timings", {}),
        "invoice_summary": {
            "doc_id": extracted.get("doc_id") or extracted.get("_doc_id"),
            "invoice_number": extracted.get("invoice_number"),
            "document_type": extracted.get("document_type", "invoice"),
            "vendor_name": extracted.get("vendor_name"),
            "vat_id": extracted.get("vat_id"),
            "total_amount": extracted.get("total_amount"),
            "subtotal": extracted.get("subtotal"),
            "tax_amount": extracted.get("tax_amount"),
            "invoice_date": extracted.get("invoice_date"),
            "due_date": extracted.get("due_date"),
            "payment_status": extracted.get("payment_status"),
            "currency": extracted.get("currency", "EUR"),
            "source_currency": extracted.get("source_currency", extracted.get("currency", "EUR")),
            "fx_rate": extracted.get("fx_rate", 1.0),
            "fx_rate_date": extracted.get("fx_rate_date", ""),
            "fx_rate_source": extracted.get("fx_rate_source", ""),
            "tax_treatment": extracted.get("tax_treatment", "standard"),
            "original_net": extracted.get("original_net", 0.0),
            "original_vat": extracted.get("original_vat", 0.0),
            "original_gross": extracted.get("original_gross", 0.0),
            "supplier_country": extracted.get("supplier_country", ""),
            "validation_status": extracted.get("validation_status", "PASS"),
            "validation_report": extracted.get("validation_report", {}),
            "vies_status": extracted.get("vies_status", "unchecked"),
            "iban_change_flag": extracted.get("iban_change_flag", 0),
            "duplicate_flag": extracted.get("duplicate_flag", 0),
            "line_items": extracted.get("line_items", []),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Invoices List & FTS5 Search - GET /api/invoices
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/invoices")
async def get_user_invoices(
    include_ids: bool = Query(False),
    q: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
):
    """
    Return all stored invoices for the authenticated user directly from SQLite.
    Supports optional `q` parameter for FTS5 full-text search across vendor_name,
    invoice_number, and item_descriptions.
    """
    user_id = str(current_user.id)
    try:
        if q and q.strip():
            invoices = search_invoices_fts(q.strip(), user_id=user_id, limit=100)
            if not include_ids:
                for inv in invoices:
                    inv.pop("_doc_id", None)
        else:
            invoices = get_all_invoices(user_id=user_id, include_ids=include_ids)

        return {
            "status": "success",
            "count": len(invoices),
            "invoices": invoices,
        }
    except Exception as exc:
        logger.error("Get invoices error for user %s: %s", user_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Fehler beim Laden der Belege aus SQLite.")


# ─────────────────────────────────────────────────────────────────────────────
# Task 4 & 5 - GET / PUT / POST /api/invoice/{doc_id} (Detail Page & Validation)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/invoice/{doc_id}")
async def get_single_invoice_detail(
    doc_id: str,
    current_user: User = Depends(get_current_user),
):
    user_id = str(current_user.id)
    inv = get_invoice_by_identifier(doc_id, user_id=user_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Beleg wurde nicht gefunden.")
    # Ensure validation report is always fresh
    if not inv.get("validation_report") or not inv["validation_report"].get("checks"):
        inv["validation_report"] = validate_invoice(inv)
        inv["validation_status"] = inv["validation_report"]["overall_status"]
    return {
        "status": "success",
        "invoice": inv,
    }


@app.put("/api/invoice/{doc_id}")
async def update_single_invoice(
    doc_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """
    Task 4: Save edited invoice fields (header, dates, payment/Skonto terms,
    VAT IDs, Leitweg-ID, line items, VAT breakdown) back to SQLite and re-validate.
    """
    user_id = str(current_user.id)
    try:
        updates = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    try:
        updated = await asyncio.to_thread(
            update_invoice_record,
            doc_id,
            updates,
            user_id=user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=404, detail="Beleg wurde nicht gefunden.")

    return {
        "status": "success",
        "message": "Beleg gespeichert und 9-Stufen-Validierung aktualisiert.",
        "invoice": updated,
    }


@app.post("/api/invoice/{doc_id}/convert")
async def convert_invoice_fx(
    doc_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """
    Task 3 & Task 7: Convert a foreign-currency invoice to EUR using ECB daily rates
    or a user-provided manual FX rate, applying the user-confirmed tax_treatment.
    """
    user_id = str(current_user.id)
    inv = get_invoice_by_identifier(doc_id, user_id=user_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Beleg wurde nicht gefunden.")

    try:
        body = await request.json()
    except Exception:
        body = {}

    raw_rate = body.get("fx_rate")
    manual_rate = None
    if raw_rate is not None and str(raw_rate).strip() != "":
        try:
            manual_rate = parse_numeric(raw_rate, 0.0)
            if manual_rate <= 0:
                raise ValueError("FX rate must be greater than 0.")
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid FX rate: {exc}")

    tax_treatment = body.get("tax_treatment")
    if body.get("source_currency"):
        inv["source_currency"] = str(body["source_currency"]).strip().upper()

    try:
        converted = await asyncio.to_thread(
            convert_invoice_to_eur,
            inv,
            manual_rate=manual_rate,
            tax_treatment=tax_treatment,
        )
    except FxRateUnavailableError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    updates = {
        "currency": converted["currency"],
        "source_currency": converted["source_currency"],
        "fx_rate": converted["fx_rate"],
        "fx_rate_date": converted["fx_rate_date"],
        "fx_rate_source": converted["fx_rate_source"],
        "tax_treatment": converted["tax_treatment"],
        "original_net": converted["original_net"],
        "original_vat": converted["original_vat"],
        "original_gross": converted["original_gross"],
        "subtotal": converted["subtotal"],
        "tax_amount": converted["tax_amount"],
        "total_amount": converted["total_amount"],
        "line_items": converted["line_items"],
        "vat_breakdown": converted["vat_breakdown"],
    }
    updated = await asyncio.to_thread(
        update_invoice_record,
        doc_id,
        updates,
        user_id=user_id,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Beleg wurde nicht gefunden.")
    return {
        "status": "success",
        "message": f"Converted {updated['source_currency']} to EUR ({updated['fx_rate_source']} rate {updated['fx_rate']}).",
        "invoice": updated,
    }


@app.post("/api/invoice/{doc_id}/validate")
async def revalidate_single_invoice(
    doc_id: str,
    current_user: User = Depends(get_current_user),
):
    user_id = str(current_user.id)
    updated = await asyncio.to_thread(
        update_invoice_record,
        doc_id,
        {"recheck_vies": True},
        user_id=user_id,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Beleg wurde nicht gefunden.")
    return {
        "status": "success",
        "invoice": updated,
        "validation_report": updated.get("validation_report"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Task 6 - GET /api/invoice/{doc_id}/xrechnung (EN 16931 XML Download)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/invoice/{doc_id}/xrechnung")
async def download_xrechnung_xml(
    doc_id: str,
    leitweg_id: Optional[str] = Query(None),
    use_settings_leitweg: bool = Query(False),
    current_user: User = Depends(get_current_user),
):
    """
    Task 6: Generate and download a valid XRechnung XML file (EN 16931) from an
    extracted invoice. Enforces V9 (Leitweg-ID BT-10 mandatory for XRechnung).
    """
    user_id = str(current_user.id)
    inv = get_invoice_by_identifier(doc_id, user_id=user_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Beleg wurde nicht gefunden.")

    raw_lw = str(leitweg_id if leitweg_id is not None else (inv.get("leitweg_id") or "")).strip()
    if raw_lw.lower() in ("", "n/a", "na", "none", "null", "-", "unknown", "unbekannt") and use_settings_leitweg:
        user_cfg = get_user_settings(user_id, CHAT_DB_PATH)
        raw_lw = str(user_cfg.get("leitweg_id") or "").strip()
    effective_leitweg = "" if raw_lw.lower() in ("", "n/a", "na", "none", "null", "-", "unknown", "unbekannt") else raw_lw

    val_check = validate_invoice({**inv, "leitweg_id": effective_leitweg})
    try:
        xml_content = generate_xrechnung_xml(inv, default_leitweg_id=effective_leitweg)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not val_check.get("xrechnung_ready", False):
        raise HTTPException(
            status_code=422,
            detail="XRechnung generation refused: invoice is not xrechnung_ready.",
        )

    clean_num = re.sub(r"[^A-Za-z0-9_-]", "_", str(inv.get("invoice_number") or "invoice"))
    filename = f"XRechnung_{clean_num}.xml"
    return Response(
        content=xml_content.encode("utf-8"),
        media_type="application/xml; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Task 9 - GET /api/invoice/{doc_id}/zugferd (ZUGFeRD PDF/A-3 Download)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/invoice/{doc_id}/zugferd")
async def download_zugferd_pdf(
    doc_id: str,
    leitweg_id: Optional[str] = Query(None),
    use_settings_leitweg: bool = Query(False),
    current_user: User = Depends(get_current_user),
):
    """
    Validate invoice with the 9-check gate (FAIL blocks, CANNOT VERIFY warns only)
    and return a ZUGFeRD / Factur-X PDF with embedded factur-x.xml.
    """
    user_id = str(current_user.id)
    inv = get_invoice_by_identifier(doc_id, user_id=user_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Beleg wurde nicht gefunden.")

    raw_lw = str(leitweg_id if leitweg_id is not None else (inv.get("leitweg_id") or "")).strip()
    if raw_lw.lower() in ("", "n/a", "na", "none", "null", "-", "unknown", "unbekannt") and use_settings_leitweg:
        user_cfg = get_user_settings(user_id, CHAT_DB_PATH)
        raw_lw = str(user_cfg.get("leitweg_id") or "").strip()
    effective_leitweg = "" if raw_lw.lower() in ("", "n/a", "na", "none", "null", "-", "unknown", "unbekannt") else raw_lw

    try:
        pdf_bytes, filename, warnings = generate_zugferd_pdf(inv, default_leitweg_id=effective_leitweg)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    warn_codes = ",".join(str(w.get("code") or "") for w in warnings if w.get("code"))
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    if warn_codes:
        headers["X-Validation-Warnings"] = warn_codes

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers=headers,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Delete Invoice - DELETE /api/invoice/{doc_id}
# ─────────────────────────────────────────────────────────────────────────────
@app.delete("/api/invoice/{doc_id}")
async def delete_user_invoice(
    doc_id: str,
    conversation_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
):
    user_id = str(current_user.id)
    if not doc_id or not doc_id.strip():
        raise HTTPException(status_code=400, detail="doc_id is required")

    invoice_details = get_invoice_by_identifier(doc_id, user_id=user_id) or {}
    success = delete_invoice(doc_id=doc_id, user_id=user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Invoice not found.")

    if conversation_id:
        msg = build_invoice_system_message("deleted", invoice_details)
        inject_system_message(conversation_id, user_id, msg)

    return {
        "status": "success",
        "message": "Invoice deleted successfully.",
        "doc_id": doc_id,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Task 2 - GET / PUT /api/settings (Company Profile, DATEV SKR03/04, Locale)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/settings")
async def api_get_settings(current_user: User = Depends(get_current_user)):
    cfg = get_user_settings(str(current_user.id), CHAT_DB_PATH)
    return {"status": "success", "settings": cfg}


@app.put("/api/settings")
async def api_update_settings(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    cfg = update_user_settings(str(current_user.id), payload, CHAT_DB_PATH)
    return {
        "status": "success",
        "message": "Unternehmensprofil, DATEV- und Gebietsschema-Einstellungen gespeichert.",
        "settings": cfg,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Task 7 & 11 & Tier 2 - GET /api/vendors & Price Comparison & Vendor Score
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/vendors")
async def api_get_vendors(
    item_query: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
):
    """
    Task 7: Vendors listing with total spend, invoice count, average invoice value,
    payment terms, VAT ID, IBAN, VIES status, IBAN fraud alert, Tier 2 validation
    pass rate per vendor, and side-by-side item unit price comparison with cheapest flagged.
    """
    user_id = str(current_user.id)
    invoices = get_all_invoices(user_id=user_id)

    # Read stored master vendor records from SQLite `vendors` table
    u_variants = _user_id_variants(user_id)
    master_vendors: Dict[str, Dict[str, Any]] = {}
    conn = sqlite3.connect(CHAT_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        if u_variants:
            placeholders = ",".join("?" for _ in u_variants)
            rows = conn.execute(
                f"SELECT * FROM vendors WHERE user_id IN ({placeholders})",
                u_variants,
            ).fetchall()
            for r in rows:
                d = dict(r)
                master_vendors[str(d.get("vendor_name") or "").lower()] = d
    finally:
        conn.close()

    vendor_groups: Dict[str, Dict[str, Any]] = {}
    all_items_catalog: Dict[str, List[Dict[str, Any]]] = {}

    for inv in invoices:
        v_name = str(inv.get("vendor_name") or "Unknown Vendor").strip()
        if not v_name or v_name.lower() in ("unknown vendor", "unknown", "n/a"):
            continue
        v_key = v_name.lower()
        master = master_vendors.get(v_key, {})

        grp = vendor_groups.setdefault(v_name, {
            "vendor_name": v_name,
            "address": inv.get("vendor_address") or master.get("address") or "",
            "vat_id": inv.get("vat_id") or master.get("vat_id") or "",
            "tax_number": inv.get("tax_number") or master.get("tax_number") or "",
            "iban": master.get("iban") or inv.get("vendor_iban") or "",
            "bic": master.get("bic") or inv.get("vendor_bic") or "",
            "payment_terms": inv.get("payment_terms") or master.get("payment_terms") or "",
            "vies_status": inv.get("vies_status") or master.get("vies_status") or "unchecked",
            "vies_checked_at": inv.get("vies_checked_at") or master.get("vies_checked_at") or "",
            "total_spend": 0.0,
            "net_spend": 0.0,
            "invoice_count": 0,
            "average_invoice_value": 0.0,
            "passed_core_count": 0,
            "passed_all_count": 0,
            "validation_score_pct": 100.0,
            "iban_change_detected": False,
            "iban_change_invoices": [],
            "invoices": [],
        })

        amt = float(inv.get("total_amount") or 0.0)
        net = float(inv.get("subtotal") or 0.0)
        grp["total_spend"] = round(grp["total_spend"] + amt, 2)
        grp["net_spend"] = round(grp["net_spend"] + net, 2)
        grp["invoice_count"] += 1

        vr = inv.get("validation_report") or {}
        if vr.get("core_math_valid", True):
            grp["passed_core_count"] += 1
        if vr.get("overall_status") == "PASS":
            grp["passed_all_count"] += 1

        if int(inv.get("iban_change_flag") or 0) == 1:
            grp["iban_change_detected"] = True
            grp["iban_change_invoices"].append({
                "invoice_number": inv.get("invoice_number"),
                "new_iban": inv.get("vendor_iban"),
                "previous_iban": inv.get("previous_iban"),
            })

        grp["invoices"].append({
            "doc_id": inv.get("doc_id"),
            "invoice_number": inv.get("invoice_number"),
            "document_type": inv.get("document_type"),
            "invoice_date": inv.get("invoice_date"),
            "due_date": inv.get("due_date"),
            "subtotal": net,
            "tax_amount": float(inv.get("tax_amount") or 0.0),
            "total_amount": amt,
            "payment_status": inv.get("payment_status"),
            "validation_status": inv.get("validation_status"),
            "iban_change_flag": inv.get("iban_change_flag", 0),
            "vendor_iban": inv.get("vendor_iban", ""),
        })

        # Collect item unit prices for comparison
        for it in inv.get("line_items") or []:
            art = str(it.get("article_number") or "").strip()
            desc = str(it.get("description") or it.get("name") or "").strip()
            # Clean parenthetical batch/note suffix so identical items group cleanly
            base_desc = re.sub(r"\s*\((?:Charge|Zuschnitt|Ersatz|lt\.\s*Ihrer|Preis\s*je|Rücksendung|Mengendifferenz|falsche).*?\)\s*$", "", desc).strip()
            item_key = f"{art} - {base_desc}" if art else base_desc
            if not item_key:
                continue
            if item_query and item_query.strip().lower() not in item_key.lower():
                continue

            up = parse_numeric(it.get("unit_price"), 0.0)
            pe = parse_numeric(it.get("price_unit"), 1.0)
            if pe <= 0:
                pe = 1.0
            eff_price = round(up / pe, 4)

            all_items_catalog.setdefault(item_key, []).append({
                "vendor_name": v_name,
                "invoice_number": inv.get("invoice_number"),
                "doc_id": inv.get("doc_id"),
                "invoice_date": inv.get("invoice_date"),
                "article_number": art,
                "description": base_desc,
                "unit": it.get("unit", "Stk"),
                "unit_price": up,
                "price_unit": pe,
                "effective_unit_price": eff_price,
                "discount_percent": parse_numeric(it.get("discount_percent"), 0.0),
            })

    vendors_list = []
    for v_name, grp in vendor_groups.items():
        cnt = grp["invoice_count"]
        grp["average_invoice_value"] = round(grp["total_spend"] / cnt, 2) if cnt else 0.0
        # Validation score per vendor (Tier 2): percentage of invoices passing core V1-V8 checks
        grp["validation_score_pct"] = round((grp["passed_core_count"] / cnt) * 100.0, 1) if cnt else 100.0
        vendors_list.append(grp)

    vendors_list.sort(key=lambda x: x["total_spend"], reverse=True)

    # Build side-by-side price comparison list with cheapest flagged
    price_comparisons = []
    for item_key, offers in sorted(all_items_catalog.items()):
        positive_offers = [o for o in offers if o["effective_unit_price"] > 0]
        min_eff = min((o["effective_unit_price"] for o in positive_offers), default=0.0)
        max_eff = max((o["effective_unit_price"] for o in positive_offers), default=0.0)

        # Group by vendor + price so every distinct vendor & price tier is shown side by side
        seen_keys = set()
        dedup_offers = []
        for o in sorted(offers, key=lambda x: (x["effective_unit_price"], x["invoice_date"])):
            k = (o["vendor_name"], o["unit_price"], o["price_unit"])
            if k in seen_keys:
                continue
            seen_keys.add(k)
            o_copy = {
                **o,
                "is_cheapest": (o["effective_unit_price"] > 0 and abs(o["effective_unit_price"] - min_eff) < 1e-6),
                "price_spread_pct": round(((o["effective_unit_price"] - min_eff) / min_eff) * 100.0, 1) if min_eff > 0 else 0.0,
            }
            dedup_offers.append(o_copy)

        price_comparisons.append({
            "item_key": item_key,
            "article_number": offers[0]["article_number"],
            "description": offers[0]["description"],
            "unit": offers[0]["unit"],
            "cheapest_effective_price": min_eff,
            "highest_effective_price": max_eff,
            "offers": dedup_offers,
        })

    return {
        "status": "success",
        "count": len(vendors_list),
        "vendors": vendors_list,
        "price_comparisons": price_comparisons,
    }


@app.put("/api/vendors/{vendor_name}")
async def api_update_vendor(
    vendor_name: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Update a vendor's stored master IBAN/BIC/VAT ID/payment terms."""
    user_id = str(current_user.id)
    u_clean = user_id.replace("-", "")
    try:
        body = await request.json()
    except Exception:
        body = {}

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = sqlite3.connect(CHAT_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        u_variants = _user_id_variants(user_id)
        placeholders = ",".join("?" for _ in u_variants)
        row = conn.execute(
            f"SELECT * FROM vendors WHERE user_id IN ({placeholders}) AND lower(vendor_name) = lower(?)",
            [*u_variants, vendor_name],
        ).fetchone()
        if not row:
            conn.execute(
                """
                INSERT INTO vendors (user_id, vendor_name, iban, bic, vat_id, payment_terms, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    u_clean,
                    vendor_name,
                    str(body.get("iban") or ""),
                    str(body.get("bic") or ""),
                    str(body.get("vat_id") or ""),
                    str(body.get("payment_terms") or ""),
                    now,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE vendors SET
                    iban = COALESCE(?, iban),
                    bic = COALESCE(?, bic),
                    vat_id = COALESCE(?, vat_id),
                    payment_terms = COALESCE(?, payment_terms),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    body.get("iban"),
                    body.get("bic"),
                    body.get("vat_id"),
                    body.get("payment_terms"),
                    now,
                    row["id"],
                ),
            )
            # Re-evaluate iban_change_flag on existing invoices for this vendor if IBAN was updated
            if body.get("iban") is not None:
                master_clean = re.sub(r"\s+", "", str(body["iban"])).upper()
                inv_rows = conn.execute(
                    f"SELECT id, vendor_iban FROM invoices WHERE user_id IN ({placeholders}) AND lower(vendor_name) = lower(?)",
                    [*u_variants, vendor_name],
                ).fetchall()
                for ir in inv_rows:
                    inv_iban_clean = re.sub(r"\s+", "", str(ir["vendor_iban"] or "")).upper()
                    flag = 1 if (master_clean and inv_iban_clean and master_clean != inv_iban_clean) else 0
                    conn.execute(
                        "UPDATE invoices SET iban_change_flag = ?, previous_iban = ? WHERE id = ?",
                        (flag, str(body["iban"]) if flag else "", ir["id"]),
                    )
        conn.commit()
        return {"status": "success", "message": f"Lieferantendaten für '{vendor_name}' aktualisiert."}
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Task 8 & Tier 2 - GET /api/dashboard/summary (Cashflow, Skonto, Health, Alerts)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/dashboard/summary")
async def api_dashboard_summary(
    reference_date: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
):
    """
    Task 8 & Tier 2:
    Computes in Python:
    1. Cash flow: what is due this week and this month, with amounts.
    2. Skonto savings available: invoices where paying early saves money, with saving and deadline.
    3. Validation health (Tier 2): percentage passing checks and top failure reasons.
    4. Proactive alerts (Tier 2): approaching due dates, Skonto deadlines, missing VAT IDs,
       IBAN changes, duplicates, and validation errors.
    """
    user_id = str(current_user.id)
    invoices = get_all_invoices(user_id=user_id)

    # Determine effective reference date from the real current date, unless reference_date override is provided.
    today = datetime.date.today()
    if reference_date and re.match(r"^\d{4}-\d{2}-\d{2}$", reference_date):
        try:
            today = datetime.datetime.strptime(reference_date, "%Y-%m-%d").date()
        except Exception:
            pass

    week_end = today + datetime.timedelta(days=7)

    due_this_week_list = []
    due_this_month_list = []
    due_this_week_total = 0.0
    due_this_month_total = 0.0

    skonto_opportunities = []
    skonto_total_savings = 0.0

    proactive_alerts = []
    failure_reasons_counter: Dict[str, int] = {}
    core_pass_count = 0
    full_pass_count = 0

    for inv in invoices:
        inv_num = inv.get("invoice_number", "N/A")
        doc_id = inv.get("doc_id")
        vendor = inv.get("vendor_name", "Unbekannt")
        gross = float(inv.get("total_amount") or 0.0)
        is_paid = str(inv.get("payment_status") or "").lower() in ("paid", "bezahlt")
        is_credit = inv.get("document_type") == "credit_note"

        # Validation health counting
        vr = inv.get("validation_report") or validate_invoice(inv)
        if vr.get("core_math_valid", False):
            core_pass_count += 1
        if vr.get("overall_status") == "PASS":
            full_pass_count += 1

        for chk in vr.get("checks") or []:
            if chk.get("status") == "FAIL":
                key = f"{chk.get('code')}: {chk.get('name')}"
                failure_reasons_counter[key] = failure_reasons_counter.get(key, 0) + 1
                if chk.get("code") != "V9":
                    proactive_alerts.append({
                        "type": "validation_fail",
                        "severity": "high",
                        "invoice_number": inv_num,
                        "doc_id": doc_id,
                        "title": f"Rechenfehler in {inv_num} ({chk.get('code')})",
                        "message": chk.get("message", ""),
                    })

        # Missing VAT ID alert
        if not str(inv.get("vat_id") or "").strip():
            proactive_alerts.append({
                "type": "missing_vat_id",
                "severity": "high",
                "invoice_number": inv_num,
                "doc_id": doc_id,
                "title": f"USt-IdNr. fehlt bei {inv_num}",
                "message": f"Lieferant {vendor}: Keine USt-IdNr. extrahiert.",
            })

        # IBAN change fraud alert (Task 11)
        if int(inv.get("iban_change_flag") or 0) == 1:
            proactive_alerts.append({
                "type": "iban_change",
                "severity": "critical",
                "invoice_number": inv_num,
                "doc_id": doc_id,
                "title": f"BETRUGSWARNUNG: IBAN-Änderung bei {vendor} ({inv_num})",
                "message": f"Neue IBAN {inv.get('vendor_iban')} weicht von gespeicherter IBAN {inv.get('previous_iban')} ab!",
            })

        # Duplicate detection alert (Tier 2)
        if int(inv.get("duplicate_flag") or 0) == 1:
            proactive_alerts.append({
                "type": "duplicate",
                "severity": "high",
                "invoice_number": inv_num,
                "doc_id": doc_id,
                "title": f"Mögliches Duplikat: {inv_num}",
                "message": f"Gleicher Lieferant ({vendor}), gleicher Betrag ({format_eur_de(gross)}) und nahes Belegdatum wie {inv.get('duplicate_of')}.",
            })

        if is_paid or is_credit:
            continue

        # Cash flow: due this week & due this month
        due_iso = parse_german_date(inv.get("due_date"))
        if due_iso and re.match(r"^\d{4}-\d{2}-\d{2}$", due_iso):
            try:
                due_dt = datetime.datetime.strptime(due_iso, "%Y-%m-%d").date()
                item_summary = {
                    "doc_id": doc_id,
                    "invoice_number": inv_num,
                    "vendor_name": vendor,
                    "due_date": due_iso,
                    "due_date_de": format_date_de(due_iso),
                    "amount": gross,
                    "validation_status": inv.get("validation_status", "PENDING"),
                }
                if today <= due_dt <= week_end:
                    due_this_week_list.append(item_summary)
                    due_this_week_total = round(due_this_week_total + gross, 2)
                if due_dt.year == today.year and due_dt.month == today.month:
                    due_this_month_list.append(item_summary)
                    due_this_month_total = round(due_this_month_total + gross, 2)

                days_until_due = (due_dt - today).days
                if days_until_due <= 7:
                    proactive_alerts.append({
                        "type": "due_soon",
                        "severity": "medium" if days_until_due >= 0 else "high",
                        "invoice_number": inv_num,
                        "doc_id": doc_id,
                        "title": f"Fälligkeit am {format_date_de(due_iso)}: {inv_num}",
                        "message": f"{vendor}: {format_eur_de(gross)} fällig am {format_date_de(due_iso)}.",
                    })
            except Exception:
                pass

        # Skonto savings available
        skonto_amt = float(inv.get("skonto_amount") or 0.0)
        skonto_pct = float(inv.get("skonto_percent") or 0.0)
        if skonto_amt <= 0 and skonto_pct > 0 and gross > 0:
            skonto_amt = round(gross * (skonto_pct / 100.0), 2)

        if skonto_amt > 0:
            sk_dl = inv.get("skonto_deadline") or ""
            skonto_opportunities.append({
                "doc_id": doc_id,
                "invoice_number": inv_num,
                "vendor_name": vendor,
                "gross_amount": gross,
                "skonto_percent": skonto_pct,
                "skonto_saving": round(skonto_amt, 2),
                "net_payable": round(gross - skonto_amt, 2),
                "skonto_deadline": sk_dl,
                "skonto_deadline_de": format_date_de(sk_dl) or sk_dl,
                "skonto_terms": inv.get("skonto_terms") or "",
            })
            skonto_total_savings = round(skonto_total_savings + skonto_amt, 2)
            proactive_alerts.append({
                "type": "skonto",
                "severity": "info",
                "invoice_number": inv_num,
                "doc_id": doc_id,
                "title": f"Skonto-Potenzial {format_eur_de(skonto_amt)} ({skonto_pct:g}%) bei {inv_num}",
                "message": f"Zahlung bis {format_date_de(sk_dl) or sk_dl} spart {format_eur_de(skonto_amt)} (Zahlbetrag: {format_eur_de(gross - skonto_amt)}).",
            })

    total_inv = len(invoices)
    core_health_pct = round((core_pass_count / total_inv) * 100.0, 1) if total_inv else 100.0
    full_health_pct = round((full_pass_count / total_inv) * 100.0, 1) if total_inv else 100.0

    top_failures = [
        {"reason": k, "count": v}
        for k, v in sorted(failure_reasons_counter.items(), key=lambda x: x[1], reverse=True)
    ]

    return {
        "status": "success",
        "reference_date": today.isoformat(),
        "cashflow": {
            "due_this_week": {
                "count": len(due_this_week_list),
                "total_amount": due_this_week_total,
                "invoices": due_this_week_list,
            },
            "due_this_month": {
                "count": len(due_this_month_list),
                "total_amount": due_this_month_total,
                "invoices": due_this_month_list,
            },
        },
        "skonto": {
            "count": len(skonto_opportunities),
            "total_savings": skonto_total_savings,
            "opportunities": skonto_opportunities,
        },
        "validation_health": {
            "total_invoices": total_inv,
            "core_pass_count": core_pass_count,
            "core_pass_rate_pct": core_health_pct,
            "full_pass_count": full_pass_count,
            "full_pass_rate_pct": full_health_pct,
            "top_failure_reasons": top_failures,
        },
        "proactive_alerts": proactive_alerts,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Task 9 - POST /api/vies/check (On-demand EU VIES SOAP VAT Check)
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/vies/check")
async def api_check_vies(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    user_id = str(current_user.id)
    try:
        body = await request.json()
    except Exception:
        body = {}

    vat_id = str(body.get("vat_id") or "").strip()
    doc_id = str(body.get("doc_id") or "").strip()

    if not vat_id and doc_id:
        inv = get_invoice_by_identifier(doc_id, user_id=user_id)
        if inv:
            vat_id = inv.get("vat_id") or ""

    res = await asyncio.to_thread(check_vat_vies, vat_id)

    if doc_id:
        await asyncio.to_thread(
            update_invoice_record,
            doc_id,
            {"vat_id": vat_id, "recheck_vies": True},
            user_id=user_id,
        )

    return {
        "status": "success",
        "vies": res,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Task 10 - SEPA Payment Run (pain.001.001.03) Preview, Execute, Audit & Download
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/sepa/preview")
async def api_sepa_preview(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    user_id = str(current_user.id)
    try:
        body = await request.json()
    except Exception:
        body = {}

    invoice_ids = body.get("invoice_ids") or []
    apply_skonto = bool(body.get("apply_skonto", False))

    if not isinstance(invoice_ids, list) or not invoice_ids:
        raise HTTPException(status_code=400, detail="Bitte wählen Sie mindestens eine Rechnung für den Zahllauf aus.")

    preview = preview_payment_run(
        invoice_identifiers=invoice_ids,
        user_id=user_id,
        apply_skonto=apply_skonto,
        db_path=CHAT_DB_PATH,
    )
    return {
        "status": "success",
        "preview": preview,
    }


@app.post("/api/sepa/execute")
async def api_sepa_execute(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """
    Task 10: A human must approve the payment run before the file is generated.
    Validates IBAN checksums, blocks only bad-IBAN payments, generates pain.001.001.03 XML,
    marks paid invoices as Paid, and records the audit trail.
    """
    user_id = str(current_user.id)
    try:
        body = await request.json()
    except Exception:
        body = {}

    if not body.get("human_approved"):
        raise HTTPException(
            status_code=400,
            detail="Menschliche Freigabe erforderlich (human_approved=true muss bestätigt sein).",
        )

    invoice_ids = body.get("invoice_ids") or []
    apply_skonto = bool(body.get("apply_skonto", False))
    execution_date = str(body.get("execution_date") or "")

    try:
        run_res = execute_approved_payment_run(
            invoice_identifiers=invoice_ids,
            user_id=user_id,
            approved_by=current_user.email,
            apply_skonto=apply_skonto,
            execution_date=execution_date,
            db_path=CHAT_DB_PATH,
        )
        return {
            "status": "success",
            "message": f"SEPA-Zahllauf '{run_res['file_name']}' für {run_res['tx_count']} Zahlung(en) erstellt.",
            "payment_run": {k: v for k, v in run_res.items() if k != "xml_content"},
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/api/sepa/runs")
async def api_sepa_list_runs(current_user: User = Depends(get_current_user)):
    runs = list_payment_runs(str(current_user.id), db_path=CHAT_DB_PATH)
    return {"status": "success", "count": len(runs), "runs": runs}


@app.get("/api/sepa/runs/{run_id}/download")
async def api_sepa_download_xml(
    run_id: str,
    current_user: User = Depends(get_current_user),
):
    file_name, xml_content = get_payment_run_xml(run_id, str(current_user.id), db_path=CHAT_DB_PATH)
    if not xml_content:
        raise HTTPException(status_code=404, detail="SEPA pain.001 Datei nicht gefunden.")
    return Response(
        content=xml_content.encode("utf-8"),
        media_type="application/xml; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{file_name}"',
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tier 2 - Month-End Checklist, Tax Summary (19%, 7%, 0%) & DATEV Export
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/month-end")
async def api_month_end_and_tax_summary(
    month: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
):
    """
    Tier 2:
    1. Month-end checklist page: unbooked invoices, missing VAT IDs, missing Leitweg-ID,
       failed validations, each as a checkable item the bookkeeper works through.
    2. Tax summary: VAT by rate (19%, 7%, 0%) for the selected month, with net and VAT
       totals, ready to hand to the tax advisor (with DATEV SKR03/SKR04 account codes).
    """
    user_id = str(current_user.id)
    u_clean = user_id.replace("-", "")
    invoices = get_all_invoices(user_id=user_id)
    user_cfg = get_user_settings(user_id, CHAT_DB_PATH)
    skr = user_cfg.get("datev_chart", "SKR03")

    # Available months from invoices
    months_set = set()
    for inv in invoices:
        d_str = str(inv.get("invoice_date") or "")
        if re.match(r"^\d{4}-\d{2}", d_str):
            months_set.add(d_str[:7])
    available_months = sorted(months_set, reverse=True)
    selected_month = month if (month and month != "all") else "all"

    # Load checked states from SQLite `month_end_checks`
    checked_map: Dict[str, bool] = {}
    conn = sqlite3.connect(CHAT_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT item_key, checked FROM month_end_checks WHERE user_id = ? AND month_key = ?",
            (u_clean, selected_month),
        ).fetchall()
        for r in rows:
            checked_map[str(r["item_key"])] = bool(r["checked"])
    finally:
        conn.close()

    unbooked_items = []
    missing_vat_items = []
    missing_leitweg_items = []
    failed_validation_items = []

    # Tax summary accumulators by rate (19, 7, 0)
    tax_by_rate: Dict[int, Dict[str, Any]] = {
        19: {
            "rate": 19,
            "net_total": 0.0,
            "vat_total": 0.0,
            "gross_total": 0.0,
            "invoice_count": 0,
            "datev_account": "1576 (Vorsteuer 19%)" if skr == "SKR03" else "1406 (Vorsteuer 19%)",
            "expense_account": "3400 (Wareneingang 19%)" if skr == "SKR03" else "5400 (Wareneingang 19%)",
            "invoices": [],
        },
        7: {
            "rate": 7,
            "net_total": 0.0,
            "vat_total": 0.0,
            "gross_total": 0.0,
            "invoice_count": 0,
            "datev_account": "1571 (Vorsteuer 7%)" if skr == "SKR03" else "1401 (Vorsteuer 7%)",
            "expense_account": "3300 (Wareneingang 7%)" if skr == "SKR03" else "5300 (Wareneingang 7%)",
            "invoices": [],
        },
        0: {
            "rate": 0,
            "net_total": 0.0,
            "vat_total": 0.0,
            "gross_total": 0.0,
            "invoice_count": 0,
            "datev_account": "1570 (Steuerfrei 0%)" if skr == "SKR03" else "1400 (Steuerfrei 0%)",
            "expense_account": "3200 (Wareneingang 0%)" if skr == "SKR03" else "5200 (Wareneingang 0%)",
            "invoices": [],
        },
    }

    for inv in invoices:
        inv_date = str(inv.get("invoice_date") or "")
        if selected_month != "all" and not inv_date.startswith(selected_month):
            continue

        inv_num = inv.get("invoice_number", "N/A")
        doc_id = inv.get("doc_id")
        vendor = inv.get("vendor_name", "Unbekannt")
        gross = float(inv.get("total_amount") or 0.0)
        vr = inv.get("validation_report") or validate_invoice(inv)

        # 1. Unbooked invoices
        if not int(inv.get("datev_booked") or 0):
            k = f"unbooked:{inv_num}"
            unbooked_items.append({
                "item_key": k,
                "category": "unbooked",
                "doc_id": doc_id,
                "invoice_number": inv_num,
                "vendor_name": vendor,
                "invoice_date": inv_date,
                "amount": gross,
                "label": f"Unverbucht in DATEV ({skr}): {inv_num} - {vendor} ({format_eur_de(gross)})",
                "checked": checked_map.get(k, False),
            })

        # 2. Missing VAT IDs
        if not str(inv.get("vat_id") or "").strip():
            k = f"missing_vat:{inv_num}"
            missing_vat_items.append({
                "item_key": k,
                "category": "missing_vat_id",
                "doc_id": doc_id,
                "invoice_number": inv_num,
                "vendor_name": vendor,
                "amount": gross,
                "label": f"Fehlende USt-IdNr.: {inv_num} ({vendor})",
                "checked": checked_map.get(k, False),
            })

        # 3. Missing Leitweg-ID
        if not str(inv.get("leitweg_id") or "").strip():
            k = f"missing_leitweg:{inv_num}"
            missing_leitweg_items.append({
                "item_key": k,
                "category": "missing_leitweg_id",
                "doc_id": doc_id,
                "invoice_number": inv_num,
                "vendor_name": vendor,
                "amount": gross,
                "label": f"Fehlende Leitweg-ID (BT-10): {inv_num} ({vendor})",
                "checked": checked_map.get(k, False),
            })

        # 4. Failed validations (V1-V8)
        failed_core = [c for c in (vr.get("checks") or []) if c.get("code") != "V9" and c.get("status") == "FAIL"]
        if failed_core:
            k = f"failed_val:{inv_num}"
            reasons = "; ".join(f"{c['code']}: {c['message']}" for c in failed_core)
            failed_validation_items.append({
                "item_key": k,
                "category": "failed_validation",
                "doc_id": doc_id,
                "invoice_number": inv_num,
                "vendor_name": vendor,
                "amount": gross,
                "label": f"Prüffehler in {inv_num}: {reasons}",
                "checked": checked_map.get(k, False),
            })

        # Tax summary aggregation per rate
        vb_list = inv.get("vat_breakdown") or []
        if vb_list:
            for vb in vb_list:
                r_int = int(round(parse_numeric(vb.get("rate"), 19.0)))
                if r_int not in tax_by_rate:
                    r_int = 19
                n_val = parse_numeric(vb.get("net"), 0.0)
                v_val = parse_numeric(vb.get("vat"), 0.0)
                bucket = tax_by_rate[r_int]
                bucket["net_total"] = round(bucket["net_total"] + n_val, 2)
                bucket["vat_total"] = round(bucket["vat_total"] + v_val, 2)
                bucket["gross_total"] = round(bucket["gross_total"] + n_val + v_val, 2)
                bucket["invoice_count"] += 1
                if inv_num not in bucket["invoices"]:
                    bucket["invoices"].append(inv_num)
        else:
            # Fallback to invoice subtotal + tax_amount under 19%
            n_val = float(inv.get("subtotal") or 0.0)
            v_val = float(inv.get("tax_amount") or 0.0)
            bucket = tax_by_rate[19]
            bucket["net_total"] = round(bucket["net_total"] + n_val, 2)
            bucket["vat_total"] = round(bucket["vat_total"] + v_val, 2)
            bucket["gross_total"] = round(bucket["gross_total"] + n_val + v_val, 2)
            bucket["invoice_count"] += 1
            if inv_num not in bucket["invoices"]:
                bucket["invoices"].append(inv_num)

    total_net = round(sum(b["net_total"] for b in tax_by_rate.values()), 2)
    total_vat = round(sum(b["vat_total"] for b in tax_by_rate.values()), 2)
    total_gross = round(sum(b["gross_total"] for b in tax_by_rate.values()), 2)

    return {
        "status": "success",
        "selected_month": selected_month,
        "available_months": available_months,
        "datev_chart": skr,
        "checklist": {
            "unbooked": unbooked_items,
            "missing_vat_id": missing_vat_items,
            "missing_leitweg_id": missing_leitweg_items,
            "failed_validation": failed_validation_items,
        },
        "tax_summary": {
            "rates": [tax_by_rate[19], tax_by_rate[7], tax_by_rate[0]],
            "total_net": total_net,
            "total_vat": total_vat,
            "total_gross": total_gross,
        },
    }


@app.post("/api/month-end/check")
async def api_toggle_month_end_check(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    user_id = str(current_user.id)
    u_clean = user_id.replace("-", "")
    try:
        body = await request.json()
    except Exception:
        body = {}

    month_key = str(body.get("month") or "all")
    item_key = str(body.get("item_key") or "").strip()
    checked = 1 if body.get("checked") else 0
    if not item_key:
        raise HTTPException(status_code=400, detail="item_key is required")

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = sqlite3.connect(CHAT_DB_PATH, check_same_thread=False)
    try:
        conn.execute(
            """
            INSERT INTO month_end_checks (user_id, month_key, item_key, checked, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, month_key, item_key) DO UPDATE SET
                checked = excluded.checked,
                updated_at = excluded.updated_at
            """,
            (u_clean, month_key, item_key, checked, now),
        )
        # If bookkeeper checks an 'unbooked:<inv_num>' item and passes mark_booked=true, update datev_booked
        if body.get("mark_booked") and item_key.startswith("unbooked:"):
            inv_num = item_key.split(":", 1)[1]
            u_variants = _user_id_variants(user_id)
            placeholders = ",".join("?" for _ in u_variants)
            conn.execute(
                f"UPDATE invoices SET datev_booked = ? WHERE invoice_number = ? AND user_id IN ({placeholders})",
                [checked, inv_num, *u_variants],
            )
        conn.commit()
    finally:
        conn.close()

    return {"status": "success", "item_key": item_key, "checked": bool(checked)}


@app.get("/api/datev/export")
async def api_export_datev_csv(
    month: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
):
    """Export invoices as a DATEV Buchungsstapel CSV (SKR03 / SKR04) for the tax advisor."""
    user_id = str(current_user.id)
    invoices = get_all_invoices(user_id=user_id)
    cfg = get_user_settings(user_id, CHAT_DB_PATH)
    skr = cfg.get("datev_chart", "SKR03")

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        "Umsatz (ohne Soll/Haben-Kz)",
        "Soll/Haben-Kennzeichen",
        "WKZ Umsatz",
        "Konto",
        "Gegenkonto (ohne BU-Schlüssel)",
        "BU-Schlüssel",
        "Belegdatum",
        "Belegfeld 1",
        "Buchungstext",
        "USt-IdNr. Lieferant",
        "Kontenrahmen",
    ])

    for inv in invoices:
        inv_date = str(inv.get("invoice_date") or "")
        if month and month != "all" and not inv_date.startswith(month):
            continue
        gross = float(inv.get("total_amount") or 0.0)
        sh = "H" if gross >= 0 else "S"
        abs_gross_de = f"{abs(gross):.2f}".replace(".", ",")
        # Determine primary VAT rate for account selection
        vb = inv.get("vat_breakdown") or []
        has_7 = any(int(round(parse_numeric(v.get("rate"), 0))) == 7 for v in vb)
        if skr == "SKR04":
            konto = "5300" if has_7 else "5400"
        else:
            konto = "3300" if has_7 else "3400"
        gegenkonto = "70000"
        bu_key = "8" if has_7 else "9"
        belegdatum = format_date_de(inv_date) or inv_date
        writer.writerow([
            abs_gross_de,
            sh,
            inv.get("currency", "EUR"),
            konto,
            gegenkonto,
            bu_key,
            belegdatum,
            inv.get("invoice_number", ""),
            f"{inv.get('vendor_name', '')[:40]}",
            inv.get("vat_id", ""),
            skr,
        ])

    csv_bytes = output.getvalue().encode("utf-8-sig")
    filename = f"DATEV_Buchungsstapel_{skr}_{month or 'alle'}.csv"
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Existing Endpoints 6-11 (Excel Reports, Chat History, Custom Dashboard Charts)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/report/download/{filename}")
async def download_report(
    filename: str,
    current_user: User = Depends(get_current_user),
):
    file_path = safe_path(filename, REPORTS_DIR)
    if not file_path:
        raise HTTPException(status_code=400, detail="Invalid file path.")

    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Report file not found.")

    return FileResponse(
        path=file_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=os.path.basename(file_path),
    )


@app.get("/api/chat/history/{conversation_id}")
async def chat_history(
    conversation_id: str,
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
):
    user_id = str(current_user.id)
    try:
        messages = get_messages(
            conversation_id=conversation_id,
            user_id=user_id,
            limit=limit,
            db_path=CHAT_DB_PATH,
        )
        for m in messages:
            if m.get("role") == "assistant" and m.get("content"):
                m["content"] = _drop_dead_chart_links(m["content"])
        return {
            "status": "success",
            "conversation_id": conversation_id,
            "messages": messages,
            "count": len(messages),
        }
    except Exception as exc:
        logger.error("Chat history error conv=%s: %s", conversation_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.")


@app.get("/api/chat/conversations")
async def list_user_conversations(
    current_user: User = Depends(get_current_user),
):
    user_id = str(current_user.id)
    try:
        convs = list_conversations(user_id=user_id, db_path=CHAT_DB_PATH)
        return {
            "status": "success",
            "user_id": user_id,
            "conversations": convs,
            "count": len(convs),
        }
    except Exception as exc:
        logger.error("List conversations error user=%s: %s", user_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.")


@app.get("/api/dashboard/charts")
async def get_user_dashboard_charts(
    current_user: User = Depends(get_current_user),
):
    user_id = str(current_user.id)
    try:
        charts = list_dashboard_charts(user_id=user_id, db_path=CHAT_DB_PATH)
        return {"status": "success", "charts": charts, "count": len(charts)}
    except Exception as exc:
        logger.error("Failed to list dashboard charts for user %s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.")


@app.post("/api/dashboard/charts")
async def create_user_dashboard_chart(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    user_id = str(current_user.id)
    try:
        data = await request.json()
    except Exception:
        data = {}

    prompt = str(data.get("prompt") or "").strip()
    title = str(data.get("title") or "").strip()

    if not prompt:
        raise HTTPException(status_code=400, detail="'prompt' is required and cannot be empty")
    if len(prompt) > 1000:
        raise HTTPException(status_code=400, detail="'prompt' must be 1000 characters or fewer")

    try:
        agent = GraphAgent()
        agent.user_id = user_id
        result = await asyncio.to_thread(agent.run, prompt)
        status_val = result.get("status")
        chart_paths = result.get("chart_paths") or []

        if status_val != "success" or not chart_paths:
            error_msg = result.get("message") or result.get("error") or "Failed to generate visualization from prompt."
            return JSONResponse(
                status_code=422,
                content={"status": "error", "error": error_msg},
            )

        if not title:
            title = prompt[:50].title()

        saved = add_dashboard_chart(
            user_id=user_id,
            title=title,
            prompt=prompt,
            chart_path=chart_paths[0],
            db_path=CHAT_DB_PATH,
        )
        return {"status": "success", "chart": saved}
    except Exception as exc:
        logger.error("Error creating dashboard chart user=%s: %s", user_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Chart generation failed. Please try again.")


@app.delete("/api/dashboard/charts/{chart_id}")
async def delete_user_dashboard_chart(
    chart_id: str,
    current_user: User = Depends(get_current_user),
):
    user_id = str(current_user.id)
    try:
        deleted = delete_dashboard_chart(chart_id=chart_id, user_id=user_id, db_path=CHAT_DB_PATH)
        if not deleted:
            raise HTTPException(status_code=404, detail="Chart not found or does not belong to user")
        return {"status": "success", "message": "Chart deleted successfully", "chart_id": chart_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error deleting dashboard chart %s: %s", chart_id, exc)
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=5000, reload=False)
