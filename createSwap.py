import asyncio
import requests
import base64
import os
from solders.transaction import VersionedTransaction
from solders.message import to_bytes_versioned
from solders.keypair import Keypair
from dotenv import load_dotenv
from utils.getOptimalBudget import get_optimal_compute_budget

load_dotenv()

# Load and validate environment variables
def validate_env_variables():
    required_vars = ["PRIVATE_KEY", "USDC_MINT", "TARGET_TOKEN_MINT_ADDRESS"]
    for var in required_vars:
        if not os.getenv(var):
            raise ValueError(f"Missing required environment variable: {var}")

validate_env_variables()

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SENDER_KEYPAIR = Keypair.from_base58_string(PRIVATE_KEY)
USDC_MINT = os.getenv("USDC_MINT")
TARGET_TOKEN_MINT_ADDRESS = os.getenv("TARGET_TOKEN_MINT_ADDRESS")

async def create_signed_jupiter_swap_tx(fee):
    """Creates and returns a signed Jupiter swap transaction."""
    print("\n=== Preparing Jupiter Swap Transaction ===")
    print(f"Fee: {fee}")

    amount_lamports = int(fee * 10**6)
    compute_budget = get_optimal_compute_budget(0)  # Start with default budget

    # Get quote from Jupiter
    quote_params = {
        "inputMint": USDC_MINT,
        "outputMint": TARGET_TOKEN_MINT_ADDRESS,
        "amount": str(amount_lamports),
        "slippageBps": "100"
    }
    response = requests.get("https://quote-api.jup.ag/v6/quote", params=quote_params)
    if not response.ok:
        raise Exception(f"Failed to get quote: {response.text}")
    quote_data = response.json()
    print(quote_data)

    # Get swap transaction from Jupiter
    swap_data = {
        "quoteResponse": quote_data,
        "userPublicKey": str(SENDER_KEYPAIR.pubkey()),
        **compute_budget,
        "slippageBps": 500,
        # Remove useVersionedTransaction flag to use default (versioned) transactions
    }
    response = requests.post("https://quote-api.jup.ag/v6/swap", json=swap_data)
    if not response.ok:
        raise Exception(f"Failed to get swap transaction: {response.text}")
    swap_instruction = response.json()["swapTransaction"]
    
    # Decode and sign transaction using versioned transaction
    raw_tx = VersionedTransaction.from_bytes(base64.b64decode(swap_instruction))
    signature = SENDER_KEYPAIR.sign_message(to_bytes_versioned(raw_tx.message))
    signed_tx = VersionedTransaction.populate(raw_tx.message, [signature])
    
    print("Jupiter Swap Transaction prepared and signed.")
    return signed_tx

