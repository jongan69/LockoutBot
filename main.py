import os
from constants import MAX_RETRIES, RETRY_DELAY, SWAP_FEE_PERCENTAGE, USDC_DECIMALS, USDC_MINT
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    ConversationHandler, 
    MessageHandler, 
    filters,
    ContextTypes,
    CallbackQueryHandler
)
import requests
from pymongo import MongoClient
import re
from solana.rpc.api import Client
from solders.transaction import VersionedTransaction
from solders.keypair import Keypair
from solders.pubkey import Pubkey
import base64
from solders.message import to_bytes_versioned
from solana.rpc.types import TxOpts
from spl.token.instructions import get_associated_token_address
from solana.transaction import Transaction
from spl.token.constants import TOKEN_PROGRAM_ID
from solana.rpc.commitment import Confirmed
import time
from typing import Optional
from spl.token.instructions import transfer_checked, TransferCheckedParams
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from spl.token.instructions import create_associated_token_account
import asyncio
import datetime

# Load environment variables
load_dotenv()

# Constants
SOLANA_RPC_URL = "https://mainnet.helius-rpc.com/?api-key=59516a79-eec8-4ff4-a505-63ea684999b5"
client = Client(SOLANA_RPC_URL)
CHANGE_NOW_URL = "https://api.changenow.io/v2/exchange"
CHANGE_NOW_API_KEY = os.getenv("CHANGE_NOW_API_KEY")  # Set your ChangeNOW API key
MONGO_URI = os.getenv("MONGO_URI")  # Set your MongoDB connection URI
INTERMEDIARY_SOL_WALLET = os.getenv("INTERMEDIARY_SOL_WALLET")  # Wallet managed by the bot
PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # Private key for the intermediary wallet
TARGET_TOKEN_ADDRESS = "8Ki8DpuWNxu9VsS3kQbarsCWMcFGWkzzA8pUPto9zBd5"  # Target token address
INTERMEDIARY_USDC_ADDRESS = "3T8re2uQJvbHLiE5QsfXJKMtj5DmWEoWe23cXsB6gmjo"
SENDER_KEYPAIR = Keypair.from_base58_string(PRIVATE_KEY)

# Connect to MongoDB
client_db = MongoClient(MONGO_URI, tls=True, tlsAllowInvalidCertificates=True)
db = client_db["telegram_bot"]
users_collection = db["users"]

# Add these states after the constants
SOLANA_WALLET, BITCOIN_ADDRESS = range(2)

def is_valid_solana_address(address):
    # Validate Solana address (base58 and 32-44 chars long)
    return re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", address) is not None

def is_valid_bitcoin_address(address):
    # Validate Bitcoin address (P2PKH, P2SH, or Bech32)
    return re.match(r"^(bc1|[13])[a-zA-HJ-NP-Z0-9]{25,39}$", address) is not None

# Commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Register Wallets", callback_data='register')],
        [InlineKeyboardButton("Start New Swap", callback_data='swap')],
        [InlineKeyboardButton("Help", callback_data='help')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Welcome to the USDC-BTC Swap Bot! 🤖\n\n"
        "What would you like to do?",
        reply_markup=reply_markup
    )

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the registration process"""
    # Check if this is from a callback query or direct command
    if update.callback_query:
        await update.callback_query.edit_message_text(
            "Let's start the registration process.\n"
            "Please send your Solana wallet address:"
        )
    else:
        await update.message.reply_text(
            "Let's start the registration process.\n"
            "Please send your Solana wallet address:"
        )
    return SOLANA_WALLET

async def solana_wallet_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Solana wallet input"""
    sol_wallet = update.message.text
    
    if not is_valid_solana_address(sol_wallet):
        await update.message.reply_text(
            "Invalid Solana wallet address. Please send a valid address:"
        )
        return SOLANA_WALLET if 'state' not in context.user_data else None
    
    context.user_data['sol_wallet'] = sol_wallet
    
    await update.message.reply_text(
        "Great! Now please send your CashApp Bitcoin address:"
    )
    
    if 'state' in context.user_data:
        context.user_data['state'] = BITCOIN_ADDRESS
        return None
    return BITCOIN_ADDRESS

