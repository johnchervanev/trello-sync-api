#!/usr/bin/env python3
"""
Trello Sync Script - JSON API Version for Make.com
Returns JSON data instead of saving to CSV
"""

import requests
import json
import os
from datetime import datetime, timedelta
from flask import Flask, jsonify

app = Flask(__name__)

# Configuration
TRELLO_API_KEY = os.getenv('TRELLO_API_KEY', 'your_api_key_here')
TRELLO_TOKEN = os.getenv('TRELLO_TOKEN', 'your_token_here')

def convert_to_canada_central_time(utc_datetime_str):
    """Convert UTC datetime to Canada Central Time (CST/CDT)"""
    try:
        parsed_date = datetime.fromisoformat(utc_datetime_str.replace('Z', '+00:00'))
        year = parsed_date.year
        
        # DST calculation
        dst_start = datetime(year, 3, 8) + timedelta(days=(6 - datetime(year, 3, 8).weekday()) % 7 + 7)
        dst_end = datetime(year, 11, 1) + timedelta(days=(6 - datetime(year, 11, 1).weekday()) % 7)
        
        if dst_start <= parsed_date.replace(tzinfo=None) < dst_end:
            offset = timedelta(hours=-5)
            timezone_label = "CDT"
        else:
            offset = timedelta(hours=-6)
            timezone_label = "CST"
        
        central_time = parsed_date + offset
        formatted_time = central_time.strftime('%I:%M %p').lstrip('0')
        return f"{central_time.strftime('%Y-%m-%d')} {formatted_time} {timezone_label}"
        
    except Exception as e:
        return utc_datetime_str[:16] if len(utc_datetime_str) >= 16 else utc_datetime_str

class TrelloAPI:
    def __init__(self):
        self.api_key = TRELLO_API_KEY
        self.token = TRELLO_TOKEN
        self.base_url = 'https://api.trello.com/1'
        self.member_cache = {}
        self.list_cache = {}
    
    def _make_request(self, endpoint):
        """Make authenticated request to Trello API"""
        url = f"{self.base_url}{endpoint}"
        params = {'key': self.api_key, 'token': self.token}
        
        response = requests.get(url, params=params)
        
        if response.status_code != 200:
            return None
        return response.json()
    
    def get_member_name(self, member_id):
        """Get member name by ID with caching"""
        if member_id in self.member_cache:
            return self.member_cache[member_id]
        
        member_data = self._make_request(f'/members/{member_id}')
        if member_data:
            name = (member_data.get('fullName') or 
                   member_data.get('username') or 
                   member_data.get('displayName') or 
                   'Unknown')
            self.member_cache[member_id] = name
            return name
        return 'Unknown'
    
    def get_list_name(self, list_id):
        """Get list name by ID with caching"""
        if list_id in self.list_cache:
            return self.list_cache[list_id]
        
        list_data = self._make_request(f'/lists/{list_id}')
        if list_data:
            name = list_data.get('name', 'Unknown List')
            self.list_cache[list_id] = name
            return name
        return 'Unknown List'
    
    def get_all_boards(self):
        """Get All Boards from Trello"""
        return self._make_request('/members/me/boards') or []
    
    def get_board_members(self, board_id):
        """Get board members lookup"""
        members = self._make_request(f'/boards/{board_id}/members') or []
        member_lookup = {}
        
        for member in members:
            member_id = member.get('id')
            member_name = (member.get('fullName') or 
                          member.get('username') or 
                          member.get('displayName') or 
                          'Unknown')
            member_lookup[member_id] = member_name
            self.member_cache[member_id] = member_name
        
        return member_lookup
    
    def get_cards_on_board(self, board_id):
        """Get Cards on Board with members and list info"""
        endpoint = f'/boards/{board_id}/cards?members=true&list=true'
        return self._make_request(endpoint) or []
    
    def get_card_members(self, card):
        """Get members assigned to a specific card"""
        members = card.get('members', [])
        member_names = []
        for member in members:
            name = member.get('fullName') or member.get('username', 'Unknown')
            member_names.append(name)
        return ', '.join(member_names) if member_names else ''
    
    def get_card_activity(self, card_id):
        """Get all card activity/comments"""
        actions = self._make_request(f'/cards/{card_id}/actions?filter=commentCard') or []
        
        comments = []
        for action in actions:
            if action.get('type') == 'commentCard':
                comment_text = action.get('data', {}).get('text', '')
                member_name = action.get('memberCreator', {}).get('fullName', 'Unknown')
                date = action.get('date', '')[:10]
                comments.append(f"{member_name} ({date}): {comment_text}")
        
        return '\n'.join(comments) if comments else ''
    
    def get_checklist_items_detailed(self, card_id, member_lookup):
        """Get detailed checklist items with assignees and due dates"""
        checklists = self._make_request(f'/cards/{card_id}/checklists') or []
        
        all_checklist_items = []
        for checklist in checklists:
            for item in checklist.get('checkItems', []):
                item_name = item['name']
                status = 'Complete' if item['state'] == 'complete' else 'Pending'
                
                # Get assignee
                assignee_ids = []
                if 'idMembers' in item and item['idMembers']:
                    assignee_ids = item['idMembers']
                elif 'idMember' in item and item['idMember']:
                    assignee_ids = [item['idMember']]
                
                assigned_to = ''
                if assignee_ids:
                    assignee_names = []
                    for assignee_id in assignee_ids:
                        if assignee_id in member_lookup:
                            assignee_names.append(member_lookup[assignee_id])
                    assigned_to = ', '.join(assignee_names)
                
                # Get due date
                due_date = item.get('due', '')
                if due_date:
                    due_date = convert_to_canada_central_time(due_date)
                
                all_checklist_items.append({
                    'name': item_name,
                    'status': status,
                    'assigned_to': assigned_to,
                    'due_date': due_date
                })
        
        return all_checklist_items

