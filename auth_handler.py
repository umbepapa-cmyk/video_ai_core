"""
WEEK 3 - DAY 20: Authentication Handler
=========================================
JWT authentication using Supabase Auth.

Features:
- User sign-up / sign-in
- JWT token management
- Session validation
- Password reset
- OAuth providers support (future)
"""

import os
import logging
from typing import Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

from dotenv import load_dotenv
from supabase import create_client, Client
from supabase.lib.client_options import ClientOptions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Exception for authentication errors."""
    pass


@dataclass
class UserSession:
    """Data structure for user session."""
    access_token: str
    refresh_token: str
    expires_at: int
    user_id: str
    email: str


@dataclass
class AuthUser:
    """Data structure for authenticated user."""
    user_id: str
    email: str
    email_confirmed: bool
    created_at: str
    user_metadata: dict


class AuthHandler:
    """
    Supabase Auth handler for JWT-based authentication.
    
    Uses Supabase Auth with:
    - Email/password authentication
    - JWT tokens (access + refresh)
    - Automatic token refresh
    - Secure session management
    """
    
    def __init__(
        self,
        url: Optional[str] = None,
        anon_key: Optional[str] = None
    ):
        """
        Initialize auth handler.
        
        Args:
            url: Supabase project URL
            anon_key: Supabase anon key (public key for client-side auth)
        
        Note:
            Use ANON_KEY (not SERVICE_ROLE_KEY) for client authentication.
        """
        load_dotenv()
        
        self.url = url or os.getenv("SUPABASE_URL")
        self.anon_key = anon_key or os.getenv("SUPABASE_ANON_KEY")
        
        if not self.url:
            raise AuthError("SUPABASE_URL not provided")
        
        if not self.anon_key:
            raise AuthError("SUPABASE_ANON_KEY not provided")
        
        self.client: Client = create_client(
            self.url,
            self.anon_key,
            options=ClientOptions(
                auto_refresh_token=True,
                persist_session=False  # For server-side, use False
            )
        )
        
        logger.info(f"Auth handler initialized: {self.url}")
    
    # ========================================================================
    # SIGN UP / SIGN IN
    # ========================================================================
    
    def sign_up(
        self,
        email: str,
        password: str,
        user_metadata: Optional[dict] = None
    ) -> Tuple[bool, str, Optional[AuthUser]]:
        """
        Register new user.
        
        Args:
            email: User email
            password: User password (min 6 chars)
            user_metadata: Optional metadata (NOT for authorization - use app_metadata)
        
        Returns:
            Tuple of (success: bool, message: str, user: Optional[AuthUser])
        """
        try:
            if len(password) < 6:
                return (False, "Password must be at least 6 characters", None)
            
            response = self.client.auth.sign_up({
                "email": email,
                "password": password,
                "options": {
                    "data": user_metadata or {}
                }
            })
            
            if not response.user:
                return (False, "Sign up failed", None)
            
            user = AuthUser(
                user_id=response.user.id,
                email=response.user.email or email,
                email_confirmed=response.user.email_confirmed_at is not None,
                created_at=response.user.created_at or datetime.utcnow().isoformat(),
                user_metadata=response.user.user_metadata or {}
            )
            
            logger.info(f"User registered: {email} ({user.user_id})")
            
            return (True, "Registration successful", user)
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Sign up error: {error_msg}")
            
            if "already registered" in error_msg.lower():
                return (False, "Email already registered", None)
            
            return (False, f"Sign up error: {error_msg}", None)
    
    def sign_in(
        self,
        email: str,
        password: str
    ) -> Tuple[bool, str, Optional[UserSession]]:
        """
        Authenticate user and create session.
        
        Args:
            email: User email
            password: User password
        
        Returns:
            Tuple of (success: bool, message: str, session: Optional[UserSession])
        """
        try:
            response = self.client.auth.sign_in_with_password({
                "email": email,
                "password": password
            })
            
            if not response.session or not response.user:
                return (False, "Authentication failed", None)
            
            session = UserSession(
                access_token=response.session.access_token,
                refresh_token=response.session.refresh_token,
                expires_at=response.session.expires_at or 0,
                user_id=response.user.id,
                email=response.user.email or email
            )
            
            logger.info(f"User signed in: {email} ({session.user_id})")
            
            return (True, "Sign in successful", session)
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Sign in error: {error_msg}")
            
            if "invalid" in error_msg.lower() or "credentials" in error_msg.lower():
                return (False, "Invalid email or password", None)
            
            return (False, f"Sign in error: {error_msg}", None)
    
    def sign_out(self, access_token: str) -> Tuple[bool, str]:
        """
        Sign out user (invalidate session).
        
        Args:
            access_token: User's access token
        
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            # Set token for the request
            self.client.auth.set_session(access_token, "")
            
            self.client.auth.sign_out()
            
            logger.info("User signed out")
            
            return (True, "Sign out successful")
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Sign out error: {error_msg}")
            return (False, f"Sign out error: {error_msg}")
    
    # ========================================================================
    # SESSION MANAGEMENT
    # ========================================================================
    
    def get_user_from_token(
        self,
        access_token: str
    ) -> Tuple[bool, str, Optional[AuthUser]]:
        """
        Validate JWT token and retrieve user.
        
        Args:
            access_token: JWT access token
        
        Returns:
            Tuple of (valid: bool, message: str, user: Optional[AuthUser])
        """
        try:
            # Set token
            self.client.auth.set_session(access_token, "")
            
            # Get user
            response = self.client.auth.get_user(access_token)
            
            if not response.user:
                return (False, "Invalid token", None)
            
            user = AuthUser(
                user_id=response.user.id,
                email=response.user.email or "",
                email_confirmed=response.user.email_confirmed_at is not None,
                created_at=response.user.created_at or datetime.utcnow().isoformat(),
                user_metadata=response.user.user_metadata or {}
            )
            
            return (True, "Token valid", user)
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Token validation error: {error_msg}")
            return (False, f"Invalid or expired token: {error_msg}", None)
    
    def refresh_session(
        self,
        refresh_token: str
    ) -> Tuple[bool, str, Optional[UserSession]]:
        """
        Refresh session using refresh token.
        
        Args:
            refresh_token: JWT refresh token
        
        Returns:
            Tuple of (success: bool, message: str, new_session: Optional[UserSession])
        """
        try:
            response = self.client.auth.refresh_session(refresh_token)
            
            if not response.session or not response.user:
                return (False, "Session refresh failed", None)
            
            session = UserSession(
                access_token=response.session.access_token,
                refresh_token=response.session.refresh_token,
                expires_at=response.session.expires_at or 0,
                user_id=response.user.id,
                email=response.user.email or ""
            )
            
            logger.info(f"Session refreshed: {session.user_id}")
            
            return (True, "Session refreshed", session)
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Session refresh error: {error_msg}")
            return (False, f"Refresh error: {error_msg}", None)
    
    # ========================================================================
    # PASSWORD MANAGEMENT
    # ========================================================================
    
    def reset_password_request(self, email: str) -> Tuple[bool, str]:
        """
        Request password reset email.
        
        Args:
            email: User email
        
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            self.client.auth.reset_password_email(email)
            
            logger.info(f"Password reset requested: {email}")
            
            return (True, "Password reset email sent")
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Password reset error: {error_msg}")
            return (False, f"Reset error: {error_msg}")
    
    def update_password(
        self,
        access_token: str,
        new_password: str
    ) -> Tuple[bool, str]:
        """
        Update user password (requires valid session).
        
        Args:
            access_token: User's access token
            new_password: New password (min 6 chars)
        
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            if len(new_password) < 6:
                return (False, "Password must be at least 6 characters")
            
            # Set token
            self.client.auth.set_session(access_token, "")
            
            self.client.auth.update_user({
                "password": new_password
            })
            
            logger.info("Password updated")
            
            return (True, "Password updated successfully")
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Password update error: {error_msg}")
            return (False, f"Update error: {error_msg}")


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

