import requests
    
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