from bson import ObjectId
from database.connection import MongoDBConnection
from services.transactions_service import TransactionsService

import logging
import os
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def perform_account_transfer(transactions_service: TransactionsService):
    """Perform an account transfer transaction."""
    try:
        success = transactions_service.perform_transaction(
            account_id_receiver="674dc33d4473ad1e1d4e02d1",  # Example ObjectId for receiver
            account_id_sender="674dc33d4473ad1e1d4e02d0",  # Example ObjectId for sender
            transaction_amount=50.0,
            sender_user_id="65a546ae4a8f64e8f88fb89e",  # Example ObjectId for sender user
            sender_user_name="fridaklo",
            sender_account_number="1234567890",
            sender_account_type="Checking",
            receiver_user_id="65a546ae4a8f64e8f88fb89e",  # Example ObjectId for receiver user
            receiver_user_name="fridaklo",
            receiver_account_number="9876543210",
            receiver_account_type="Savings",
            transaction_type="AccountTransfer"
        )
        if success:
            logging.info("Account transfer transaction completed successfully.")
        else:
            logging.error("Account transfer transaction failed.")
    except Exception as e:
        logging.error(f"Failed to perform account transfer: {e}")

def perform_digital_payment(transactions_service: TransactionsService):
    """Perform a digital payment transaction."""
    try:
        success = transactions_service.perform_transaction(
            account_id_receiver="674eee8aeba89055c8ef3617",  # Example ObjectId for receiver
            account_id_sender="674ee14f3dc463acd6eb5633",  # Example ObjectId for sender
            transaction_amount=50.0,
            sender_user_id="671ff2451ec726b417352703",  # Example ObjectId for sender user
            sender_user_name="claumon",
            sender_account_number="321321321",
            sender_account_type="Savings",
            receiver_user_id="66fe219d625d93a100528224",  # Example ObjectId for receiver user
            receiver_user_name="gracehop",
            receiver_account_number="765438213",
            receiver_account_type="Savings",
            transaction_type="DigitalPayment",
            payment_method="Zelle"
        )
        if success:
            logging.info("Digital payment transaction completed successfully.")
        else:
            logging.error("Digital payment transaction failed.")
    except Exception as e:
        logging.error(f"Failed to perform digital payment: {e}")

def main():
    # Initialize the MongoDB connection
    uri = MONGODB_URI
    db_name = "leafy_bank"
    connection = MongoDBConnection(uri)

    # Initialize the TransactionsService
    transactions_service = TransactionsService(connection, db_name)

    # Perform an account transfer
    perform_account_transfer(transactions_service)

    # Perform a digital payment
    perform_digital_payment(transactions_service)

if __name__ == "__main__":
    main()