# In-memory tracking (replace with database for production)
processed_cards = set()

@app.route('/sync', methods=['GET', 'POST'])
def sync_trello():
    """Main API endpoint that returns JSON data"""
    
    # Check API credentials
    if TRELLO_API_KEY == 'your_api_key_here' or TRELLO_TOKEN == 'your_token_here':
        return jsonify({"error": "API credentials not set"}), 400
    
    try:
        trello_api = TrelloAPI()
        all_tasks = []
        
        # Get all boards
        boards = trello_api.get_all_boards()
        
        if not boards:
            return jsonify({"error": "No boards found"}), 404
        
        # Process each board
        for board in boards:
            board_id = board['id']
            board_name = board['name']
            
            # Get board members
            member_lookup = trello_api.get_board_members(board_id)
            
            # Get all cards
            all_cards = trello_api.get_cards_on_board(board_id)
            
            # Filter out archived cards
            active_cards = [card for card in all_cards if not card.get('closed', False)]
            
            # Process each card
            for card in active_cards:
                card_id = card['id']
                card_name = card['name']
                card_url = card['shortUrl']
                
                # Skip if already processed (simple deduplication)
                if card_id in processed_cards:
                    continue
                
                # Get card details
                card_members = trello_api.get_card_members(card)
                
                # Get list name
                list_name = 'Unknown List'
                if 'list' in card and card['list']:
                    list_name = card['list'].get('name', 'Unknown List')
                elif card.get('idList'):
                    list_name = trello_api.get_list_name(card['idList'])
                
                # Get card activity
                card_activity = trello_api.get_card_activity(card_id)
                
                # Get checklist items
                checklist_items = trello_api.get_checklist_items_detailed(card_id, member_lookup)
                
                if checklist_items:
                    # Add each checklist item as a separate record
                    for item in checklist_items:
                        task_record = {
                            "Client_Name": board_name,
                            "Project_Name": card_name,
                            "Status": list_name,
                            "Assigned_To": card_members,
                            "Step": item['name'],
                            "Step_Assigned_To": item['assigned_to'],
                            "Step_Status": item['status'],
                            "Due_Date": item['due_date'],
                            "Trello_Card_Link": card_url,
                            "Comment": card_activity,
                            "Notes": ""
                        }
                        all_tasks.append(task_record)
                
                # Mark card as processed
                processed_cards.add(card_id)
        
        # Return JSON response
        return jsonify({
            "status": "success",
            "timestamp": datetime.now().isoformat(),
            "total_tasks": len(all_tasks),
            "tasks": all_tasks
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})

@app.route('/', methods=['GET'])
def home():
    """Home endpoint"""
    return jsonify({
        "service": "Trello-SharePoint Sync API",
        "endpoints": {
            "/sync": "Main sync endpoint",
            "/health": "Health check"
        }
    })

if __name__ == '__main__':
    # For local testing
    app.run(debug=True, host='0.0.0.0', port=5000)

# For cloud deployment (Render, Railway, etc.)
# They will automatically detect and run this Flask app
