# API URLs
CHANGE_NOW_URL = "https://api.changenow.io/v2/exchange"
JUPITER_QUOTE_API_URL = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_API_URL = "https://quote-api.jup.ag/v6/swap"

# Token Constants
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDC_DECIMALS = 6

# Rate Limiting
MAX_REQUESTS_PER_MINUTE = 5
CACHE_TTL = 60  # seconds

# Circuit Breaker
FAILURE_THRESHOLD = 5
RESET_TIMEOUT = 60  # seconds

# Swap Settings
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
MAX_SWAP_AMOUNT = 100_000
SWAP_FEE_PERCENTAGE = 0.005  # 0.5%
SLIPPAGE_BPS = 100

# Prometheus Metrics Port
METRICS_PORT = 8000 