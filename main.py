import os
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
from pymongo import MongoClient
from solana.rpc.api import Client
from datetime import datetime, UTC
from utils.validateBtcAddress import is_valid_bitcoin_address
from utils.validateSolAddress import is_valid_solana_address
from utils.getRatePreview import get_rate_preview
from utils.createSwap import create_signed_jupiter_swap_tx
from utils.initiateChangeNow import initiate_change_now_swap
from utils.createTransfer import create_signed_usdc_transfer_tx
from utils.getMinimumAmt import get_min_amount
from jito.bundle import BundleStatus, send_bundle_with_tip
from utils.getChangeNowStatus import get_status
from utils.verifyDeposit import verify_usdc_deposit

class ChangeNowError(Exception):
    pass

class SolanaTransactionError(Exception):
    pass

# Load environment variables
load_dotenv()

# Constants and Configurations
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL")
client = Client(SOLANA_RPC_URL)
CHANGE_NOW_URL = "https://api.changenow.io/v2/exchange"
CHANGE_NOW_API_KEY = os.getenv("CHANGE_NOW_API_KEY")
MONGO_URI = os.getenv("MONGO_URI")
INTERMEDIARY_SOL_WALLET = os.getenv("INTERMEDIARY_SOL_WALLET")
USDC_MINT = os.getenv("USDC_MINT")
TARGET_TOKEN_MINT_ADDRESS = os.getenv("TARGET_TOKEN_MINT_ADDRESS")
USDC_DECIMALS = os.getenv("USDC_DECIMALS")
PRESET_AMOUNTS = [100, 500, 1000, 5000]
MAX_AMOUNT = 1000000
FEE_PERCENTAGE = 0.05

# MongoDB setup
client_db = MongoClient(MONGO_URI, tls=True, tlsAllowInvalidCertificates=True)
db = client_db["telegram_bot"]
users_collection = db["users"]

# Conversation states
SOLANA_WALLET, BITCOIN_ADDRESS, CUSTOM_AMOUNT = range(3)

# Core Command Handlers
async def start(update, context):
    keyboard = [
        [InlineKeyboardButton("Register Wallets", callback_data='register')],
        [InlineKeyboardButton("Start New Swap", callback_data='swap')],
        [InlineKeyboardButton("Help", callback_data='help')]
    ]
    await update.message.reply_text(
        "Welcome to the USDC-BTC Swap Bot! 🤖\n\nWhat would you like to do?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Let's register your wallet addresses! 📝\n\n"
        "First, please enter your Solana wallet address:"
    )
    return SOLANA_WALLET


async def solana_wallet_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    solana_address = update.message.text.strip()
    
    if not is_valid_solana_address(solana_address):
        await update.message.reply_text(
            "❌ Invalid Solana address. Please enter a valid Solana wallet address or /cancel to abort."
        )
        return SOLANA_WALLET
    
    # Store the Solana address temporarily in context
    context.user_data['solana_address'] = solana_address
    
    await update.message.reply_text(
        "✅ Solana address saved!\n\n"
        "Now, please enter your Bitcoin address where you'll receive BTC:"
    )
    return BITCOIN_ADDRESS

async def bitcoin_address_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bitcoin_address = update.message.text.strip()
    
    if not is_valid_bitcoin_address(bitcoin_address):
        await update.message.reply_text(
            "❌ Invalid Bitcoin address. Please enter a valid Bitcoin address or /cancel to abort."
        )
        return BITCOIN_ADDRESS
    
    # Get the previously stored Solana address
    solana_address = context.user_data.get('solana_address')
    
    # Store both addresses in MongoDB
    users_collection.update_one(
        {"_id": user_id},
        {
            "$set": {
                "solana_address": solana_address,
                "bitcoin_address": bitcoin_address,
                "updated_at": datetime.now(UTC)
            }
        },
        upsert=True
    )
    
    # Clear temporary data
    context.user_data.clear()
    
    await update.message.reply_text(
        "✅ Registration complete!\n\n"
        "Your addresses have been saved:\n"
        f"🔹 Solana: `{solana_address}`\n"
        f"🔹 Bitcoin: `{bitcoin_address}`\n\n"
        "You can now use /swap to start a new swap transaction.",
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Registration cancelled. You can start over with /register"
    )
    return ConversationHandler.END