async def bitcoin_address_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Bitcoin address input"""
    btc_address = update.message.text
    
    if not is_valid_bitcoin_address(btc_address):
        await update.message.reply_text(
            "Invalid Bitcoin address. Please send a valid CashApp Bitcoin address:"
        )
        return BITCOIN_ADDRESS
    
    sol_wallet = context.user_data.get('sol_wallet')
    user_id = update.effective_user.id
    
    users_collection.update_one(
        {"_id": user_id},
        {"$set": {"sol_wallet": sol_wallet, "btc_address": btc_address}},
        upsert=True
    )
    
    await update.message.reply_text(
        f"Registration complete!\n"
        f"Solana wallet: {sol_wallet}\n"
        f"Bitcoin address: {btc_address}\n\n"
        f"You can now use the /swap command."
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the registration process"""
    await update.message.reply_text(
        "Registration cancelled. Use /register to start again."
    )
    return ConversationHandler.END

async def swap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = users_collection.find_one({"_id": user_id})

    if not user:
        await update.message.reply_text("Please register your wallet first using /register.")
        return

    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: /swap <Amount in USDC>")
        return

    try:
        amount = float(args[0])
        if amount <= 0:
            await update.message.reply_text("Amount must be greater than 0.")
            return
    except ValueError:
        await update.message.reply_text("Invalid amount. Please enter a valid number.")
        return

    # Calculate fee and amount after fee
    fee = amount * SWAP_FEE_PERCENTAGE
    amount_after_fee = amount - fee

    # Get rate preview
    estimated_btc = await get_rate_preview(amount_after_fee)
    if estimated_btc:
        await update.message.reply_text(
            f"💱 Current Rate Preview:\n"
            f"{amount_after_fee} USDC ≈ {estimated_btc:.5f} BTC\n"
            "(Rate may vary slightly at execution time)"
        )

    btc_address = user["btc_address"]

    # Show transaction summary
    summary = (
        "📝 Transaction Summary:\n\n"
        f"Amount: {amount} USDC\n"
        f"Fee: {fee} USDC ({0.5}%)\n"
        f"Amount after fee: {amount_after_fee} USDC\n"
        f"Estimated BTC: {estimated_btc:.5f} BTC\n"
        f"Receiving address (BTC): {btc_address}\n\n"
        "Please confirm by clicking /confirm or cancel with /cancel"
    )
    await update.message.reply_text(summary)

    # Store the swap details in context for confirmation
    context.user_data['pending_swap'] = {
        'amount': amount,
        'fee': fee,
        'amount_after_fee': amount_after_fee,
        'btc_address': btc_address
    }

