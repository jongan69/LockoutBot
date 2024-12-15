from pydantic_settings import BaseSettings
from pydantic import SecretStr, validator
import logging

logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    # API Keys and Secrets
    TELEGRAM_BOT_TOKEN: SecretStr
    CHANGE_NOW_API_KEY: SecretStr
    PRIVATE_KEY: SecretStr
    
    # Connection URLs
    MONGO_URI: SecretStr
    SOLANA_RPC_URL: str
    
    # Wallet and Token Settings
    INTERMEDIARY_SOL_WALLET: str
    TARGET_TOKEN_ADDRESS: str
    
    @validator('TELEGRAM_BOT_TOKEN')
    def validate_telegram_token(cls, v):
        token = v.get_secret_value()
        parts = token.split(':')
        
        if len(parts) != 2:
            raise ValueError("Token must contain exactly one colon")
        if not parts[0].isdigit():
            raise ValueError("Token prefix must be numeric")
        if len(parts[1]) < 20:
            raise ValueError("Token suffix too short")
            
        return v
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings() 