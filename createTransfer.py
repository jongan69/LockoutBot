import asyncio
import os
from solana.rpc.api import Client
from spl.token.instructions import create_associated_token_account
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from spl.token.instructions import get_associated_token_address, transfer_checked, TransferCheckedParams
from spl.token.constants import TOKEN_PROGRAM_ID
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from dotenv import load_dotenv

load_dotenv()

# Load and validate environment variables
def validate_env_variables():
    required_vars = ["SOLANA_RPC_URL", "PRIVATE_KEY"]
    for var in required_vars:
        if not os.getenv(var):
            raise ValueError(f"Missing required environment variable: {var}")

validate_env_variables()

SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SENDER_KEYPAIR = Keypair.from_base58_string(PRIVATE_KEY)

async def create_signed_usdc_transfer_tx(mint, decimals, destination_address, amount):
    """Creates and returns a signed USDC transfer transaction without sending it."""
    # Convert decimals and amount to proper types
    decimals = int(decimals)
    amount = float(amount) if isinstance(amount, str) else amount
    amount_in_smallest_units = int(amount * (10 ** decimals))
    
    print("\n=== Preparing USDC Transfer Transaction ===")
    print(f"Destination: {destination_address}")
    print(f"Amount: {amount} (in smallest units: {amount_in_smallest_units})")

    client = Client(SOLANA_RPC_URL)
    MINT_PUBKEY = Pubkey.from_string(mint)

    # Get sender and recipient ATA
    sender_ata = get_associated_token_address(SENDER_KEYPAIR.pubkey(), MINT_PUBKEY)
    recipient_pubkey = Pubkey.from_string(destination_address)
    recipient_ata = get_associated_token_address(recipient_pubkey, MINT_PUBKEY)

    print(f"Sender ATA: {sender_ata}")
    print(f"Recipient ATA: {recipient_ata}")

    # Get recent blockhash
    recent_blockhash_response = client.get_latest_blockhash(commitment="confirmed")
    blockhash = recent_blockhash_response.value.blockhash

    # Create instructions list
    instructions = []
    
    # Check if recipient ATA exists
    response = client.get_account_info(recipient_ata)
    if response.value is None:
        print("Recipient ATA does not exist. Adding ATA creation instruction.")
        create_ata_ix = create_associated_token_account(
            payer=SENDER_KEYPAIR.pubkey(),
            owner=recipient_pubkey,
            mint=MINT_PUBKEY
        )
        instructions.append(create_ata_ix)
    
    # Add transfer instruction
    transfer_ix = transfer_checked(
        TransferCheckedParams(
            program_id=TOKEN_PROGRAM_ID,
            source=sender_ata,
            mint=MINT_PUBKEY,
            dest=recipient_ata,
            owner=SENDER_KEYPAIR.pubkey(),
            amount=amount_in_smallest_units,
            decimals=decimals,
            signers=[]
        )
    )
    instructions.append(transfer_ix)

    # Create versioned transaction
    message = MessageV0.try_compile(
        payer=SENDER_KEYPAIR.pubkey(),
        instructions=instructions,
        recent_blockhash=blockhash,
        address_lookup_table_accounts=[]
    )
    versioned_tx = VersionedTransaction(message, [SENDER_KEYPAIR])
    
    print("Transaction prepared and signed.")
    return versioned_tx

# if __name__ == "__main__":
#     # Example usage
#     asyncio.run(
#         create_signed_usdc_transfer_tx(
#             mint="YourMintAddressHere",
#             decimals=6,
#             destination_address="RecipientAddressHere",
#             amount=1000000  # Amount in smallest units (e.g., 1 USDC = 1,000,000 units for 6 decimals)
#         )
#     )