async def process_swap(amount_after_fee, fee, btc_address, update):
    try:
        # Get user's Solana address from database
        user_id = update.effective_user.id
        user = users_collection.find_one({"_id": user_id})
        if not user or 'sol_wallet' not in user:
            await update.message.reply_text("Error: Could not find your registered Solana wallet address.")
            return

        user_sol_address = user['sol_wallet']

        # Wait and verify USDC deposit
        await update.message.reply_text(
            f'Waiting for USDC deposit of ```{amount_after_fee + fee}``` USDC from your registered wallet address ```{user_sol_address}``` to the deposit address ```{INTERMEDIARY_SOL_WALLET}``` You have 10 minutes to complete the deposit',
            parse_mode="MarkdownV2"
        )
        
        deposit_verified = await verify_usdc_deposit(amount_after_fee + fee, user_sol_address)
        
        if not deposit_verified:
            await update.message.reply_text(
                "❌ Deposit verification timed out after 10 minutes.\n"
                "The swap has been cancelled. Please try again with a new swap."
            )
            return

        await update.message.reply_text("✅ USDC deposit confirmed. Processing swap...")
        
        # Swap USDC to target token (fee processing)
        swap_result = swap_to_target_token(fee, update)
        if not swap_result:
            await update.message.reply_text("Failed to process fee swap. Please contact support.")
            return

        # Notify user that fee has been processed
        await update.message.reply_text(f"Fee of {fee} USDC worth of $LOCKIN processed successfully.")

        # Exchange remaining USDC to BTC
        payload = {
            "fromCurrency": "usdc",
            "toCurrency": "btc",
            "fromNetwork": "sol",
            "toNetwork": "btc",
            "fromAmount": amount_after_fee,
            "address": btc_address,
            "type": "direct",
            "flow": "standard",
        }

        headers = {
            "Content-Type": "application/json",
            "x-changenow-api-key": CHANGE_NOW_API_KEY,
        }

        # Make API request to initiate exchange
        response = requests.post(CHANGE_NOW_URL, json=payload, headers=headers)
        response_data = response.json()
        print(f"ChangeNOW data: {response_data}")
        if response.status_code == 200:
            tx_id = response_data.get("id")
            payin_address = response_data.get("payinAddress")
            
            # Send USDC to the ChangeNOW payin address
            try:
                transfer_amount = int(amount_after_fee * 10**6)  # Convert to USDC decimals
                transfer_tx = send_usdc_to_address(payin_address, transfer_amount)
                
                # Update transaction status with ChangeNOW details
                update_transaction_status(
                    user_sol_address=update.effective_user.id,
                    tx_sig=transfer_tx,
                    changenow_id=tx_id,
                    status="completed",
                    outbound_tx=tx_id
                )
                
                await update.message.reply_text(
                    f"✅ Swap initiated successfully!\n"
                    f"Transaction ID: {tx_id}\n"
                    f"USDC Transfer TX: {transfer_tx}\n"
                    f"The payout will be sent to your Bitcoin address."
                )
            except Exception as e:
                # Update status to failed if transfer fails
                update_transaction_status(
                    user_sol_address=update.effective_user.id,
                    tx_sig=transfer_tx if 'transfer_tx' in locals() else None,
                    status="failed"
                )
                await update.message.reply_text(
                    f"❌ Error sending USDC to exchange: {str(e)}\n"
                    f"Please contact support with Transaction ID: {tx_id}"
                )
                return
                
        elif response_data.get("error") == "out_of_range":
            min_amount = response_data.get("message", "").split(":")[-1].strip()
            await update.message.reply_text(f"Error: The amount is less than the minimum required: {min_amount}.")
        else:
            await update.message.reply_text(f"Error: {response_data.get('message', 'Failed to initiate exchange')}")
    except Exception as e:
        await update.message.reply_text(f"An error occurred: {e}")

async def verify_usdc_deposit(expected_amount, user_sol_address):
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

def update_transaction_status(user_sol_address, tx_sig, changenow_id=None, status=None, outbound_tx=None, swap_sig_id=None):
    """Update the status and details of a transaction"""
    update_fields = {
        "transaction_history.$.status": status
    }
    
    # Convert Signature object to string if necessary
    tx_sig_str = str(tx_sig) if tx_sig else None
    
    if changenow_id:
        update_fields["transaction_history.$.changenow_id"] = changenow_id
    
    if outbound_tx:
        update_fields["transaction_history.$.outbound_tx"] = outbound_tx
        update_fields["transaction_history.$.status"] = "completed"
        
    if swap_sig_id:
        update_fields["transaction_history.$.swap_sig_id"] = str(swap_sig_id)
    
    users_collection.update_one(
        {
            "sol_wallet": user_sol_address,
            "transaction_history.signature": tx_sig_str  # Use string version of signature
        },
        {
            "$set": update_fields
        }
    )
    
