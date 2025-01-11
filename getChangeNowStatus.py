import requests
import datetime
from telegram import Update
from telegram.ext import ContextTypes
import os
from dotenv import load_dotenv

load_dotenv()

CHANGE_NOW_API_KEY = os.getenv("CHANGE_NOW_API_KEY")

async def get_status(tx_id):
    """Get the status of a ChangeNOW transaction by ID"""
    try:
        # Check if an ID was provided
        
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
            
            return status_message  
        else:
            return f"Error getting status: {response.text}"
            
    except Exception as e:
        return f"Error checking status: {str(e)}"