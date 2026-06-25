import logging
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from pymongo.client_session import ClientSession
from pymongo.errors import DuplicateKeyError

from database.connection import MongoDBConnection
from shared.refs import derive_ref

logger = logging.getLogger(__name__)

# v6: payments.debtor.accountType / creditor.accountType share the same enum (title-case).
_ACCOUNT_TYPE_DISPLAY = {
    "CURRENT": "Current",
    "SAVINGS": "Savings",
    "CHECKING": "Checking",
    "FIXED_DEPOSIT": "FixedDeposit",
}


def _display_account_type(account_type: Optional[str]) -> Optional[str]:
    if not account_type:
        return None
    return _ACCOUNT_TYPE_DISPLAY.get(account_type)


class PaymentsService:
    """`initiate_payment` inserts the payment order (status PENDING) OUTSIDE the transaction, then
    performs the money move as ONE multi-document ACID transaction on `leafy_bank_bian`: debtor
    balance update, creditor balance update, ONE `transactions` doc (payer/payee, v4_21 — no legs,
    no GL), payment status flip to SETTLED, and a sender-side notification insert.
    """

    def __init__(self, connection: MongoDBConnection, db_name: str, payment_limit_usd: float):
        self.db = connection.get_database(db_name)
        self.customers = self.db["customers"]
        self.accounts = self.db["accounts"]
        self.payments = self.db["payments"]
        self.transactions = self.db["transactions"]
        self.notifications = self.db["notifications"]
        self.payment_limit_usd = payment_limit_usd

    def initiate_payment(
        self,
        customer_ref: str,
        debtor_account_ref: str,
        creditor_account_ref: str,
        instructed_amount: float,
        instructed_currency: str,
        payment_type: str,
        payment_rail: str,
        remittance_unstructured: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """Initiate a payment order. Returns the persisted payment document.

        Raises ValueError on validation failures; caller maps to HTTP 400.
        """
        if instructed_amount <= 0:
            raise ValueError("PaymentInstructedAmount must be greater than 0.")
        if instructed_amount > self.payment_limit_usd:
            raise ValueError(
                f"PaymentInstructedAmount exceeds the limit of {self.payment_limit_usd}."
            )

        if idempotency_key:
            existing = self.payments.find_one({"endToEndId": idempotency_key})
            if existing:
                logger.info(
                    "Idempotent replay for endToEndId=%s — returning existing paymentId=%s",
                    idempotency_key,
                    existing["paymentId"],
                )
                return existing

        debtor_account = self.accounts.find_one({"accountId": debtor_account_ref})
        if not debtor_account:
            raise ValueError(f"Debtor account {debtor_account_ref} not found.")
        creditor_account = self.accounts.find_one({"accountId": creditor_account_ref})
        if not creditor_account:
            raise ValueError(f"Creditor account {creditor_account_ref} not found.")

        if debtor_account["status"] == "CLOSED":
            raise ValueError("Debtor account is CLOSED.")
        if creditor_account["status"] == "CLOSED":
            raise ValueError("Creditor account is CLOSED.")
        if debtor_account_ref == creditor_account_ref:
            raise ValueError("Debtor and creditor accounts must differ.")

        debtor_currency = debtor_account.get("currency")
        creditor_currency = creditor_account.get("currency")
        if debtor_currency != creditor_currency or debtor_currency != instructed_currency:
            raise ValueError("Currency mismatch — FX is out of scope for Phase 1.")

        available = debtor_account.get("balance", {}).get("available", 0)
        if available < instructed_amount:
            raise ValueError("Insufficient available balance in debtor account.")

        # v6: customer FK lives at customerSnapshot.customerId (top-level customerId removed).
        debtor_customer_id = debtor_account["customerSnapshot"]["customerId"]
        creditor_customer_id = creditor_account["customerSnapshot"]["customerId"]
        if debtor_customer_id != customer_ref:
            raise ValueError(
                f"Debtor account {debtor_account_ref} is not owned by {customer_ref}."
            )
        debtor_customer = self.customers.find_one({"customerId": debtor_customer_id})
        creditor_customer = self.customers.find_one({"customerId": creditor_customer_id})
        if not debtor_customer or not creditor_customer:
            raise ValueError("Customer reference data missing for debtor or creditor.")

        is_internal = debtor_customer_id == creditor_customer_id

        payment_oid = ObjectId()
        payment_id = derive_ref("PAY", payment_oid)
        end_to_end_id = idempotency_key or derive_ref("E2E", payment_oid, last_n=12)
        # ISO 20022 BankTransactionCode derived from rail.
        txn_code = "PMNT-ICDT-BOOK" if payment_rail == "INTERNAL" else "PMNT-ICDT-ESCT"

        now = datetime.now(timezone.utc)

        # Payment initiation is outside the ACID transaction — it records the order before settlement.
        payment_doc = {
            "_id": payment_oid,
            "paymentId": payment_id,
            "endToEndId": end_to_end_id,
            "instructionId": derive_ref("INSTR", payment_oid),
            "txnId": derive_ref("TXN", payment_oid),
            "uetr": f"UETR-{str(payment_oid)}",
            "msgId": derive_ref("MSG", payment_oid),
            "customerId": debtor_customer_id,
            "initiatedAt": now,
            "type": "CREDIT_TRANSFER",
            "rail": payment_rail,
            "status": "PENDING",
            "priority": "NORMAL",
            "instructedAmount": instructed_amount,
            "instructedCurrency": instructed_currency,
            "amount": instructed_amount,
            "currency": instructed_currency,
            "chargeBearer": "SLEV",
            "fees": [],
            "debtor": _party_snapshot(debtor_customer, debtor_account),
            "creditor": _party_snapshot(creditor_customer, creditor_account),
            "remittance": {
                "unstructured": remittance_unstructured,
                "reference": None,
                "invoiceNo": None,
                "purposeCode": None,
            },
            "correspondent": {
                "sanctionsCheck": {
                    "status": "CLEAR",
                    "checkedAt": now,
                    "provider": "PROV-SYNTH",
                }
            },
            "cardTxn": None,
            "rtp": None,
            "clearing": {
                "receivedAt": now,
                "validatedAt": now,
                "authorisedAt": now,
                "submittedAt": now,
                "settledAt": None,
            },
            "fraud": {"score": 5, "decision": "APPROVED"},
            "initiation": {
                "initiatedAt": now,
                "initiatedBy": debtor_customer_id,
                "channel": "API",
                "ipAddress": None,
                "deviceId": None,
            },
            "isInternal": is_internal,
            "createdAt": now,
            "updatedAt": now,
            "createdBy": "SERVICE-PAYMENTS",
            "version": 1,
            "sourceSystem": "leafy-bank-payments-service",
        }
        try:
            self.payments.insert_one(payment_doc)
        except DuplicateKeyError:
            raise ValueError(
                f"Idempotency-Key {end_to_end_id} already exists with a different payment."
            )

        def callback(session: ClientSession) -> dict:
            debtor_after = self.accounts.find_one_and_update(
                {"accountId": debtor_account_ref},
                {
                    "$inc": {
                        "balance.current": -instructed_amount,
                        "balance.available": -instructed_amount,
                        "balance.ledger": -instructed_amount,
                    },
                    "$set": {"balance.updatedAt": now, "updatedAt": now},
                },
                session=session,
                return_document=True,
            )
            self.accounts.find_one_and_update(
                {"accountId": creditor_account_ref},
                {
                    "$inc": {
                        "balance.current": instructed_amount,
                        "balance.available": instructed_amount,
                        "balance.ledger": instructed_amount,
                    },
                    "$set": {"balance.updatedAt": now, "updatedAt": now},
                },
                session=session,
                return_document=True,
            )

            txn_doc = _transaction_doc(
                payment_oid=payment_oid,
                payment_id=payment_id,
                debtor_account=debtor_account,
                debtor_customer=debtor_customer,
                creditor_account=creditor_account,
                creditor_customer=creditor_customer,
                debtor_after=debtor_after,
                amount=instructed_amount,
                currency=instructed_currency,
                payment_rail=payment_rail,
                txn_code=txn_code,
                is_internal=is_internal,
                now=now,
            )
            self.transactions.insert_one(txn_doc, session=session)

            notif_docs = _build_notifications(
                payment_oid=payment_oid,
                payment_id=payment_id,
                txn_id=txn_doc["txnId"],
                debtor_account=debtor_account,
                creditor_account=creditor_account,
                debtor_customer=debtor_customer,
                debtor_after=debtor_after,
                amount=instructed_amount,
                currency=instructed_currency,
                payment_rail=payment_rail,
                is_internal=is_internal,
                now=now,
            )
            if notif_docs:
                self.notifications.insert_many(notif_docs, session=session)

            self.payments.update_one(
                {"_id": payment_oid},
                {"$set": {"status": "SETTLED", "clearing.settledAt": now, "updatedAt": now}},
                session=session,
            )

            return self.payments.find_one({"_id": payment_oid}, session=session)

        with self.db.client.start_session() as session:
            return session.with_transaction(callback)

    def retrieve_payment(self, payment_ref: str) -> Optional[dict]:
        """Retrieve a payment plus its single transaction doc (v4_21)."""
        payment = self.payments.find_one({"paymentId": payment_ref})
        if not payment:
            return None
        payment["_txn"] = self.transactions.find_one({"paymentId": payment_ref})
        return payment


def _party_snapshot(customer: dict, account: dict) -> dict:
    identification = customer.get("identification", {}) or {}
    return {
        "accountId": account["accountId"],
        "accountNo": account.get("accountNumber"),
        "iban": account.get("iban"),
        "name": identification.get("legalName"),
        "bic": "LEAFUS33",
        "address": (customer.get("contact", {}) or {}).get("addresses", []),
        "accountType": _display_account_type(account.get("type")),
    }


def _transaction_doc(
    *,
    payment_oid: ObjectId,
    payment_id: str,
    debtor_account: dict,
    debtor_customer: dict,
    creditor_account: dict,
    creditor_customer: dict,
    debtor_after: dict,
    amount: float,
    currency: str,
    payment_rail: str,
    txn_code: str,
    is_internal: bool,
    now: datetime,
) -> dict:
    """One v4_21 transactions doc: the confirmed payer->payee movement. NOT an accounting record
    (no legs, no gl) — the ledger service derives DR/CR ledgerEvents from this via CDC."""
    debtor_name = (debtor_customer.get("identification") or {}).get("legalName")
    creditor_name = (creditor_customer.get("identification") or {}).get("legalName")
    return {
        "_id": ObjectId(),
        "txnId": derive_ref("TXN", payment_oid),
        "paymentId": payment_id,
        "bankRef": f"LEAFY-BOOK-{payment_id.split('-', 1)[-1]}",
        "rail": payment_rail,
        "paymentType": "CREDIT_TRANSFER",
        "direction": "OUTGOING",
        "txnCode": txn_code,
        "amount": amount,
        "currency": currency,
        "baseAmount": amount,
        "valueDate": now.date().isoformat(),
        "bookingDate": now.date().isoformat(),
        "description": f"Transfer to {creditor_account.get('accountNumber')}",
        "balanceAfter": (debtor_after.get("balance", {}) or {}).get("current"),
        "channel": "API",
        "payer": {
            "accountId": debtor_account["accountId"],
            "accountNo": debtor_account.get("accountNumber"),
            "name": debtor_name,
            "bic": "LEAFUS33",
            "country": "US",
            "isInternal": True,
        },
        "payee": {
            "accountId": creditor_account["accountId"],
            "accountNo": creditor_account.get("accountNumber"),
            "name": creditor_name,
            "bic": "LEAFUS33",
            "country": "US",
            "isInternal": is_internal,
        },
        "transactionCategory": "AccountTransfer",
        "isReversed": False,
        "reversalTxnId": None,
        "transactionDates": [
            {"date": now, "type": "TransactionInitiatedDate"},
            {"date": now, "type": "TransactionCompletedDate"},
        ],
        "transactionStatus": "Completed",
        "isCompleted": True,
        "isNotified": False,
        "createdAt": now,
        "createdBy": "SERVICE-PAYMENTS",
        "sourceSystem": "leafy-bank-payments-service",
    }


def _build_notifications(
    *,
    payment_oid: ObjectId,
    payment_id: str,
    txn_id: str,
    debtor_account: dict,
    creditor_account: dict,
    debtor_customer: dict,
    debtor_after: dict,
    amount: float,
    currency: str,
    payment_rail: str,
    is_internal: bool,
    now: datetime,
) -> list[dict]:
    """Build the sender-side notification for a payment.

    Leafy Bank UX: only the debtor (sender) receives a notification. Always returns exactly
    one document. `txn_id` is the single v4_21 transaction doc's txnId.
    """
    debtor_balance = (debtor_after.get("balance", {}) or {}).get("current")
    creditor_name = creditor_account.get("accountNumber") or creditor_account.get("accountId")

    if is_internal:
        event_type = "InternalTransfer"
        message = (
            f"You transferred {currency} {amount} between your accounts. "
            f"New balance on {debtor_account['accountId']}: {currency} {debtor_balance}."
        )
    elif payment_rail == "INTERNAL":
        event_type = "TransferSent"
        message = (
            f"You sent {currency} {amount} to {creditor_name}. "
            f"New balance: {currency} {debtor_balance}."
        )
    else:
        event_type = "PaymentMade"
        message = (
            f"You paid {currency} {amount} to {creditor_name}. "
            f"New balance: {currency} {debtor_balance}."
        )

    notif_oid = ObjectId()
    return [
        {
            "_id": notif_oid,
            "notificationId": derive_ref("NOTIF", notif_oid),
            "eventType": event_type,
            "message": message,
            "notificationDate": now,
            "recipient": {"customerId": debtor_customer["customerId"]},
            "transactionId": txn_id,
            "paymentId": payment_id,
            "accounts": {
                "senderAccountId": debtor_account["accountId"],
                "receiverAccountId": creditor_account["accountId"],
            },
            "createdAt": now,
            "createdBy": "SERVICE-PAYMENTS",
            "sourceSystem": "leafy-bank-payments-service",
        }
    ]