async def swap(update, context):
    user_id = update.effective_user.id
    user = users_collection.find_one({"_id": user_id})

    if not user:
        await update.message.reply_text(
            "❌ Please register your wallets first using /register"
        )
        return

    # If no amount provided, show preset amounts
    if not context.args:
        keyboard = [
            [InlineKeyboardButton(f"${amt:,} USDC", callback_data=f"swap_{amt}") 
             for amt in PRESET_AMOUNTS[i:i+2]]
            for i in range(0, len(PRESET_AMOUNTS), 2)
        ]
        keyboard.append([InlineKeyboardButton("Custom Amount", callback_data="swap_custom")])
        
        await update.message.reply_text(
            "💱 Select swap amount or enter custom amount:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    try:
        amount = float(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number")
        return
    
    if amount > MAX_AMOUNT:
        await update.message.reply_text(
            f"❌ Amount exceeds maximum limit of ${MAX_AMOUNT:,} USDC"
        )
        return

    # Show swap preview with confirmation buttons
    fee = amount * FEE_PERCENTAGE
    amount_after_fee = amount - fee

    try:
        minimum_amount = await get_min_amount("usdc", "btc", "sol", "btc")
        total_min_amount = minimum_amount + fee
        if amount < total_min_amount:
            await update.message.reply_text(
                f"❌ Amount too low. Minimum amount after fees is ${total_min_amount:,.2f} USDC"
            )
            return

        rate_preview = await get_rate_preview(amount_after_fee)
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm", callback_data=f"confirm_swap_{amount}"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_swap")
            ]
        ]

        await update.message.reply_text(
            f"📊 *Swap Preview*\n\n"
            f"💰 Amount: `${amount:,.2f}` USDC\n"
            f"📊 Fee ({FEE_PERCENTAGE*100}%): `${fee:,.2f}` USDC\n"
            f"💱 Amount after fee: `${amount_after_fee:,.2f}` USDC\n"
            f"🔄 Expected BTC: `{format(rate_preview, '.8f')}` BTC\n\n"
            f"Please confirm to proceed with the swap.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        await update.message.reply_text(f"❌ An error occurred: {str(e)}")
        return

async def check_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(context.args[0]) if context.args else 100  # Default amount
        rate_preview = await get_rate_preview(amount)
        await update.message.reply_text(
            f"Current Rate Preview:\n"
            f"{amount:,.2f} USDC ≈ {format(rate_preview, '.8f')} BTC"
        )
    except Exception as e:
        await update.message.reply_text("Error fetching rate. Please try again.")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith('swap_'):
        if query.data == 'swap_custom':
            await query.message.reply_text(
                "Please enter the amount of USDC you want to swap:",
            )
            return CUSTOM_AMOUNT  # Return the new state
        else:
            amount = float(query.data.split('_')[1])
            context.args = [str(amount)]
            await swap(query, context)
            
    elif query.data.startswith('confirm_swap_'):
        try:
            amount = float(query.data.split('_')[2])
            # First remove the inline keyboard
            await query.message.edit_reply_markup(reply_markup=None)
            # Then process the swap
            await process_swap(query.message, amount, context)
        except Exception as e:
            await query.message.reply_text(f"❌ Error processing swap: {str(e)}")
        
    elif query.data == 'cancel_swap':
        await query.message.edit_text("❌ Swap cancelled")
        
    elif query.data == 'register':
        await query.message.reply_text(
            "Let's register your wallet addresses! 📝\n\n"
            "First, please enter your Solana wallet address:"
        )
        return SOLANA_WALLET
        
    elif query.data == 'swap':
        print(query)
        user_id = query.from_user.id
        user = users_collection.find_one({"_id": user_id})
        if not user:
            await query.message.reply_text("Please register first using /register")
            return
        await query.message.reply_text("Please use the /swap <amount> command to start a swap")
        
    elif query.data == 'help':
        help_text = (
            "🔹 Use /register to set up your wallet addresses\n"
            "🔹 Use /swap to start a new swap\n"
            "🔹 Use /getstatus to check swap status\n"
            "🔹 Use /cancel to cancel any ongoing operation"
        )
        await query.message.reply_text(help_text)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors in the telegram bot."""
    # Log the error
    print(f"Update {update} caused error {context.error}")
    
    # Send a message to the user
    error_message = (
        "❌ An error occurred while processing your request.\n"
        "Please try again later or contact support if the issue persists."
    )
    
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(error_message)
    except Exception as e:
        print(f"Failed to send error message: {e}")

async def get_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    user = users_collection.find_one({"_id": user_id})
    
    if not user or "transactions" not in user:
        await update.message.reply_text("No transaction history found.")
        return
        
    history = "📜 Transaction History:\n\n"
    for tx in user["transactions"][-5:]:  # Show last 5 transactions
        history += (
            f"Amount: {tx['amount_usdc']} USDC → {tx['amount_btc']} BTC\n"
            f"Status: {tx['status'].upper()}\n"
            f"Date: {tx['timestamp'].strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
        )
    
    await update.message.reply_text(history)

async def get_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the getstatus command with transaction ID"""
    # Check if transaction ID was provided
    if not context.args:
        await update.message.reply_text(
            "Please provide a transaction ID: /getstatus <transaction_id>"
        )
        return
        
    tx_id = context.args[0]
    
    try:
        statusMessage = await get_status(tx_id)
        await update.message.reply_text(statusMessage)
    except Exception as e:
        await update.message.reply_text(f"❌ Error fetching status: {str(e)}")

# New function to process the actual swap
async def process_swap(message, amount, context):
    user_id = message.chat.id  # Changed from message.from_user.id
    user = users_collection.find_one({"_id": user_id})
    
    if not user:
        await message.reply_text("❌ Please register first using /register")
        return
        
    if "sol_wallet" not in user or "btc_address" not in user:
        await message.reply_text("❌ Please register both your Solana and Bitcoin addresses first")
        return

    progress_message = await message.reply_text(
        f"🔄 Processing swap\.\.\.\n\n⏳ Step 1/4: Verifying deposit\.\.\.\nPlease send USDC to the address below:\n```{INTERMEDIARY_SOL_WALLET}```",
        parse_mode="MarkdownV2"
    )
    
    try:
        fee = amount * FEE_PERCENTAGE
        amount_after_fee = amount - fee

        # Verify deposit - updated to use correct field name
        deposit_verified = await verify_usdc_deposit(amount_after_fee, user["sol_wallet"], users_collection)
        
        if not deposit_verified:
            await progress_message.edit_text(
                "❌ Deposit verification timed out after 10 minutes.\n"
                "The swap has been cancelled. Please try again with a new swap."
            )
            return

        await progress_message.edit_text(
            "🔄 Processing swap...\n\n"
            "✅ Step 1/4: Deposit verified\n"
            "⏳ Step 2/4: Getting rate preview..."
        )

        rate_preview = await get_rate_preview(amount_after_fee)
        
        await progress_message.edit_text(
            "🔄 Processing swap...\n\n"
            "✅ Step 1/4: Deposit verified\n"
            "✅ Step 2/4: Rate confirmed\n"
            "⏳ Step 3/4: Creating transactions..."
        )

        # Execute transactions
        signed_tx = await create_signed_jupiter_swap_tx(3)
        
        # Initialize ChangeNOW Swap
        tx_id, payin_address, amount_received = initiate_change_now_swap(amount_after_fee, user["btc_address"])

        if abs(amount_received - rate_preview) / rate_preview > 0.20:
            await progress_message.edit_text(
                "❌ Rate changed significantly. Please try again.\n"
                f"Expected: {format(rate_preview, '.8f')} BTC\n"
                f"Current: {format(amount_received, '.8f')} BTC\n"
                f"Percentage difference: {abs(amount_received - rate_preview) / rate_preview * 100:.2f}%"
            )
            return

        await progress_message.edit_text(
            "🔄 Processing swap...\n\n"
            "✅ Step 1/4: Deposit verified\n"
            "✅ Step 2/4: Rate confirmed\n"
            "✅ Step 3/4: Transactions created\n"
            "⏳ Step 4/4: Executing swap..."
        )

        signed_transfer_tx = await create_signed_usdc_transfer_tx(
            USDC_MINT, 
            USDC_DECIMALS, 
            payin_address, 
            amount_after_fee
        )
        
        bundle_id, status, landed_slot = await send_bundle_with_tip([signed_tx, signed_transfer_tx], 1000000)
        
        if status == BundleStatus.LANDED:
            await progress_message.edit_text(
                "✅ Swap initiated successfully!\n\n"
                f"Transaction ID: `{tx_id}`\n\n"
                "Use /getstatus {tx_id} to check the status of your swap.",
                parse_mode='Markdown'
            )
            # Update database - use correct field names in the query
            users_collection.update_one(
                {"_id": user_id},
                {
                    "$push": {
                        "transactions": {
                            "amount_usdc": amount,
                            "amount_btc": rate_preview,
                            "status": "pending",
                            "timestamp": datetime.now(UTC),
                            "bundle_id": bundle_id,
                            "change_now_tx_id": tx_id,
                            "landed_slot": landed_slot
                        }
                    }
                }
            )
        else:
            await progress_message.edit_text(
                "❌ Swap failed. Please try again."
            )

    except Exception as e:
        await progress_message.edit_text(
            f"❌ Swap failed\n\n"
            f"Error: {str(e)}\n\n"
            f"Please try again or contact support if the issue persists."
        )

# Add this new handler function
async def custom_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        context.args = [str(amount)]
        await swap(update, context)
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number")
    return ConversationHandler.END

# Main Application Setup
def main():
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    application = Application.builder().token(TOKEN).build()

    # Core handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("swap", swap))
    application.add_handler(CommandHandler("getstatus", get_status_command))
    application.add_handler(CommandHandler("checkrate", check_rate))
    application.add_handler(CommandHandler("history", get_history))

    # Registration conversation handler
    register_handler = ConversationHandler(
        entry_points=[
            CommandHandler('register', register),
            CallbackQueryHandler(button_callback, pattern='^swap_custom$')
        ],
        states={
            SOLANA_WALLET: [MessageHandler(filters.TEXT & ~filters.COMMAND, solana_wallet_input)],
            BITCOIN_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, bitcoin_address_input)],
            CUSTOM_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_amount_input)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    application.add_handler(register_handler)

    # Button callbacks and error handling
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_error_handler(error_handler)

    application.run_polling()

if __name__ == '__main__':
    main()
