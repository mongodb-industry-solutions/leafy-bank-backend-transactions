from bson import ObjectId
from typing import Union
from database.connection import MongoDBConnection

class TransactionsService:
    def __init__(self):
        self.db = MongoDBConnection().db

    def get_transactions(self, user_id: Union[str, ObjectId]):
        return self.db.transactions.find({'user_id': user_id})

    def create_transaction(self, user_id: Union[str, ObjectId], amount: int):
        return self.db.transactions.insert_one({
            'user_id': user_id,
            'amount': amount
        })

    def get_transaction(self, transaction_id: Union[str, ObjectId]):
        return self.db.transactions.find_one({'_id': transaction_id})