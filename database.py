import sqlite3
import os
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from cryptography.fernet import Fernet
import base64

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path=None):
        """
        Initialize database connection.
        Uses Railway volume path for persistence in production, local path for development.
        """
        # Use local path for development, Railway volume path for production
        if db_path is None:
            if os.path.exists("/data"):
                # Running in Railway with volume
                db_path = "/data/users.db"
            else:
                # Running locally
                db_path = "./users.db"

        # Ensure directory exists
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        self.db_path = db_path
        self.encryption_key = self._get_or_create_encryption_key()
        self.init_database()

    def _get_or_create_encryption_key(self) -> bytes:
        """Get or create encryption key for credential security."""
        # Use same directory as database
        key_file = os.path.join(os.path.dirname(self.db_path), "encryption.key")

        if os.path.exists(key_file):
            with open(key_file, 'rb') as f:
                return f.read()
        else:
            # Generate new key
            key = Fernet.generate_key()
            with open(key_file, 'wb') as f:
                f.write(key)
            return key

    def _encrypt(self, data: str) -> str:
        """Encrypt sensitive data."""
        f = Fernet(self.encryption_key)
        return f.encrypt(data.encode()).decode()

    def _decrypt(self, encrypted_data: str) -> str:
        """Decrypt sensitive data."""
        f = Fernet(self.encryption_key)
        return f.decrypt(encrypted_data.encode()).decode()

    def init_database(self):
        """Initialize database tables."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                aspen_username TEXT,
                aspen_password TEXT,
                notification_method TEXT DEFAULT 'telegram',
                email TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # User settings table for additional preferences
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                telegram_id INTEGER PRIMARY KEY,
                timezone TEXT DEFAULT 'America/Chicago',
                notification_frequency TEXT DEFAULT 'daily',
                notification_time TEXT DEFAULT '15:00',
                FOREIGN KEY (telegram_id) REFERENCES users (telegram_id)
            )
        ''')

        # Add notification_time column if it doesn't exist (for existing databases)
        try:
            cursor.execute('ALTER TABLE user_settings ADD COLUMN notification_time TEXT DEFAULT "15:00"')
            logger.info("Added notification_time column to existing user_settings table")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                logger.info("notification_time column already exists")
            else:
                logger.warning(f"Could not add notification_time column: {e}")

        # Fix existing users with invalid timestamps
        try:
            # Fix various invalid timestamp formats
            cursor.execute('''
                UPDATE users
                SET created_at = datetime('now')
                WHERE created_at IS NULL OR created_at = '' OR created_at = '1' OR created_at = 1
            ''')
            logger.info("Fixed invalid timestamps in existing users")
        except Exception as e:
            logger.warning(f"Could not fix timestamps: {e}")

        # Create feedback table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                first_name TEXT,
                feedback_type TEXT,
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        conn.commit()
        conn.close()
        logger.info("Database initialized successfully")

    def add_user(self, telegram_id: int, aspen_username: str, aspen_password: str,
                 notification_method: str = 'telegram') -> bool:
        """Add or update user credentials."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Encrypt credentials
            encrypted_username = self._encrypt(aspen_username)
            encrypted_password = self._encrypt(aspen_password)

            # Check if user exists
            cursor.execute('SELECT telegram_id FROM users WHERE telegram_id = ?', (telegram_id,))
            existing_user = cursor.fetchone()

            if existing_user:
                # Update existing user
                cursor.execute('''
                    UPDATE users
                    SET aspen_username = ?, aspen_password = ?, notification_method = ?, last_updated = ?
                    WHERE telegram_id = ?
                ''', (encrypted_username, encrypted_password, notification_method, datetime.utcnow(), telegram_id))
            else:
                # Insert new user
                cursor.execute('''
                    INSERT INTO users
                    (telegram_id, aspen_username, aspen_password, notification_method, created_at, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (telegram_id, encrypted_username, encrypted_password, notification_method, datetime.utcnow(), datetime.utcnow()))

            conn.commit()
            conn.close()
            logger.info(f"User {telegram_id} added/updated successfully")
            return True

        except Exception as e:
            logger.error(f"Error adding user {telegram_id}: {e}")
            return False

    def get_user(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        """Get user data by telegram ID."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('SELECT * FROM users WHERE telegram_id = ?', (telegram_id,))
            user = cursor.fetchone()
            conn.close()

            if user:
                return {
                    'telegram_id': user[0],
                    'aspen_username': self._decrypt(user[1]),
                    'aspen_password': self._decrypt(user[2]),
                    'notification_method': user[3],
                    'is_active': bool(user[5]),  # Fixed: was user[4], should be user[5]
                    'created_at': user[6],        # Fixed: was user[5], should be user[6]
                    'last_updated': user[7]       # Fixed: was user[6], should be user[7]
                }
            return None

        except Exception as e:
            logger.error(f"Error getting user {telegram_id}: {e}")
            return None

    def get_all_active_users(self) -> List[Dict[str, Any]]:
        """Get all active users."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('SELECT * FROM users WHERE is_active = 1')
            users = cursor.fetchall()
            conn.close()

            result = []
            for user in users:
                result.append({
                    'telegram_id': user[0],
                    'aspen_username': self._decrypt(user[1]),
                    'aspen_password': self._decrypt(user[2]),
                    'notification_method': user[3],
                    'is_active': bool(user[5]),   # user[4] is email, user[5] is is_active
                    'created_at': user[6],
                    'last_updated': user[7]
                })

            return result

        except Exception as e:
            logger.error(f"Error getting all users: {e}")
            return []

    def add_feedback(self, user_id: int, username: str, first_name: str, feedback_type: str, message: str) -> bool:
        """Add feedback to database."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('''
                INSERT INTO feedback (user_id, username, first_name, feedback_type, message)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, username, first_name, feedback_type, message))

            conn.commit()
            conn.close()
            logger.info(f"Feedback added from user {user_id}")
            return True

        except Exception as e:
            logger.error(f"Error adding feedback from user {user_id}: {e}")
            return False

    def get_feedback(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent feedback messages."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('''
                SELECT user_id, username, first_name, feedback_type, message, created_at
                FROM feedback
                ORDER BY created_at DESC
                LIMIT ?
            ''', (limit,))

            feedback_list = cursor.fetchall()
            conn.close()

            result = []
            for feedback in feedback_list:
                result.append({
                    'user_id': feedback[0],
                    'username': feedback[1],
                    'first_name': feedback[2],
                    'feedback_type': feedback[3],
                    'message': feedback[4],
                    'created_at': feedback[5]
                })

            return result

        except Exception as e:
            logger.error(f"Error getting feedback: {e}")
            return []

    def update_user_notification_method(self, telegram_id: int, method: str) -> bool:
        """Update user's notification method."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('''
                UPDATE users
                SET notification_method = ?, last_updated = ?
                WHERE telegram_id = ?
            ''', (method, datetime.utcnow(), telegram_id))

            conn.commit()
            conn.close()
            return cursor.rowcount > 0

        except Exception as e:
            logger.error(f"Error updating notification method for {telegram_id}: {e}")
            return False

    # Email functionality removed - Telegram only notifications

    def get_user_settings(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        """Get user settings."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('SELECT * FROM user_settings WHERE telegram_id = ?', (telegram_id,))
            settings = cursor.fetchone()
            conn.close()

            if settings:
                return {
                    'telegram_id': settings[0],
                    'timezone': settings[1],
                    'notification_frequency': settings[2],
                    'notification_time': settings[3]
                }
            return None

        except Exception as e:
            logger.error(f"Error getting user settings for {telegram_id}: {e}")
            return None

    def update_user_notification_time(self, telegram_id: int, notification_time: str) -> bool:
        """Update user's notification time."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Get current timezone or use default
            current_settings = self.get_user_settings(telegram_id)
            timezone = current_settings.get('timezone', 'America/Chicago') if current_settings else 'America/Chicago'

            # Insert or update user settings
            cursor.execute('''
                INSERT OR REPLACE INTO user_settings
                (telegram_id, timezone, notification_frequency, notification_time)
                VALUES (?, ?, ?, ?)
            ''', (telegram_id, timezone, 'daily', notification_time))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Error updating notification time for {telegram_id}: {e}")
            return False

    def update_user_timezone(self, telegram_id: int, timezone: str) -> bool:
        """Update user's timezone."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Get current settings or use defaults
            current_settings = self.get_user_settings(telegram_id)
            notification_time = current_settings.get('notification_time', '15:00') if current_settings else '15:00'

            # Insert or update user settings
            cursor.execute('''
                INSERT OR REPLACE INTO user_settings
                (telegram_id, timezone, notification_frequency, notification_time)
                VALUES (?, ?, ?, ?)
            ''', (telegram_id, timezone, 'daily', notification_time))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Error updating timezone for {telegram_id}: {e}")
            return False

    def deactivate_user(self, telegram_id: int) -> bool:
        """Deactivate user account."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('''
                UPDATE users
                SET is_active = 0, last_updated = ?
                WHERE telegram_id = ?
            ''', (datetime.utcnow(), telegram_id))

            conn.commit()
            conn.close()
            return cursor.rowcount > 0

        except Exception as e:
            logger.error(f"Error deactivating user {telegram_id}: {e}")
            return False

    def delete_user(self, telegram_id: int) -> bool:
        """Delete user account completely."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('DELETE FROM users WHERE telegram_id = ?', (telegram_id,))
            affected_users = cursor.rowcount
            cursor.execute('DELETE FROM user_settings WHERE telegram_id = ?', (telegram_id,))
            affected_settings = cursor.rowcount

            conn.commit()
            conn.close()
            return (affected_users + affected_settings) > 0

        except Exception as e:
            logger.error(f"Error deleting user {telegram_id}: {e}")
            return False

    def get_user_count(self) -> int:
        """Get total number of active users."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('SELECT COUNT(*) FROM users WHERE is_active = 1')
            count = cursor.fetchone()[0]
            conn.close()

            return count

        except Exception as e:
            logger.error(f"Error getting user count: {e}")
            return 0

    def backup_database(self) -> str:
        """Create backup of database."""
        try:
            from datetime import datetime
            import shutil

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_dir = os.path.dirname(self.db_path)
            backup_path = os.path.join(backup_dir, f"backup_users_{timestamp}.db")
            shutil.copy2(self.db_path, backup_path)

            logger.info(f"Database backed up to {backup_path}")
            return backup_path

        except Exception as e:
            logger.error(f"Error creating backup: {e}")
            return None
