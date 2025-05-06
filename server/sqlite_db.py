import os
import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional

DB_DIR = Path(__file__).parent.parent / "data"
DB_DIR.mkdir(exist_ok=True)

DB_PATH = DB_DIR / "voice_agent.db"

class SQLiteVoiceAgentDB:
    
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self._init_db()
    
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_db(self):
        """Initialize the database tables if they don't exist."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            id TEXT PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            phone_number TEXT UNIQUE NOT NULL,
            email TEXT NOT NULL,
            city TEXT NOT NULL,
            job_business TEXT NOT NULL,
            investor_type TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        ''')
        
        # Create calls table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS calls (
            id TEXT PRIMARY KEY,
            client_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            transcript TEXT,
            summary TEXT,
            FOREIGN KEY (client_id) REFERENCES clients (id)
        )
        ''')
        
        # Check if summary column exists in calls table, add it if not
        cursor.execute("PRAGMA table_info(calls)")
        columns = [column[1] for column in cursor.fetchall()]
        if "summary" not in columns:
            cursor.execute("ALTER TABLE calls ADD COLUMN summary TEXT")
            conn.commit()
            
        # Check if investor_type column exists in clients table, add it if not
        cursor.execute("PRAGMA table_info(clients)")
        columns = [column[1] for column in cursor.fetchall()]
        if "investor_type" not in columns:
            cursor.execute("ALTER TABLE clients ADD COLUMN investor_type TEXT DEFAULT 'individual'")
            conn.commit()
            print("Added investor_type column to clients table")
        
        conn.commit()
        conn.close()
    
    def add_customer(self, first_name: str, last_name: str, phone_number: str, 
                     email: str, city: str, job_business: str,
                     investor_type: str = "individual") -> str:
        """
        Add a new customer to the database.
        
        Args:
            first_name: First name of the customer
            last_name: Last name of the customer
            phone_number: Phone number of the customer
            email: Email of the customer
            city: City of the customer
            job_business: Job or business of the customer
            investor_type: Type of investor ('individual' or 'managed')
            
        Returns:
            The ID of the created customer
        """
        client_id = str(uuid.uuid4())
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                '''
                INSERT INTO clients (id, first_name, last_name, phone_number, email, city, job_business, investor_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    client_id, first_name, last_name, phone_number, 
                    email, city, job_business, investor_type, datetime.now().isoformat()
                )
            )
            conn.commit()
            return client_id
        except sqlite3.IntegrityError:
            # If the phone number already exists, return the existing client ID
            cursor.execute("SELECT id FROM clients WHERE phone_number = ?", (phone_number,))
            existing_id = cursor.fetchone()
            return existing_id["id"] if existing_id else None
        finally:
            conn.close()
    
    def add_customer_with_id(self, client_id: str, first_name: str, last_name: str, phone_number: str, 
                           email: str, city: str, job_business: str,
                           investor_type: str = "individual") -> str:
        """
        Add a new customer with a specific ID to the database.
        
        Args:
            client_id: Explicit ID to use for the customer
            first_name: First name of the customer
            last_name: Last name of the customer
            phone_number: Phone number of the customer
            email: Email of the customer
            city: City of the customer
            job_business: Job or business of the customer
            investor_type: Type of investor ('individual' or 'managed')
            
        Returns:
            The ID of the created customer (same as the input client_id)
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            # First check if the client with this phone number already exists
            cursor.execute("SELECT id FROM clients WHERE phone_number = ?", (phone_number,))
            existing = cursor.fetchone()
            if existing:
                return existing["id"]
            
            # Also check if client with this ID already exists
            cursor.execute("SELECT id FROM clients WHERE id = ?", (client_id,))
            id_exists = cursor.fetchone()
            if id_exists:
                return client_id  # Client with this ID already exists
                
            # Insert the new client with the specified ID
            cursor.execute(
                '''
                INSERT INTO clients (id, first_name, last_name, phone_number, email, city, job_business, investor_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    client_id, first_name, last_name, phone_number, 
                    email, city, job_business, investor_type, datetime.now().isoformat()
                )
            )
            conn.commit()
            return client_id
        except Exception as e:
            print(f"Error in add_customer_with_id: {e}")
            return None
        finally:
            conn.close()
    
    def get_customer_by_phone(self, phone_number: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """
        Get a customer by phone number.
        
        Args:
            phone_number: The phone number to search for
            
        Returns:
            A tuple containing the customer ID and customer data (or None, None if not found)
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM clients WHERE phone_number = ?", (phone_number,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            client_data = dict(row)
            return client_data["id"], client_data
        
        return None, None
    
    def get_customer_by_id(self, client_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a customer by ID.
        
        Args:
            client_id: The customer ID to search for
            
        Returns:
            Customer data or None if not found
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
        row = cursor.fetchone()
        conn.close()
        
        return dict(row) if row else None
    
    def create_call(self, client_id: str) -> str:
        """
        Create a new call record.
        
        Args:
            client_id: ID of the client making the call
            
        Returns:
            The ID of the created call
        """
        call_id = str(uuid.uuid4())
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT INTO calls (id, client_id, timestamp, transcript) VALUES (?, ?, ?, ?)",
            (call_id, client_id, datetime.now().isoformat(), None)
        )
        conn.commit()
        conn.close()
        
        return call_id
    
    def create_call_with_id(self, client_id: str, call_id: str) -> str:
        """
        Create a new call record with a specific ID.
        
        Args:
            client_id: ID of the client making the call
            call_id: Explicit ID to use for the call
            
        Returns:
            The ID of the created call (same as input call_id)
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            # Check if call with this ID already exists
            cursor.execute("SELECT id FROM calls WHERE id = ?", (call_id,))
            existing = cursor.fetchone()
            if existing:
                return call_id  # Call already exists with this ID
                
            cursor.execute(
                "INSERT INTO calls (id, client_id, timestamp, transcript, summary) VALUES (?, ?, ?, ?, ?)",
                (call_id, client_id, datetime.now().isoformat(), None, None)
            )
            conn.commit()
            return call_id
        except Exception as e:
            print(f"Error in create_call_with_id: {e}")
            return None
        finally:
            conn.close()
    
    def update_call_transcript(self, call_id: str, transcript: str) -> bool:
        """
        Update the transcript for a call.
        
        Args:
            call_id: ID of the call
            transcript: Call transcript
            
        Returns:
            True if successful, False otherwise
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "UPDATE calls SET transcript = ? WHERE id = ?",
            (transcript, call_id)
        )
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        
        return success
    
    def get_latest_call(self, client_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the latest call for a client.
        
        Args:
            client_id: ID of the client
            
        Returns:
            The latest call data or None if not found
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT * FROM calls WHERE client_id = ? ORDER BY timestamp DESC LIMIT 1",
            (client_id,)
        )
        row = cursor.fetchone()
        conn.close()
        
        return dict(row) if row else None
    
    def get_call_history(self, client_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Get call history for a client.
        
        Args:
            client_id: ID of the client
            limit: Maximum number of calls to return
            
        Returns:
            List of call data
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT * FROM calls WHERE client_id = ? ORDER BY timestamp DESC LIMIT ?",
            (client_id, limit)
        )
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def update_call_summary(self, call_id: str, summary: str) -> bool:
        """
        Update the summary for a call.
        
        Args:
            call_id: ID of the call
            summary: Call summary
            
        Returns:
            True if successful, False otherwise
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "UPDATE calls SET summary = ? WHERE id = ?",
            (summary, call_id)
        )
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        
        return success 