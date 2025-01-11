import re

def is_valid_solana_address(address):
    """
    Validates Solana wallet addresses using regex
    - Must be base58 encoded (1-9, A-H, J-N, P-Z, a-k, m-z)
    - Length between 32-44 characters
    """
    return re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", address) is not None