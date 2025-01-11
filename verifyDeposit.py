import requests
import asyncio
import datetime
from solders.pubkey import Pubkey
from spl.token.instructions import get_associated_token_address
import os
from dotenv import load_dotenv

load_dotenv()

SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL")
USDC_MINT = os.getenv("USDC_MINT")


async def verify_usdc_deposit(expected_amount, user_sol_address, users_collection):
    """
    Verify that the USDC deposit has been received in the intermediary wallet's USDC address.
    Checks periodically for 10 minutes before timing out.
    Returns True if deposit is confirmed, False otherwise.
    """
    TIMEOUT_MINUTES = 10
    CHECK_INTERVAL_SECONDS = 15
    max_attempts = (TIMEOUT_MINUTES * 60) // CHECK_INTERVAL_SECONDS
    INTERMEDIARY_USDC_ADDRESS = "3T8re2uQJvbHLiE5QsfXJKMtj5DmWEoWe23cXsB6gmjo"
    
    try:
        # Get user's USDC ATA
        user_pubkey = Pubkey.from_string(user_sol_address)
        usdc_mint_pubkey = Pubkey.from_string(USDC_MINT)
        user_usdc_address = str(get_associated_token_address(user_pubkey, usdc_mint_pubkey))
        
        print(f"Looking for transfers from user's USDC address: {user_usdc_address}")
        
        # Get user's document from MongoDB
        user = users_collection.find_one({"sol_wallet": user_sol_address})
        if not user:
            print("User not found in database")
            return False
            
        # Initialize processed_transactions array if it doesn't exist
        if 'processed_transactions' not in user:
            users_collection.update_one(
                {"sol_wallet": user_sol_address},
                {"$set": {"processed_transactions": []}}
            )
        
        for attempt in range(max_attempts):
            print(f"\nScanning recent transactions...")
            
            # Get recent signatures for the intermediary USDC token account
            headers = {"accept": "application/json", "content-type": "application/json"}
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    INTERMEDIARY_USDC_ADDRESS,
                    {
                        "limit": 20,
                        "commitment": "confirmed"
                    }
                ]
            }

            response = requests.post(SOLANA_RPC_URL, headers=headers, json=payload)
            signatures = response.json().get("result", [])

            for sig_info in signatures:
                tx_sig = sig_info['signature']
                
                # Check if transaction has already been processed
                user = users_collection.find_one({
                    "sol_wallet": user_sol_address,
                    "processed_transactions": tx_sig
                })
                
                if user:
                    print(f"Transaction {tx_sig} already processed, skipping...")
                    continue
                    
                print(f"Checking tx: {tx_sig}")
                
                # Get detailed transaction info
                tx_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [
                        tx_sig,
                        {
                            "encoding": "jsonParsed",
                            "commitment": "confirmed",
                            "maxSupportedTransactionVersion": 0
                        }
                    ]
                }
                
                tx_response = requests.post(SOLANA_RPC_URL, headers=headers, json=tx_payload)
                tx_data = tx_response.json().get("result", {})

                if not tx_data or 'transaction' not in tx_data:
                    continue

                # Look for token transfers in the transaction
                message = tx_data['transaction'].get('message', {})
                instructions = message.get('instructions', [])
                
                for instruction in instructions:
                    if instruction.get('program') == 'spl-token' and 'parsed' in instruction:
                        parsed = instruction['parsed']
                        
                        if parsed.get('type') == 'transferChecked' and 'info' in parsed:
                            info = parsed['info']
                            
                            # Check if this is a USDC transfer from user's USDC address to our intermediary
                            if (info.get('mint') == USDC_MINT and 
                                info.get('source') == user_usdc_address and
                                info.get('destination') == INTERMEDIARY_USDC_ADDRESS):
                                
                                amount = float(info['tokenAmount']['uiAmount'])
                                
                                print(f"💰 USDC Transfer Found:")
                                print(f"Amount: {amount} USDC")
                                print(f"From: {user_usdc_address}")
                                print(f"To: {INTERMEDIARY_USDC_ADDRESS}")
                                
                                # Store transaction details with initial status
                                tx_details = {
                                    "signature": tx_sig,
                                    "amount": amount,
                                    "timestamp": datetime.datetime.now(),
                                    "type": "USDC_deposit",
                                    "status": "processing",
                                    "changenow_id": None,
                                    "changenow_status": None,
                                    "outbound_tx": None
                                }
                                
                                # Add transaction to processed list with processing status
                                users_collection.update_one(
                                    {"sol_wallet": user_sol_address},
                                    {
                                        "$push": {
                                            "processed_transactions": tx_sig,
                                            "transaction_history": tx_details
                                        }
                                    }
                                )
                                
                                # Check if this matches our expected amount
                                if amount >= expected_amount:
                                    print(f"✅ Expected deposit verified: {amount} USDC")
                                    return True

            if attempt < max_attempts - 1:
                remaining_seconds = TIMEOUT_MINUTES * 60 - (attempt + 1) * CHECK_INTERVAL_SECONDS
                print(f"\nWaiting {CHECK_INTERVAL_SECONDS} seconds... "
                      f"({remaining_seconds // 60}m {remaining_seconds % 60}s remaining)")
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            
        print(f"\nDeposit verification timed out after {TIMEOUT_MINUTES} minutes.")
        return False
        
    except Exception as e:
        print(f"Error verifying deposit: {str(e)}")
        return False