def get_optimal_compute_budget(attempt):
    """Return optimal compute budget based on attempt number"""
    base_price = 50000
    return {
        "computeUnitPriceMicroLamports": base_price * (2 ** attempt),
        "computeUnitsLimit": 400000
    }

def swap_to_target_token(fee, update) -> Optional[str]:
    max_retries = MAX_RETRIES
    retry_delay = RETRY_DELAY  # seconds
    user_id = update.effective_user.id
    user = users_collection.find_one({"_id": user_id})
    user_sol_address = user['sol_wallet'] if user else None
    
    for attempt in range(max_retries):
        try:
            # Convert USDC amount to proper integer format (6 decimals)
            amount_lamports = int(fee * 10**6)
            compute_budget = get_optimal_compute_budget(attempt)
            
            # Get quote from Jupiter
            JUPITER_QUOTE_API_URL = f"https://quote-api.jup.ag/v6/quote"
            quote_params = {
                "inputMint": USDC_MINT,  # USDC
                "outputMint": TARGET_TOKEN_ADDRESS,
                "amount": str(amount_lamports),
                "slippageBps": "100"
            }
            
            print(f"Getting quote with params: {quote_params}")
            quote_response = requests.get(JUPITER_QUOTE_API_URL, params=quote_params)
            print(f"Quote response status: {quote_response.status_code}")
            
            if not quote_response.ok:
                raise Exception(f"Failed to get quote: {quote_response.text}")
            
            quote_data = quote_response.json()
            
            # Get swap transaction
            JUPITER_SWAP_API_URL = "https://quote-api.jup.ag/v6/swap"
            swap_data = {
                "quoteResponse": quote_data,
                "userPublicKey": str(SENDER_KEYPAIR.pubkey()),
                **compute_budget,
                "slippageBps": 200  # Increase slippage tolerance to 2%
            }
            
            swap_response = requests.post(JUPITER_SWAP_API_URL, json=swap_data)
            print(f"Swap response status: {swap_response.status_code}")
            
            if not swap_response.ok:
                raise Exception(f"Failed to get swap transaction: {swap_response.text}")
                
            # Get the swap transaction
            swap_instruction = swap_response.json()["swapTransaction"]
            
            print("Creating versioned transaction...")
            raw_tx = VersionedTransaction.from_bytes(base64.b64decode(swap_instruction))
            
            print("Signing transaction...")
            signature = SENDER_KEYPAIR.sign_message(to_bytes_versioned(raw_tx.message))
            signed_tx = VersionedTransaction.populate(raw_tx.message, [signature])
            
            print("Sending transaction...")
            result = client.send_transaction(
                signed_tx,
                opts=TxOpts(
                    skip_confirmation=False,
                    skip_preflight=True,
                    max_retries=5,
                    preflight_commitment="confirmed",
                ),
            )
            
            if not result or not result.value:
                raise Exception(f"Failed to send transaction: {result}")
                
            transaction_id = result.value
            print(f"Transaction sent. ID: {transaction_id}")
            
            # Update transaction status to processing and include swap_sig_id
            if user_sol_address:
                update_transaction_status(
                    user_sol_address=user_sol_address,
                    tx_sig=transaction_id,
                    status="processing",
                    swap_sig_id=transaction_id
                )
            
            # Enhanced confirmation process
            confirmation_timeout = 30  # seconds
            confirmation_check_interval = 1  # seconds
            start_time = time.time()
            
            while time.time() - start_time < confirmation_timeout:
                try:
                    print(f"Checking confirmation for tx {transaction_id}...")
                    
                    # Get transaction status with maxSupportedTransactionVersion
                    tx_status = client.get_transaction(
                        transaction_id,
                        commitment="confirmed",
                        max_supported_transaction_version=0 
                    )
                    if tx_status.value:
                        # Check if the transaction was confirmed
                        if isinstance(tx_status.value, list):
                            # Handle case where result is a list
                            is_confirmed = all(not item.get('err') for item in tx_status.value if isinstance(item, dict))
                        else:
                            # Handle case where result is a single object
                            is_confirmed = not getattr(tx_status.value, 'err', None)
                        if is_confirmed:
                            print(f"Transaction confirmed successfully: {transaction_id}")
                            if user_sol_address:
                                update_transaction_status(
                                    user_sol_address=user_sol_address,
                                    tx_sig=transaction_id,
                                    status="swapping",
                                    swap_sig_id=transaction_id
                                )
                                return transaction_id
                        else:
                            if user_sol_address:
                                update_transaction_status(
                                    user_sol_address=user_sol_address,
                                    tx_sig=transaction_id,
                                    status="failed",
                                    swap_sig_id=transaction_id
                                    )
                                error_details = tx_status.value
                                raise Exception(f"Transaction failed with error: {error_details}")
                
                    print("Transaction not yet confirmed, waiting...")
                    time.sleep(confirmation_check_interval)
                    
                except Exception as confirm_error:
                    print(f"Error checking confirmation: {str(confirm_error)}")
                    time.sleep(confirmation_check_interval)
            
            # Update transaction status to failed if timeout
            if user_sol_address:
                update_transaction_status(
                    user_sol_address=user_sol_address,
                    tx_sig=transaction_id,
                    status="failed",
                    swap_sig_id=transaction_id
                )
            raise Exception(f"Transaction confirmation timeout after {confirmation_timeout} seconds")
            
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {str(e)}")
            if attempt < max_retries - 1:
                print(f"Retrying with higher priority fee in {retry_delay} seconds...")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                if user_sol_address:
                    update_transaction_status(
                        user_sol_address=user_sol_address,
                        tx_sig=transaction_id if 'transaction_id' in locals() else None,
                        status="failed",
                        swap_sig_id=transaction_id if 'transaction_id' in locals() else None
                    )
                error_msg = f"Swap to target token failed after {max_retries} attempts: {str(e)}"
                print(error_msg)
                raise Exception(error_msg)
    
    return None

