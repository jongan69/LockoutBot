import os
from solana.rpc.api import Client
from solana.rpc.commitment import Processed
from solders.transaction import VersionedTransaction
from solders.system_program import TransferParams, transfer
from solders.keypair import Keypair
from dotenv import load_dotenv
from typing import List
from jito_searcher_client.generated.bundle_pb2 import Bundle
from jito_searcher_client.generated.searcher_pb2 import SendBundleRequest
from jito_searcher_client.searcher import get_searcher_client
from solders.pubkey import Pubkey
from jito_searcher_client.generated.packet_pb2 import Packet
from solders.message import MessageV0
from time import sleep
from enum import Enum
import requests

# Constants for tip accounts
TIP_ACCOUNTS = [
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe", 
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT"
]

MINIMUM_TIP = 1000  # Minimum tip in lamports

load_dotenv()

# Load and validate environment variables
def validate_env_variables():
    print("Validating environment variables...")
    required_vars = ["PRIVATE_KEY", "BLOCK_ENGINE_URL", "SOLANA_RPC_URL"]
    for var in required_vars:
        if not os.getenv(var):
            raise ValueError(f"Missing required environment variable: {var}")
    print("\u2713 All required environment variables found")

validate_env_variables()

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
BLOCK_ENGINE_URL = os.getenv("BLOCK_ENGINE_URL")
RPC_URL = os.getenv("SOLANA_RPC_URL")
SENDER_KEYPAIR = Keypair.from_base58_string(PRIVATE_KEY)

def get_random_tip_account() -> Pubkey:
    """Returns a random tip account from the list of valid tip accounts."""
    import random
    tip_account = random.choice(TIP_ACCOUNTS)
    return Pubkey.from_string(tip_account)

class BundleStatus(Enum):
    INVALID = "Invalid"  # Bundle ID not in system (5 minute look back)
    PENDING = "Pending"  # Not failed, not landed, not invalid
    FAILED = "Failed"   # All regions marked as failed, not forwarded
    LANDED = "Landed"   # Landed on-chain

def check_bundle_status(jito_client, bundle_id: str, max_retries: int = 30, retry_delay: float = 1.0):
    """
    Checks the status of a bundle using getBundleStatuses.
    
    Args:
        jito_client: The initialized Jito client (used only to get the URL)
        bundle_id: The bundle ID to check
        max_retries: Maximum number of status check attempts
        retry_delay: Delay between retries in seconds
        
    Returns:
        tuple: (status: BundleStatus, landed_slot: Optional[int])
    """
    print(f"\n=== Checking Bundle Status: {bundle_id} ===")
    
    # Match the TypeScript implementation URL format
    base_url = BLOCK_ENGINE_URL.rstrip('/')  # Remove any trailing slashes
    json_rpc_url = f"https://{base_url}/api/v1/bundles"  # Base URL without path
    
    print(f"Checking bundle status at: {json_rpc_url}")
    
    headers = {
        "Content-Type": "application/json",
    }
    
    for attempt in range(max_retries):
        try:
            # Create JSON-RPC request payload
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBundleStatuses",
                "params": [[bundle_id]]
            }
            
            response = requests.post(json_rpc_url, headers=headers, json=payload, timeout=5)
            response.raise_for_status()
            
            result = response.json()
            print(f"Response: {result}")
            
            if "error" in result:
                print(f"Error from server: {result['error']}")
                sleep(retry_delay)
                continue
            
            # Check if we have a valid result structure
            if not result.get("result", {}).get("value"):
                print(f"Attempt {attempt + 1}/{max_retries}: Bundle status not found, retrying...")
                sleep(retry_delay)
                continue
            
            bundle_info = result["result"]["value"][0]
            print(f"Bundle info: {bundle_info}")
            
            # Map the confirmation_status to our BundleStatus enum
            confirmation_status = bundle_info.get("confirmation_status", "").upper()
            if confirmation_status == "FINALIZED":
                status = BundleStatus.LANDED
            elif confirmation_status == "PROCESSED" or confirmation_status == "CONFIRMED":
                status = BundleStatus.PENDING
            elif bundle_info.get("err"):
                status = BundleStatus.FAILED
            else:
                status = BundleStatus.INVALID
            
            landed_slot = bundle_info.get("slot")
            
            status_msg = f"Status: {status.value}"
            if landed_slot:
                status_msg += f", Slot: {landed_slot}"
            print(f"\u2713 {status_msg}")
            
            # Return immediately if we have a final status
            if status in [BundleStatus.LANDED, BundleStatus.FAILED]:
                return status, landed_slot
            
            # For PENDING, keep checking
            if status == BundleStatus.PENDING:
                print(f"Bundle is pending, checking again in {retry_delay} seconds...")
                sleep(retry_delay)
                continue
            
            # For INVALID, stop checking
            if status == BundleStatus.INVALID:
                print("Bundle is invalid or expired")
                return status, None
            
        except requests.exceptions.RequestException as e:
            print(f"HTTP request error: {e}")
            sleep(retry_delay)
        except Exception as e:
            print(f"Error checking bundle status: {e}")
            print(f"Full error details: {str(e)}")
            sleep(retry_delay)
    
    print(f"\u2757 Max retries ({max_retries}) reached without final status")
    return BundleStatus.PENDING, None

