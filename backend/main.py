from database.connection import MongoDBConnection
from services.transactions_service import TransactionsService
from encoder.json_encoder import MyJSONEncoder

import logging

import json
from typing import Union
from bson import ObjectId

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

import os
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

MONGODB_URI = os.getenv("MONGODB_URI")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize the MongoDB connection
connection = MongoDBConnection(MONGODB_URI)

# Set the database name
db_name = "leafy_bank"

# Initialize the TransactionsService
transactions_service = TransactionsService(connection, db_name)


def validate_transaction_amount(data):
    """Validate the transaction amount from the request data."""
    try:
        transaction_amount = float(data["transaction_amount"])
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Transaction amount must be a valid number.")

    if transaction_amount <= 0:
        raise HTTPException(
            status_code=400, detail="Transaction amount must be greater than 0.")

    transaction_limit = float(500)
    if transaction_amount > transaction_limit:
        raise HTTPException(
            status_code=400,
            detail=f"Transaction amount exceeds the limit of {transaction_limit}. Please ensure the amount is {transaction_limit} or less."
        )

    return transaction_amount


@app.get("/")
async def read_root(request: Request):
    return {"message": "Server is running"}


@app.post("/fetch-recent-transactions-for-user")
async def fetch_recent_transactions_for_user(request: Request):
    """Retrieve recent transactions for a specific user by UserName or ID.
    Args:
        request (Request): The request object containing the user_identifier.
    Returns:
        dict: A list of recent transactions associated with the user.
    """
    try:
        data = await request.json()
        user_identifier = data.get("user_identifier")
        if not user_identifier:
            raise HTTPException(
                status_code=400, detail="User identifier is required")
        if ObjectId.is_valid(user_identifier):
            user_identifier = ObjectId(user_identifier)
        # Validate if the user exists
        if not transactions_service.is_valid_user(user_identifier):
            raise HTTPException(
                status_code=404, detail="User not found")
        transactions = transactions_service.get_recent_transactions_for_user(user_identifier)
        if transactions:
            logging.info(f"Found {len(transactions)} recent transactions for user {user_identifier}")
            return Response(content=json.dumps({"transactions": transactions}, cls=MyJSONEncoder), media_type="application/json")
        else:
            logging.info(f"No recent transactions found for user {user_identifier}")
            return Response(content=json.dumps({"transactions": []}, cls=MyJSONEncoder), media_type="application/json")
    except Exception as e:
        logging.error(f"Error retrieving recent transactions for user: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/perform-account-transfer")
async def perform_account_transfer(request: Request):
    """Perform an account transfer transaction.

    Args:
        request (Request): The request object containing transaction data.

    Returns:
        dict: A message indicating success or failure.
    """
    try:
        data = await request.json()
        transaction_amount = validate_transaction_amount(data)

        transaction_id = transactions_service.perform_transaction(
            account_id_sender=data["account_id_sender"],
            account_id_receiver=data["account_id_receiver"],
            transaction_amount=transaction_amount,
            sender_user_id=data["sender_user_id"],
            sender_user_name=data["sender_user_name"],
            sender_account_number=data["sender_account_number"],
            sender_account_type=data["sender_account_type"],
            receiver_user_id=data["receiver_user_id"],
            receiver_user_name=data["receiver_user_name"],
            receiver_account_number=data["receiver_account_number"],
            receiver_account_type=data["receiver_account_type"],
            transaction_type="AccountTransfer"
        )
        if transaction_id:
            logging.info(
                f"Account transfer transaction completed successfully with ID: {transaction_id}")
            return {"message": "Account transfer transaction completed successfully.", "transaction_id": str(transaction_id)}
        else:
            logging.error("Account transfer transaction failed.")
            raise HTTPException(
                status_code=400, detail="Account transfer transaction failed.")
    except HTTPException as e:
        raise e
    except Exception as e:
        logging.error(f"Failed to perform account transfer: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/perform-digital-payment")
async def perform_digital_payment(request: Request):
    """Perform a digital payment transaction.

    Args:
        request (Request): The request object containing transaction data.

    Returns:
        dict: A message indicating success or failure.
    """
    try:
        data = await request.json()
        transaction_amount = validate_transaction_amount(data)

        # Validate payment method
        payment_method = data.get("payment_method")
        if not payment_method or payment_method == "N/A":
            raise HTTPException(
                status_code=400,
                detail="Payment method must be selected for a digital payment."
            )

        transaction_id = transactions_service.perform_transaction(
            account_id_sender=data["account_id_sender"],
            account_id_receiver=data["account_id_receiver"],
            transaction_amount=transaction_amount,
            sender_user_id=data["sender_user_id"],
            sender_user_name=data["sender_user_name"],
            sender_account_number=data["sender_account_number"],
            sender_account_type=data["sender_account_type"],
            receiver_user_id=data["receiver_user_id"],
            receiver_user_name=data["receiver_user_name"],
            receiver_account_number=data["receiver_account_number"],
            receiver_account_type=data["receiver_account_type"],
            transaction_type="DigitalPayment",
            payment_method=payment_method
        )
        if transaction_id:
            logging.info(
                f"Digital payment transaction completed successfully with ID: {transaction_id}")
            return {"message": "Digital payment transaction completed successfully.", "transaction_id": str(transaction_id)}
        else:
            logging.error("Digital payment transaction failed.")
            raise HTTPException(
                status_code=400, detail="Digital payment transaction failed.")
    except HTTPException as e:
        raise e
    except Exception as e:
        logging.error(f"Failed to perform digital payment: {e}")
        raise HTTPException(status_code=500, detail=str(e))