def send_usdc_to_address(destination_address, amount):
    # Initialize client and constants
    client = Client(SOLANA_RPC_URL)
    USDC_MINT_PUBKEY = Pubkey.from_string(USDC_MINT)
    max_retries = MAX_RETRIES   
    base_delay = RETRY_DELAY

    for attempt in range(max_retries):
        try:
            print(f"Attempt {attempt + 1}: Sending {amount} USDC to {destination_address}")

            # Get sender and recipient ATA
            sender_ata = get_associated_token_address(SENDER_KEYPAIR.pubkey(), USDC_MINT_PUBKEY)
            recipient_pubkey = Pubkey.from_string(destination_address)
            recipient_ata = get_associated_token_address(recipient_pubkey, USDC_MINT_PUBKEY)
            
            # Get recent blockhash
            recent_blockhash_response = client.get_latest_blockhash(commitment="confirmed")
            recent_blockhash = recent_blockhash_response.value.blockhash

            # Create compute budget instructions
            compute_unit_limit_ix = set_compute_unit_limit(200_000)
            compute_unit_price_ix = set_compute_unit_price(1_000)
            
            # Create ATA creation tx
            create_ata_tx = Transaction(fee_payer=SENDER_KEYPAIR.pubkey(), recent_blockhash=recent_blockhash)
            create_ata_ix = create_associated_token_account(
                payer=SENDER_KEYPAIR.pubkey(),
                owner=recipient_pubkey,
                mint=USDC_MINT_PUBKEY
            )    
            create_ata_tx.add(create_ata_ix)
            
            # Create transfer instruction
            transfer_ix = transfer_checked(
                TransferCheckedParams(
                    program_id=TOKEN_PROGRAM_ID,
                    source=sender_ata,
                    mint=USDC_MINT_PUBKEY,
                    dest=recipient_ata,
                    owner=SENDER_KEYPAIR.pubkey(),
                    amount=amount,
                    decimals=USDC_DECIMALS,
                    signers=[]
                )
            )

            # Add instructions and compute budget to the transaction
            create_ata_tx.add(transfer_ix)
            create_ata_tx.add(compute_unit_limit_ix)
            create_ata_tx.add(compute_unit_price_ix)
           
            # Sign and send transaction
            create_ata_tx.sign(SENDER_KEYPAIR)
            result = client.send_transaction(
                create_ata_tx,
                SENDER_KEYPAIR,
                opts=TxOpts(
                    skip_confirmation=False,
                    skip_preflight=True,
                    max_retries=15,
                    preflight_commitment="confirmed"
                )
            )

            tx_id = result.value
            print(f"Transfer transaction sent. TX ID: {tx_id}")

            # Confirm transaction
            confirmation_timeout = 60
            start_time = time.time()

            while time.time() - start_time < confirmation_timeout:
                print("Checking transaction confirmation...")
                tx_status = client.get_transaction(tx_id, commitment="confirmed")

                if tx_status.value:
                    # Check if the transaction was confirmed
                    if isinstance(tx_status.value, list):
                        # Handle case where result is a list
                        is_confirmed = all(not item.get('err') for item in tx_status.value if isinstance(item, dict))
                    else:
                        # Handle case where result is a single object
                        is_confirmed = not getattr(tx_status.value, 'err', None)
                    
                    if is_confirmed:
                        print(f"Transaction confirmed successfully: {tx_id}")
                        return tx_id
                    else:
                        error_details = tx_status.value
                        raise Exception(f"Transaction failed with error: {error_details}")
                        
                time.sleep(2)

            raise Exception(f"Transaction confirmation timeout: {tx_id}")

        except Exception as e:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                print(f"Error on attempt {attempt + 1}: {str(e)}")
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                print(f"Failed after {max_retries} attempts: {str(e)}")
                raise


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    help_text = """
Available commands:
/start - Start the bot
/help - Show this help message
/register - Start the wallet registration process
/swap <Amount in USDC> - Swap USDC to BTC
/cancel - Cancel the current registration process
/getstatus <id> - Check the status of a swap transaction

During registration:
1. Send your Solana wallet address when prompted
2. Send your CashApp Bitcoin address when prompted
    """
    await update.message.reply_text(help_text)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log Errors caused by Updates."""
    print(f'Update "{update}" caused error "{context.error}"')
    if update and update.message:
        await update.message.reply_text("An error occurred while processing your request. Please try again later.")

async def get_rate_preview(usdc_amount):
    """Get current exchange rate preview"""
    try:
        response = requests.get(f"https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd")
        data = response.json()
        btc_price_usd = data['bitcoin']['usd']
        estimated_btc = usdc_amount / btc_price_usd
        return estimated_btc
    except Exception:
        return None

async def confirm_swap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle swap confirmation"""
    if 'pending_swap' not in context.user_data:
        await update.message.reply_text("No pending swap to confirm. Please start a new swap with /swap command.")
        return

    swap_details = context.user_data['pending_swap']
    await process_swap(
        swap_details['amount_after_fee'],
        swap_details['fee'],
        swap_details['btc_address'],
        update
    )
    
    # Clear the pending swap
    del context.user_data['pending_swap']

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()  # Answer the callback query to remove the loading state

    if query.data == 'register':
        await query.edit_message_text(
            "Let's start the registration process.\n"
            "Please send your Solana wallet address:"
        )
        # Set the state manually
        context.user_data['state'] = SOLANA_WALLET
    elif query.data == 'swap':
        await query.edit_message_text(
            "To start a swap, use the command:\n"
            "/swap <amount in USDC>"
        )
    elif query.data == 'help':
        help_text = """
Available commands:
/start - Start the bot
/help - Show this help message
/register - Start the wallet registration process
/swap <Amount in USDC> - Swap USDC to BTC
/cancel - Cancel the current registration process
/getstatus <id> - Check the status of a swap transaction

During registration:
1. Send your Solana wallet address when prompted
2. Send your CashApp Bitcoin address when prompted
        """
        await query.edit_message_text(help_text)

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages during manual state registration"""
    if 'state' not in context.user_data:
        return
    
    state = context.user_data['state']
    if state == SOLANA_WALLET:
        await solana_wallet_input(update, context)
    elif state == BITCOIN_ADDRESS:
        await bitcoin_address_input(update, context)

async def get_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get the status of a ChangeNOW transaction by ID"""
    try:
        # Check if an ID was provided
        if not context.args:
            await update.message.reply_text("Please provide a transaction ID.\nUsage: /getstatus <transaction_id>")
            return

        tx_id = context.args[0]
        
        # Call ChangeNOW API to get status
        headers = {
            "x-changenow-api-key": CHANGE_NOW_API_KEY
        }
        status_url = f"https://api.changenow.io/v2/exchange/by-id?id={tx_id}"
        
        response = requests.get(status_url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            
            # Create status emoji based on status
            status_emoji = {
                "new": "🆕",
                "waiting": "⏳",
                "confirming": "🔄",
                "exchanging": "💱",
                "sending": "📤",
                "finished": "✅",
                "failed": "❌",
                "refunded": "↩️",
                "expired": "⌛"
            }.get(data.get('status', ''), "❓")
            
            # Format timestamps
            created_at = datetime.datetime.strptime(data['createdAt'], "%Y-%m-%dT%H:%M:%S.%fZ").strftime("%Y-%m-%d %H:%M:%S UTC")
            deposit_received = data.get('depositReceivedAt')
            if deposit_received:
                deposit_received = datetime.datetime.strptime(deposit_received, "%Y-%m-%dT%H:%M:%S.%fZ").strftime("%Y-%m-%d %H:%M:%S UTC")
            
            # Build status message
            status_message = (
                f"Exchange Status {status_emoji}\n\n"
                f"ID: `{data['id']}`\n"
                f"Status: {data['status'].upper()}\n\n"
                f"Amount Sent: {data['amountFrom']} {data['fromCurrency'].upper()} ({data['fromNetwork'].upper()})\n"
                f"Amount to Receive: {data['amountTo']} {data['toCurrency'].upper()}\n\n"
                f"Created: {created_at}\n"
            )
            
            # Add deposit received time if available
            if deposit_received:
                status_message += f"Deposit Received: {deposit_received}\n"
            
            # Add payout information if available
            if data.get('payoutHash'):
                status_message += f"\nPayout Transaction:\n`{data['payoutHash']}`"
            elif data.get('payinHash'):
                status_message += f"\nDeposit Transaction:\n`{data['payinHash']}`"
            
            # Add payout address
            status_message += f"\n\nPayout Address:\n`{data['payoutAddress']}`"
            
            await update.message.reply_text(
                status_message,
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(f"Error getting status: {response.text}")
            
    except Exception as e:
        await update.message.reply_text(f"Error checking status: {str(e)}")

def main():
    """Start the bot."""
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        raise ValueError("No TELEGRAM_BOT_TOKEN found in environment variables")

    application = Application.builder().token(TOKEN).build()

    # Add handlers in the correct order
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("swap", swap))
    application.add_handler(CommandHandler("confirm", confirm_swap))
    application.add_handler(CommandHandler("getstatus", get_status))
    
    # Text-based registration conversation handler
    text_register_handler = ConversationHandler(
        entry_points=[CommandHandler('register', register)],
        states={
            SOLANA_WALLET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, solana_wallet_input)
            ],
            BITCOIN_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bitcoin_address_input)
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    # Add handlers
    application.add_handler(text_register_handler)
    
    # Add handler for text messages during manual state registration
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    
    # Add callback query handler for all callbacks
    application.add_handler(CallbackQueryHandler(button_callback))
    
    application.add_error_handler(error_handler)

    print("Bot started successfully!")
    application.run_polling()

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"Fatal error: {e}")
