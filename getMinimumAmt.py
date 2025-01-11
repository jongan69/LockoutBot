import requests
import os
from dotenv import load_dotenv

load_dotenv()

CHANGE_NOW_API_KEY = os.getenv("CHANGE_NOW_API_KEY")

async def get_min_amount(from_currency, to_currency, from_network, to_network):
    """
    Check minimum allowed amount for exchange using ChangeNOW API
    """
    try:
        min_amount_url = "https://api.changenow.io/v2/exchange/min-amount"
        params = {
            "fromCurrency": from_currency,
            "toCurrency": to_currency,
            "fromNetwork": from_network,
            "toNetwork": to_network,
            "flow": "standard"
        }
        headers = {
            "x-changenow-api-key": CHANGE_NOW_API_KEY
        }

        print("\n=== Checking Minimum Amount ===")
        print(f"Parameters: {params}")
        
        response = requests.get(min_amount_url, params=params, headers=headers)
        print(f"Min amount response status: {response.status_code}")
        print(f"Min amount response: {response.text}")

        if response.status_code == 200:
            data = response.json()
            return data.get('minAmount')
        else:
            print(f"Error getting minimum amount: {response.text}")
            return None
    except Exception as e:
        print(f"Error checking minimum amount: {str(e)}")
        return None