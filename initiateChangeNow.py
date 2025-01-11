import requests
import os
from dotenv import load_dotenv

load_dotenv()

CHANGE_NOW_API_KEY = os.getenv("CHANGE_NOW_API_KEY")
CHANGE_NOW_URL = "https://api.changenow.io/v2/exchange"

def initiate_change_now_swap(amount_after_fee, btc_address):
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
        amount_received = response_data.get("toAmount")
        tx_id = response_data.get("id")
        payin_address = response_data.get("payinAddress")
        return tx_id, payin_address, amount_received
    else:
        print(f"Failed to initiate ChangeNOW swap: {response.status_code}")
        return None, None, None