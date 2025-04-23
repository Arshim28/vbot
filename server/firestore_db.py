import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
import datetime
import uuid
import json

class VoiceAgentDB:
    def __init__(self, service_account_path='serviceAccountKey.json'):
        try:
            firebase_admin.get_app()
        except ValueError:
            cred = credentials.Certificate(service_account_path)
            firebase_admin.initialize_app(cred)
        
        self.db = firestore.client()
    
    def add_customer(self, first_name, last_name, phone_number, room_id=None, room_url=None,        job_business=None, city=None, email=None):
        customer_data = {
            'firstName': first_name,
            'lastName': last_name,
            'phoneNumber': phone_number,
            'jobBusiness': job_business,
            'city': city,
            'email': email,
            'dateCreated': firestore.SERVER_TIMESTAMP,
            'lastUpdated': firestore.SERVER_TIMESTAMP,
            'lastContacted': None,
            'status': 'active',
            'RoomId': room_id,
            'RoomURL': room_url
        }
        
        customer_ref = self.db.collection('customers').document()
        customer_ref.set(customer_data)
        
        self._initialize_customer_profile(customer_ref.id)
        
        return customer_ref.id
    
    def get_customer_by_phone(self, phone_number):
        query = self.db.collection('customers').where('phoneNumber', '==', phone_number).limit(1)
        results = query.get()
        
        for doc in results:
            return doc.id, doc.to_dict()
        
        return None, None
    
    def get_customer(self, customer_id):
        doc_ref = self.db.collection('customers').document(customer_id)
        doc = doc_ref.get()
        
        if doc.exists:
            return doc.to_dict()
        
        return None
    
    def update_customer(self, customer_id, update_data):
        if not update_data:
            return False
        
        update_data['lastUpdated'] = firestore.SERVER_TIMESTAMP
        
        doc_ref = self.db.collection('customers').document(customer_id)
        doc_ref.update(update_data)
        
        return True
    
    def create_call(self, customer_id, agent_id=None, call_type="outbound", call_id=None):
        if not call_id:
            call_id = str(uuid.uuid4())
            
        call_data = {
            'callId': call_id,
            'customerId': customer_id,
            'agentId': agent_id,
            'callType': call_type,
            'startTime': firestore.SERVER_TIMESTAMP,
            'endTime': None,
            'duration': None,
            'status': 'active',
            'transcript': [],
            'summary': None,
            'tags': []
        }
        
        call_ref = self.db.collection('calls').document(call_id)
        call_ref.set(call_data)
        
        self.update_customer(customer_id, {'lastContacted': firestore.SERVER_TIMESTAMP})
        
        return call_id
    
    def add_message_to_call(self, call_id, message, speaker, timestamp=None):
        if not timestamp:
            timestamp = datetime.datetime.now()
            
        message_data = {
            'speaker': speaker,  # 'agent' or 'customer'
            'timestamp': timestamp,
            'content': message
        }
        
        call_ref = self.db.collection('calls').document(call_id)
        
        try:
            call_ref.update({
                'transcript': firestore.ArrayUnion([message_data])
            })
            return True
        except Exception as e:
            print(f"Error adding message: {e}")
            return False
    
    def end_call(self, call_id, summary=None, tags=None, call_metrics=None):
        call_ref = self.db.collection('calls').document(call_id)
        call_doc = call_ref.get()
        
        if not call_doc.exists:
            return False
            
        call_data = call_doc.to_dict()
        
        duration = None
        if 'startTime' in call_data and call_data['startTime']:
            try:
                if hasattr(call_data['startTime'], 'seconds'):
                    start_seconds = call_data['startTime'].seconds + (call_data['startTime'].nanos / 1e9)
                    current_seconds = datetime.datetime.now().timestamp()
                    duration = current_seconds - start_seconds
                else:
                    pass
            except Exception as e:
                print(f"Error calculating duration: {e}")
        
        update_data = {
            'endTime': firestore.SERVER_TIMESTAMP,
            'status': 'completed'
        }
        
        if duration is not None:
            update_data['duration'] = duration
            
        if summary:
            update_data['summary'] = summary
            
        if tags and isinstance(tags, list):
            update_data['tags'] = tags
            
        if call_metrics and isinstance(call_metrics, dict):
            update_data['metrics'] = call_metrics
            
        call_ref.update(update_data)
        
        return True
    
    def add_call_transcript(self, call_id, full_transcript):
        call_ref = self.db.collection('calls').document(call_id)
        call = call_ref.get()
        if not call.exists:
            return False

        if isinstance(full_transcript, str):
            full_transcript = [{
                'speaker': 'system',
                'timestamp': datetime.datetime.now(),
                'content': full_transcript
            }]
            
        call_ref.update({
            'transcript': full_transcript
        })
        
        return True
    
    def update_client_profile(self, customer_id, profile_data):
        if not profile_data:
            return False
            
        profile_ref = self.db.collection('clientProfiles').document(customer_id)
        profile_doc = profile_ref.get()
        if not profile_doc.exists:
            self._initialize_customer_profile(customer_id)
        
        updates = {
            'lastUpdated': firestore.SERVER_TIMESTAMP
        }
        
        valid_keys = [
            'clientType', 'understandsCreditFunds', 'hasMinimumInvestment', 
            'knowsManeesh', 'investorSophistication', 'attitudeTowardsOffering',
            'wantsZoomCall', 'shouldCallAgain', 'interestedInSalesContact', 
            'languagePreference', 'notes'
        ]
        
        for key in valid_keys:
            if key in profile_data and profile_data[key] is not None:
                updates[key] = profile_data[key]
        
        profile_ref.update(updates)
        return True
    
    def get_call_history(self, customer_id, limit=10):
        query = (self.db.collection('calls')
                .where(filter=firestore.FieldFilter("customerId", "==", customer_id))
                .order_by('startTime', direction=firestore.Query.DESCENDING)
                .limit(limit))
        
        try:
            results = query.get()
            calls = []
            
            for doc in results:
                call_data = doc.to_dict()
                calls.append(call_data)
                
            return calls
        except Exception as e:
            print(f"Error retrieving call history: {e}")
            print("If this is an index error, please create the required index using the link in the error message.")
            return []
    
    def get_call_transcript(self, call_id):
        call_ref = self.db.collection('calls').document(call_id)
        call = call_ref.get()
        
        if not call.exists:
            return []
            
        call_data = call.to_dict()
        return call_data.get('transcript', [])
    
    def get_customer_profile(self, customer_id):
        profile_ref = self.db.collection('clientProfiles').document(customer_id)
        profile = profile_ref.get()
        
        if profile.exists:
            return profile.to_dict()
        
        return None
    
    def search_customers(self, query_field, query_value, limit=10):
        query = (self.db.collection('customers')
                .where(query_field, '==', query_value)
                .limit(limit))
        
        results = query.get()
        customers = []
        
        for doc in results:
            customers.append((doc.id, doc.to_dict()))
            
        return customers
    
    def add_call_note(self, call_id, note):
        note_data = {
            'timestamp': datetime.datetime.now(),
            'content': note
        }
        
        call_ref = self.db.collection('calls').document(call_id)
        
        try:
            call_ref.update({
                'notes': firestore.ArrayUnion([note_data])
            })
            return True
        except Exception as e:
            print(f"Error adding note: {e}")
            return False
    
    def tag_call(self, call_id, tags):
        if not isinstance(tags, list):
            tags = [tags]
            
        call_ref = self.db.collection('calls').document(call_id)
        
        try:
            call_ref.update({
                'tags': firestore.ArrayUnion(tags)
            })
            return True
        except Exception as e:
            print(f"Error adding tags: {e}")
            return False
    
    def _initialize_customer_profile(self, customer_id):
        default_profile = {
            'customerId': customer_id,
            'dateGenerated': firestore.SERVER_TIMESTAMP,
            'lastUpdated': firestore.SERVER_TIMESTAMP,
            'clientType': None,                    # 'distributor' or 'investor'
            'understandsCreditFunds': None,        # True or False
            'hasMinimumInvestment': None,          # True or False (1 cr)
            'knowsManeesh': None,                  # True or False
            'investorSophistication': None,        # 'sophisticated' or 'novice'
            'attitudeTowardsOffering': None,       # 'optimistic' or 'skeptic'
            'wantsZoomCall': None,                 # True or False
            'shouldCallAgain': None,               # True or False
            'interestedInSalesContact': None,      # True or False
            'languagePreference': 'English',       # Default 'English' or other language
            'notes': ''
        }
        
        self.db.collection('clientProfiles').document(customer_id).set(default_profile)