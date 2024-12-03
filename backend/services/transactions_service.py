from bson import ObjectId
from pymongo.client_session import ClientSession
from datetime import datetime, timezone
import logging
from database.connection import MongoDBConnection

from typing import Optional

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class TransactionsService:
    """This class provides methods to perform transactions in the database."""

    def __init__(self, connection: MongoDBConnection, db_name: str):
        """Initialize the TransactionsService with the MongoDB connection and database name.

        Args:
            connection (MongoDBConnection): The MongoDB connection instance.
            db_name (str): The name of the database.

        Returns:
            None
        """
        self.db = connection.get_database(db_name)
        self.accounts_collection = self.db['accounts']
        self.transactions_collection = self.db['transactions']
        self.users_collection = self.db['users']
        self.notifications_collection = self.db['notifications']

    def perform_transaction(self, account_id_receiver: str, account_id_sender: str,
                            transaction_amount: float, sender_user_id: str, sender_user_name: str,
                            sender_account_number: str, sender_account_type: str, receiver_user_id: str,
                            receiver_user_name: str, receiver_account_number: str, receiver_account_type: str,
                            transaction_type: str, transaction_description: Optional[str] = "N/A", payment_method: Optional[str] = "N/A") -> bool:
        """Perform a transaction between two accounts.

        Args:
            account_id_receiver (str): The ID of the receiver's account.
            account_id_sender (str): The ID of the sender's account.
            transaction_amount (float): The amount to transfer.
            sender_user_id (str): The ID of the sender user.
            sender_user_name (str): The name of the sender user.
            sender_account_number (str): The account number of the sender.
            sender_account_type (str): The account type of the sender.
            receiver_user_id (str): The ID of the receiver user.
            receiver_user_name (str): The name of the receiver user.
            receiver_account_number (str): The account number of the receiver.
            receiver_account_type (str): The account type of the receiver.
            transaction_type (str): The type of transaction (e.g., AccountTransfer, DigitalPayment).
            transaction_description (str, Optional): The description of the transaction.
            payment_method (str, Optional): The payment method used if the transaction is a DigitalPayment.

        Returns:
            bool: True if the transaction was successful, False otherwise.
        """
        def callback(session: ClientSession):
            # Create the transaction document

            if sender_user_name == receiver_user_name and sender_account_number == receiver_account_number:
                logging.error("Cannot transfer to the same account!")
                return False

            transaction_internal = False

            if sender_user_name == receiver_user_name and sender_account_number != receiver_account_number:
                transaction_internal = True

            transaction = {
                "TransactionAmount": transaction_amount,
                "TransactionDescription": transaction_description,
                "TransactionDetails": {
                    "TransactionType": transaction_type,
                    "TransactionInternal": transaction_internal,
                },
                "TransactionReferenceData": {
                    "TransactionSender": {
                        "UserId": ObjectId(sender_user_id),
                        "UserName": sender_user_name,
                        "AccountId": ObjectId(account_id_sender),
                        "AccountNumber": sender_account_number,
                        "AccountType": sender_account_type,
                    },
                    "TransactionReceiver": {
                        "UserId": ObjectId(receiver_user_id),
                        "UserName": receiver_user_name,
                        "AccountId": ObjectId(account_id_receiver),
                        "AccountNumber": receiver_account_number,
                        "AccountType": receiver_account_type,
                    },
                },
                "TransactionDates": [
                    {
                        "TransactionDate": datetime.now(timezone.utc),
                        "TransactionDateType": "TransactionInitiatedDate",
                    }
                ],
                "TransactionStatus": "Initiated",
                "TransactionCompleted": False,
                "TransactionNotified": False,
            }

            # Add payment method if it's a DigitalPayment
            if transaction_type == "DigitalPayment" and payment_method:
                transaction["TransactionDetails"]["TransactionPaymentMethod"] = payment_method

            # Update sender account: subtract transaction amount from balance
            sender_result = self.accounts_collection.find_one_and_update(
                {"_id": ObjectId(account_id_sender)},
                {
                    "$inc": {"AccountBalance": -transaction_amount}
                },
                session=session,
                return_document=True
            )
            # Update receiver account: add transaction amount to balance
            receiver_result = self.accounts_collection.find_one_and_update(
                {"_id": ObjectId(account_id_receiver)},
                {
                    "$inc": {"AccountBalance": transaction_amount}
                },
                session=session,
                return_document=True
            )

            # Add new transaction to 'transactions' collection
            transaction_id = self.transactions_collection.insert_one(transaction, session=session).inserted_id

            # Update the transaction document with the completed date and status
            self.transactions_collection.update_one(
                {"_id": transaction_id},
                {
                    "$set": {
                        "TransactionStatus": "Completed",
                        "TransactionCompleted": True
                    },
                    "$push": {
                        "TransactionDates": {
                            "TransactionDate": datetime.now(timezone.utc),
                            "TransactionDateType": "TransactionCompletedDate"
                        }
                    }
                },
                session=session
            )

            # Update RecentTransactions for sender
            self.users_collection.update_one(
                {"_id": ObjectId(sender_user_id)},
                {
                    "$push": {
                        "RecentTransactions": {
                            "$each": [{"TransactionId": transaction_id, "Date": datetime.now(timezone.utc)}],
                            "$slice": -20
                        }
                    }
                },
                session=session
            )
            # Update RecentTransactions for receiver
            self.users_collection.update_one(
                {"_id": ObjectId(receiver_user_id)},
                {
                    "$push": {
                        "RecentTransactions": {
                            "$each": [{"TransactionId": transaction_id, "Date": datetime.now(timezone.utc)}],
                            "$slice": -20
                        }
                    }
                },
                session=session
            )

            # Create notifications
            notification_date = datetime.now(timezone.utc)
            if transaction_type == "AccountTransfer":
                sender_notification = {
                    "NotificationEvent": "TransferSent",
                    "NotificationMessage": f"You have transferred {sender_result['AccountCurrency']} {transaction_amount} to {receiver_user_name}. Your new balance is {sender_result['AccountCurrency']} {sender_result['AccountBalance']}.",
                    "NotificationDate": notification_date,
                    "NotificationUser": {
                        "UserName": sender_user_name,
                        "UserId": ObjectId(sender_user_id)
                    }
                }
                receiver_notification = {
                    "NotificationEvent": "TransferReceived",
                    "NotificationMessage": f"You have received a transfer of {receiver_result['AccountCurrency']} {transaction_amount} from {sender_user_name}. Your new balance is {receiver_result['AccountCurrency']} {receiver_result['AccountBalance']}.",
                    "NotificationDate": notification_date,
                    "NotificationUser": {
                        "UserName": receiver_user_name,
                        "UserId": ObjectId(receiver_user_id)
                    }
                }
            else:  # Assuming the other type is DigitalPayment
                sender_notification = {
                    "NotificationEvent": "PaymentMade",
                    "NotificationMessage": f"You have made a payment of {sender_result['AccountCurrency']} {transaction_amount} to {receiver_user_name} using {payment_method}. Your new balance is {sender_result['AccountCurrency']} {sender_result['AccountBalance']}.",
                    "NotificationDate": notification_date,
                    "NotificationUser": {
                        "UserName": sender_user_name,
                        "UserId": ObjectId(sender_user_id)
                    }
                }
                receiver_notification = {
                    "NotificationEvent": "PaymentReceived",
                    "NotificationMessage": f"You have received a payment of {receiver_result['AccountCurrency']} {transaction_amount} from {sender_user_name} via {payment_method}. Your new balance is {receiver_result['AccountCurrency']} {receiver_result['AccountBalance']}.",
                    "NotificationDate": notification_date,
                    "NotificationUser": {
                        "UserName": receiver_user_name,
                        "UserId": ObjectId(receiver_user_id)
                    }
                }
            self.notifications_collection.insert_many([sender_notification, receiver_notification], session=session)

            # Update the transaction document with the notified date, status, and notification flag
            self.transactions_collection.update_one(
                {"_id": transaction_id},
                {
                    "$set": {
                        "TransactionStatus": "Notified",
                        "TransactionNotified": True
                    },
                    "$push": {
                        "TransactionDates": {
                            "TransactionDate": datetime.now(timezone.utc),
                            "TransactionDateType": "TransactionNotifiedDate"
                        }
                    }
                },
                session=session
            )

            logging.info("Transaction successful")

        # Start a client session and execute the transaction
        with self.db.client.start_session() as session:
            try:
                session.with_transaction(callback)
                return True
            except Exception as e:
                logging.error(f"Transaction failed: {e}")
                return False
