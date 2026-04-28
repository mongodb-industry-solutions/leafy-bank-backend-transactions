import logging

from database.connection import MongoDBConnection

logger = logging.getLogger(__name__)


class TransactionsService:
    """Read-side helpers for the BIAN CurrentAccountPaymentTransaction service domain.

    Public list/query routes for ledger legs live in the accounts service per plan-v2 § 6
    (`POST /CurrentAccountFulfillmentArrangement/CurrentAccountTransaction/Request`). This
    class is kept thin to support intra-repo read paths (e.g. attaching legs to a payment
    in `PaymentsService.retrieve_payment`).
    """

    def __init__(self, connection: MongoDBConnection, db_name: str):
        self.db = connection.get_database(db_name)
        self.transactions = self.db["transactions"]

    def get_legs_for_payment(self, payment_ref: str) -> list[dict]:
        return list(self.transactions.find({"paymentId": payment_ref}).sort("type", 1))
