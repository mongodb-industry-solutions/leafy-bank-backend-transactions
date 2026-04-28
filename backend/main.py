import json
import logging
import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from api_models import (
    PaymentOrderInitiateRequest,
    PaymentOrderRetrieveRequest,
)
from database.connection import MongoDBConnection
from encoder.json_encoder import MyJSONEncoder
from services.payments_service import PaymentsService
from services.transactions_service import TransactionsService
from shared import registry

load_dotenv()

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("LEAFYBANK_DB_NAME", "leafy_bank_bian")
PAYMENT_LIMIT_USD = float(os.getenv("PAYMENT_LIMIT_USD", "500"))

app = FastAPI(title="Leafy Bank — Payments (BIAN PaymentOrderProcedure)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

connection = MongoDBConnection(MONGODB_URI)
payments_service = PaymentsService(connection, DB_NAME, PAYMENT_LIMIT_USD)
transactions_service = TransactionsService(connection, DB_NAME)


def _bian_response(envelope: dict) -> Response:
    return Response(
        content=json.dumps(envelope, cls=MyJSONEncoder),
        media_type="application/json",
    )


@app.get("/")
async def read_root():
    return {
        "service": "leafy-bank-payments",
        "bian": "PaymentOrderProcedure",
        "bianVersion": registry.bian_version,
    }


@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.post("/PaymentOrderProcedure/Initiate")
async def payment_order_procedure_initiate(
    body: PaymentOrderInitiateRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """BIAN PaymentOrderProcedure / Initiate.

    Boundary validation handled by `PaymentOrderInitiateRequest`. Translation to
    camelCase storage keys handled by `registry.to_alias("payments", ...)`.
    """
    try:
        alias_body = registry.to_alias("payments", body.model_dump(exclude_none=True))
        debtor = alias_body.get("debtor") or {}
        creditor = alias_body.get("creditor") or {}
        remittance = alias_body.get("remittance") or {}

        payment_doc = payments_service.initiate_payment(
            customer_ref=alias_body["customerId"],
            debtor_account_ref=debtor["accountId"],
            creditor_account_ref=creditor["accountId"],
            instructed_amount=alias_body["instructedAmount"],
            instructed_currency=alias_body["instructedCurrency"],
            payment_type=alias_body["type"],
            payment_rail=alias_body["rail"],
            remittance_unstructured=remittance.get("unstructured"),
            idempotency_key=idempotency_key,
        )
        return _bian_response({
            "PaymentOrderReference": payment_doc["paymentId"],
            "PaymentApexStatus": payment_doc["status"],
            "PaymentOrderRecord": registry.to_bian("payments", payment_doc),
        })
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.error("PaymentOrderProcedure/Initiate failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal payment processing error.")


@app.post("/PaymentOrderProcedure/Retrieve")
async def payment_order_procedure_retrieve(body: PaymentOrderRetrieveRequest):
    """BIAN PaymentOrderProcedure / Retrieve — payment order plus its ledger legs."""
    try:
        payment = payments_service.retrieve_payment(body.PaymentOrderReference)
        if not payment:
            raise HTTPException(status_code=404, detail="PaymentOrderReference not found.")

        legs = payment.pop("_ledgerLegs", [])
        return _bian_response({
            "PaymentOrderReference": payment["paymentId"],
            "PaymentOrderRecord": registry.to_bian("payments", payment),
            "CurrentAccountPaymentTransactionRecord": [
                registry.to_bian("transactions", leg) for leg in legs
            ],
        })
    except HTTPException:
        raise
    except Exception as e:
        logging.error("PaymentOrderProcedure/Retrieve failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal retrieve error.")
