import re

def is_valid_bitcoin_address(address):
    # Validate Bitcoin address (P2PKH, P2SH, or Bech32)
    return re.match(r"^(bc1|[13])[a-zA-HJ-NP-Z0-9]{25,39}$", address) is not None