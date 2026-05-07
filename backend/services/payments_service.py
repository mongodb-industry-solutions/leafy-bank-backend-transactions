import logging
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from pymongo.client_session import ClientSession
from pymongo.errors import DuplicateKeyError

from database.connection import MongoDBConnection
from shared.refs import derive_ref

logger = logging.getLogger(__name__)

# v6: payments.debtor.accountType / creditor.accountType / transactions.counterparty.accountType
# all share the same enum (title-case). Internal accounts.type is upper-case.
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
    """Write path for the BIAN PaymentOrderProcedure service domain.

    Each `initiate_payment` call performs a 5-write multi-document ACID transaction on
    `leafy_bank_bian`: debtor balance update, creditor balance update, payments insert,
    two ledger-leg inserts (DEBIT + CREDIT) into `transactions`, and notification inserts.
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
            raise ValueError(
                "Currency mismatch — FX is out of scope for Phase 1."
            )

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
        # ISO 20022 BankTransactionCode derived from rail. Phase 1 ships INTERNAL only
        # (book transfer between two accounts on the same ledger → PMNT-ICDT-BOOK).
        txn_code = "PMNT-ICDT-BOOK" if payment_rail == "INTERNAL" else "PMNT-ICDT-ESCT"

        def callback(session: ClientSession) -> dict:
            now = datetime.now(timezone.utc)

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
            creditor_after = self.accounts.find_one_and_update(
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
                "status": "RECEIVED",
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
                self.payments.insert_one(payment_doc, session=session)
            except DuplicateKeyError:
                raise ValueError(
                    f"Idempotency-Key {end_to_end_id} already exists with a different payment."
                )

            debit_leg = _ledger_leg(
                payment_oid=payment_oid,
                payment_id=payment_id,
                leg="DEBIT",
                account=debtor_account,
                account_after=debtor_after,
                counterparty=creditor_account,
                counterparty_customer=creditor_customer,
                amount=instructed_amount,
                currency=instructed_currency,
                txn_code=txn_code,
                description=f"Transfer to {creditor_account.get('accountNumber')}",
                now=now,
            )
            credit_leg = _ledger_leg(
                payment_oid=payment_oid,
                payment_id=payment_id,
                leg="CREDIT",
                account=creditor_account,
                account_after=creditor_after,
                counterparty=debtor_account,
                counterparty_customer=debtor_customer,
                amount=instructed_amount,
                currency=instructed_currency,
                txn_code=txn_code,
                description=f"Transfer from {debtor_account.get('accountNumber')}",
                now=now,
            )
            self.transactions.insert_many([debit_leg, credit_leg], session=session)

            self.payments.update_one(
                {"_id": payment_oid},
                {"$set": {"status": "SETTLED", "clearing.settledAt": now, "updatedAt": now}},
                session=session,
            )

            notif_docs = _build_notifications(
                payment_oid=payment_oid,
                payment_id=payment_id,
                debit_txn_id=debit_leg["txnId"],
                credit_txn_id=credit_leg["txnId"],
                debtor_account=debtor_account,
                creditor_account=creditor_account,
                debtor_customer=debtor_customer,
                creditor_customer=creditor_customer,
                debtor_after=debtor_after,
                creditor_after=creditor_after,
                amount=instructed_amount,
                currency=instructed_currency,
                payment_rail=payment_rail,
                is_internal=is_internal,
                now=now,
            )
            if notif_docs:
                self.notifications.insert_many(notif_docs, session=session)

            return self.payments.find_one({"_id": payment_oid}, session=session)

        with self.db.client.start_session() as session:
            return session.with_transaction(callback)

    def retrieve_payment(self, payment_ref: str) -> Optional[dict]:
        """Retrieve a payment plus its two ledger legs."""
        payment = self.payments.find_one({"paymentId": payment_ref})
        if not payment:
            return None
        legs = list(self.transactions.find({"paymentId": payment_ref}).sort("type", 1))
        payment["_ledgerLegs"] = legs
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


def _ledger_leg(
    payment_oid: ObjectId,
    payment_id: str,
    leg: str,
    account: dict,
    account_after: dict,
    counterparty: dict,
    counterparty_customer: Optional[dict],
    amount: float,
    currency: str,
    txn_code: str,
    description: str,
    now: datetime,
) -> dict:
    leg_oid = ObjectId()
    identification = ((counterparty_customer or {}).get("identification") or {})
    payment_id_suffix = payment_id.split("-", 1)[1] if "-" in payment_id else payment_id
    return {
        "_id": leg_oid,
        "txnId": f"{derive_ref('TXN', payment_oid)}-{leg}",
        "accountId": account["accountId"],
        "paymentId": payment_id,
        "bankRef": f"LEAFY-BOOK-{payment_id_suffix}",
        "type": leg,
        "txnCode": txn_code,
        "amount": amount,
        "currency": currency,
        "baseAmount": amount,
        "valueDate": now.date().isoformat(),
        "bookingDate": now.date().isoformat(),
        "description": description,
        "balanceAfter": (account_after.get("balance", {}) or {}).get("current"),
        "channel": "API",
        "isReversed": False,
        "reversalTxnId": None,
        "counterparty": {
            "name": identification.get("legalName"),
            "userName": identification.get("userName"),
            "accountNo": counterparty.get("accountNumber"),
            "accountType": _display_account_type(counterparty.get("type")),
            "bic": "LEAFUS33",
            "country": "US",
        },
        "createdAt": now,
        "createdBy": "SERVICE-PAYMENTS",
        "sourceSystem": "leafy-bank-payments-service",
    }


def _build_notifications(
    payment_oid: ObjectId,
    payment_id: str,
    debit_txn_id: str,
    credit_txn_id: str,
    debtor_account: dict,
    creditor_account: dict,
    debtor_customer: dict,
    creditor_customer: dict,
    debtor_after: dict,
    creditor_after: dict,
    amount: float,
    currency: str,
    payment_rail: str,
    is_internal: bool,
    now: datetime,
) -> list[dict]:
    """Build the sender-side notification for a payment.

    Leafy Bank UX: only the logged-in user (= debtor / sender) receives notifications.
    No receiver-side notification is generated, even when the creditor is another
    real customer in the system. Always returns exactly one document.
    """
    del credit_txn_id, creditor_customer, creditor_after  # not used in sender-only model

    debtor_balance = (debtor_after.get("balance", {}) or {}).get("current")
    creditor_name = (creditor_account.get("accountNumber") or creditor_account.get("accountId"))

    notif_oid = ObjectId()
    base = {
        "paymentId": payment_id,
        "accounts": {
            "senderAccountId": debtor_account["accountId"],
            "receiverAccountId": creditor_account["accountId"],
        },
        "createdAt": now,
        "createdBy": "SERVICE-PAYMENTS",
        "sourceSystem": "leafy-bank-payments-service",
    }

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

    return [
        {
            "_id": notif_oid,
            "notificationId": derive_ref("NOTIF", notif_oid),
            "eventType": event_type,
            "message": message,
            "notificationDate": now,
            "recipient": {"customerId": debtor_customer["customerId"]},
            "transactionId": debit_txn_id,
            **base,
        }
    ]
