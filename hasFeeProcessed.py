import pymongo
from dotenv import load_dotenv
import os

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")

def has_fee_been_processed(user_sol_address: str, original_tx_sig: str) -> bool:
    """Check if the fee has already been processed for this transaction"""
    client = pymongo.MongoClient(MONGO_URI)
    db = client["swap_bot"]
    users_collection = db["users"]
    user = users_collection.find_one({
        "sol_wallet": user_sol_address,
        "transaction_history": {
            "$elemMatch": {
                "signature": original_tx_sig,
                "fee_processed": True
            }
        }
    })
    return bool(user)