_auth_handler: Optional[AuthHandler] = None


def get_auth_handler() -> AuthHandler:
    """Get singleton auth handler instance."""
    global _auth_handler
    if _auth_handler is None:
        _auth_handler = AuthHandler()
    return _auth_handler


def sign_up(email: str, password: str) -> Tuple[bool, str, Optional[AuthUser]]:
    """Register new user."""
    return get_auth_handler().sign_up(email, password)


def sign_in(email: str, password: str) -> Tuple[bool, str, Optional[UserSession]]:
    """Authenticate user."""
    return get_auth_handler().sign_in(email, password)


def sign_out(access_token: str) -> Tuple[bool, str]:
    """Sign out user."""
    return get_auth_handler().sign_out(access_token)


def validate_token(access_token: str) -> Tuple[bool, str, Optional[AuthUser]]:
    """Validate JWT token."""
    return get_auth_handler().get_user_from_token(access_token)


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print("AUTH HANDLER - DAY 20")
    print(f"{'='*60}\n")
    
    print("Features:")
    print("✓ Email/password authentication")
    print("✓ JWT token management")
    print("✓ Session validation")
    print("✓ Password reset")
    print("✓ Automatic token refresh")
    print()
    
    print("Setup Instructions:")
    print("-" * 60)
    print("1. Set environment variables in .env:")
    print("   SUPABASE_URL=https://your-project.supabase.co")
    print("   SUPABASE_ANON_KEY=your_anon_key")
    print()
    print("2. Enable Email Auth in Supabase Dashboard:")
    print("   Authentication > Providers > Email")
    print()
    
    print("Usage Example:")
    print("-" * 60)
    print("""
from auth_handler import sign_up, sign_in, validate_token

# Register user
success, msg, user = sign_up("user@example.com", "password123")

# Sign in
success, msg, session = sign_in("user@example.com", "password123")

if success:
    access_token = session.access_token
    
    # Validate token
    valid, msg, user = validate_token(access_token)
    
    if valid:
        print(f"User: {user.email}")
    """)
    
    print()
    
    try:
        handler = AuthHandler()
        print("✓ Auth handler initialized")
    except AuthError as e:
        print(f"✗ Auth initialization failed: {e}")
        print("\nSet SUPABASE_URL and SUPABASE_ANON_KEY first!")