async def send_bundle_with_tip(signed_transactions: List[VersionedTransaction], tip_lamports: int):
    """
    Bundles and sends signed versioned transactions with a built-in tip.
    The tip must be at least 1000 lamports.
    
    Args:
        signed_transactions: List of signed transactions to bundle
        tip_lamports: Amount of lamports to tip (minimum 1000)
    
    Returns:
        tuple: (bundle_id: str, status: BundleStatus, landed_slot: Optional[int])
    """
    if tip_lamports < MINIMUM_TIP:
        raise ValueError(f"Tip must be at least {MINIMUM_TIP} lamports")

    if len(signed_transactions) > 5:
        raise ValueError("Maximum bundle size is 5 transactions")

    print("\n=== Initializing Bundle Send Process ===")
    print(f"Number of transactions to bundle: {len(signed_transactions)}")
    print(f"Tip amount: {tip_lamports} lamports")

    print("\nInitializing clients...")
    jito_client = get_searcher_client(BLOCK_ENGINE_URL)
    print("\u2713 Jito client initialized")

    print("\n=== Getting Blockchain Info ===")
    rpc_client = Client(RPC_URL)
    blockhash = rpc_client.get_latest_blockhash().value.blockhash
    block_height = rpc_client.get_block_height(Processed).value
    print(f"\u2713 Latest blockhash: {blockhash}")
    print(f"\u2713 Current block height: {block_height}")

    try:
        print("\n=== Creating Bundle ===")
        tip_account_pubkey = get_random_tip_account()
        
        # Create tip instruction and transaction
        tip_instruction = transfer(
            TransferParams(
                from_pubkey=SENDER_KEYPAIR.pubkey(),
                to_pubkey=tip_account_pubkey,
                lamports=tip_lamports
            )
        )
        message = MessageV0.try_compile(
            payer=SENDER_KEYPAIR.pubkey(),
            instructions=[tip_instruction],
            recent_blockhash=blockhash,
            address_lookup_table_accounts=[]
        )
        tip_tx = VersionedTransaction(message, [SENDER_KEYPAIR])
        
        # Insert tip transaction at the beginning
        signed_transactions.insert(0, tip_tx)
        
        print("\n=== Converting Transactions to Packets ===")
        packets = [Packet(data=bytes(tx)) for tx in signed_transactions]
        
        print("\n=== Sending Bundle to Network ===")
        response = jito_client.SendBundle(SendBundleRequest(bundle=Bundle(header=None, packets=packets)))
        print(f"Response: {response}")
        bundle_id = response.uuid
        print(f"\u2713 Bundle sent successfully! Bundle ID: {bundle_id}")

        # Check bundle status
        status, landed_slot = check_bundle_status(jito_client, bundle_id)
        
        # Provide a summary based on final status
        if status == BundleStatus.LANDED:
            print(f"\n\u2705 Bundle successfully landed in slot {landed_slot}")
        elif status == BundleStatus.FAILED:
            print("\n\u274c Bundle failed to land")
        elif status == BundleStatus.INVALID:
            print("\n\u26a0 Bundle is invalid or expired")
        else:  # PENDING
            print("\n\u231b Bundle status is still pending")
            
        return bundle_id, status, landed_slot

    except Exception as e:
        print(f"\n\u274c ERROR: Failed to send bundle: {e}")
        raise
