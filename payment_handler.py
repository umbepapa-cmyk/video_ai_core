"""
WEEK 4 - DAY 22: Payment Gateway Integration
=============================================
Handles payment webhooks from high-risk gateways (CCBill, Segpay, Epoch).

Features:
- Webhook signature validation
- Payment notification processing
- Automatic credit top-up
- Transaction logging
"""

import os
import hmac
import hashlib
import logging
from typing import Dict, Optional
from datetime import datetime
from dotenv import load_dotenv

from database import SupabaseClient

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PaymentGatewayError(Exception):
    """Exception for payment gateway errors."""
    pass


class PaymentHandler:
    """
    Handler for high-risk payment gateway integrations.
    
    Supported providers:
    - CCBill
    - Segpay
    - Epoch
    """
    
    PROVIDERS = {
        "ccbill": {
            "webhook_url": "/webhooks/ccbill",
            "secret_key_env": "CCBILL_WEBHOOK_SECRET"
        },
        "segpay": {
            "webhook_url": "/webhooks/segpay",
            "secret_key_env": "SEGPAY_WEBHOOK_SECRET"
        },
        "epoch": {
            "webhook_url": "/webhooks/epoch",
            "secret_key_env": "EPOCH_WEBHOOK_SECRET"
        }
    }
    
    # Package ID to credits mapping
    CREDIT_PACKAGES = {
        "basic_19.99": 350,
        "pro_39.99": 800,
        "premium_79.99": 1800,
        "ultimate_149.99": 4000
    }
    
    def __init__(self, provider: str):
        """
        Initialize payment handler for specific provider.
        
        Args:
            provider: Payment gateway name (ccbill, segpay, epoch)
        """
        if provider not in self.PROVIDERS:
            raise ValueError(f"Unsupported provider: {provider}")
        
        self.provider = provider
        self.secret = os.getenv(self.PROVIDERS[provider]["secret_key_env"])
        
        if not self.secret:
            logger.warning(f"No secret key found for {provider} - using mock mode")
            self.secret = "mock_secret_for_testing"
        
        self.db_client = SupabaseClient()
        
        logger.info(f"PaymentHandler initialized for {provider}")
    
    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """
        Verify webhook signature using HMAC-SHA256.
        
        Args:
            payload: Raw webhook payload (bytes)
            signature: Signature from webhook header
        
        Returns:
            True if signature is valid, False otherwise
        """
        try:
            # Calculate expected signature
            expected = hmac.new(
                self.secret.encode(),
                payload,
                hashlib.sha256
            ).hexdigest()
            
            # Compare using constant-time comparison to prevent timing attacks
            is_valid = hmac.compare_digest(expected, signature)
            
            if not is_valid:
                logger.warning(f"Invalid webhook signature for {self.provider}")
            
            return is_valid
            
        except Exception as e:
            logger.error(f"Signature verification error: {e}")
            return False
    
    def process_payment_notification(self, data: Dict) -> Dict:
        """
        Process payment notification and credit user account.
        
        Args:
            data: Webhook payload data
        
        Returns:
            Processing result dictionary
        
        Raises:
            PaymentGatewayError: If processing fails
        """
        try:
            # Extract payment data
            package_id = data.get("package_id")
            user_email = data.get("email")
            transaction_id = data.get("transaction_id")
            amount = data.get("amount", 0)
            currency = data.get("currency", "USD")
            
            logger.info(f"Processing payment: {transaction_id} for {user_email}")
            logger.info(f"Package: {package_id}, Amount: {amount} {currency}")
            
            # Validate required fields
            if not all([package_id, user_email, transaction_id]):
                raise PaymentGatewayError("Missing required payment fields")
            
            # Map package to credits
            credits = self.CREDIT_PACKAGES.get(package_id, 0)
            
            if credits == 0:
                logger.warning(f"Unknown package_id: {package_id}, defaulting to 0 credits")
                raise PaymentGatewayError(f"Unknown package: {package_id}")
            
            # Find user by email
            try:
                user = self.db_client.get_user_profile_by_email(user_email)
            except Exception as e:
                logger.error(f"User not found: {user_email}")
                raise PaymentGatewayError(f"User not found: {user_email}")
            
            # Credit user account using RPC (atomic operation)
            try:
                result = self.db_client.client.rpc("add_credits_secure", {
                    "p_user_id": str(user.user_id),
                    "p_amount": credits,
                    "p_transaction_id": transaction_id
                }).execute()
                
                if not result.data:
                    raise PaymentGatewayError("Failed to credit account")
                
                result_data = result.data
                
                logger.info(f"Credits added: {credits} for user {user_email}")
                logger.info(f"New balance: {result_data.get('new_balance', 'unknown')}")
                
                return {
                    "success": True,
                    "user_id": str(user.user_id),
                    "user_email": user_email,
                    "credits_added": credits,
                    "new_balance": result_data.get("new_balance", 0),
                    "transaction_id": transaction_id,
                    "package_id": package_id,
                    "provider": self.provider,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
            except Exception as e:
                logger.error(f"Failed to credit account: {e}")
                raise PaymentGatewayError(f"Failed to credit account: {e}")
            
        except PaymentGatewayError:
            raise
        except Exception as e:
            logger.error(f"Payment processing error: {e}")
            raise PaymentGatewayError(f"Payment processing failed: {e}")
    
    def log_webhook_event(
        self,
        event_type: str,
        data: Dict,
        success: bool,
        error: Optional[str] = None
    ):
        """
        Log webhook event to database for audit trail.
        
        Args:
            event_type: Type of webhook event
            data: Event data
            success: Whether processing succeeded
            error: Error message if failed
        """
        try:
            self.db_client.client.table("payment_webhooks").insert({
                "provider": self.provider,
                "event_type": event_type,
                "transaction_id": data.get("transaction_id"),
                "user_email": data.get("email"),
                "package_id": data.get("package_id"),
                "amount": data.get("amount"),
                "success": success,
                "error_message": error,
                "raw_data": data,
                "created_at": datetime.utcnow().isoformat()
            }).execute()
            
        except Exception as e:
            logger.error(f"Failed to log webhook event: {e}")


def create_payment_handler(provider: str) -> PaymentHandler:
    """
    Factory function to create payment handler.
    
    Args:
        provider: Payment gateway name
    
    Returns:
        PaymentHandler instance
    """
    return PaymentHandler(provider)


# ============================================================================
# WEBHOOK SIGNATURE HELPERS
# ============================================================================

def verify_ccbill_signature(payload: bytes, signature: str) -> bool:
    """Verify CCBill webhook signature."""
    handler = PaymentHandler("ccbill")
    return handler.verify_webhook_signature(payload, signature)


def verify_segpay_signature(payload: bytes, signature: str) -> bool:
    """Verify Segpay webhook signature."""
    handler = PaymentHandler("segpay")
    return handler.verify_webhook_signature(payload, signature)


def verify_epoch_signature(payload: bytes, signature: str) -> bool:
    """Verify Epoch webhook signature."""
    handler = PaymentHandler("epoch")
    return handler.verify_webhook_signature(payload, signature)


if __name__ == "__main__":
    print(f"\n{'='*70}")
    print("PAYMENT HANDLER - WEEK 4 DAY 22")
    print(f"{'='*70}\n")
    
    print("Supported Payment Gateways:")
    print("-" * 70)
    for provider, config in PaymentHandler.PROVIDERS.items():
        print(f"  - {provider.upper()}: {config['webhook_url']}")
    
    print()
    print("Credit Packages:")
    print("-" * 70)
    for package, credits in PaymentHandler.CREDIT_PACKAGES.items():
        print(f"  - {package}: {credits} credits")
    
    print()
    print("Environment Variables Required:")
    print("-" * 70)
    for provider, config in PaymentHandler.PROVIDERS.items():
        env_var = config["secret_key_env"]
        value = os.getenv(env_var)
        status = "✓ Set" if value else "✗ Not set"
        print(f"  - {env_var}: {status}")
    
    print(f"\n{'='*70}\n")
    
    # Test signature verification
    print("Testing signature verification...")
    handler = PaymentHandler("ccbill")
    
    test_payload = b'{"email":"test@example.com","package_id":"basic_19.99"}'
    test_signature = hmac.new(
        handler.secret.encode(),
        test_payload,
        hashlib.sha256
    ).hexdigest()
    
    is_valid = handler.verify_webhook_signature(test_payload, test_signature)
    print(f"Signature verification test: {'PASSED' if is_valid else 'FAILED'}")
    
    print()
