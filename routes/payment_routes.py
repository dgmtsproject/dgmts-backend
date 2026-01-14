from flask import Blueprint, request, jsonify
from config import Config
import requests
import json
import time
import re

def luhn_check(card_number):
    """Validate credit card number using Luhn algorithm"""
    card_number = card_number.replace(' ', '').replace('-', '')
    if not card_number.isdigit():
        return False
    
    def digits_of(n):
        return [int(d) for d in str(n)]
    
    digits = digits_of(card_number)
    odd_digits = digits[-1::-2]
    even_digits = digits[-2::-2]
    checksum = sum(odd_digits)
    for d in even_digits:
        checksum += sum(digits_of(d*2))
    
    return checksum % 10 == 0

def sanitize_card_number(card_number):
    """Mask card number for logging (show only last 4 digits)"""
    if not card_number or len(card_number) < 4:
        return "****"
    return "****" + card_number[-4:]

# Create Blueprint
payment_bp = Blueprint('payment', __name__, url_prefix='/api')

@payment_bp.route('/process-payment', methods=['POST'])
def process_payment():
    """
    Process payment through Authorize.net API
    
    Expected JSON body:
    {
        "amount": "100.00",
        "cardNumber": "4111111111111111",
        "expirationDate": "12/25",
        "cardCode": "123",
        "firstName": "John",
        "lastName": "Doe",
        "address": "123 Main St",
        "city": "New York",
        "state": "NY",
        "zip": "10001",
        "country": "USA"
    }
    """
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['amount', 'cardNumber', 'expirationDate', 'cardCode', 'firstName', 'lastName']
        for field in required_fields:
            if not data.get(field):
                return jsonify({
                    "error": f"Missing required field: {field}",
                    "status": "error"
                }), 400
        
        # Validate amount
        try:
            amount = float(data['amount'])
            if amount <= 0:
                return jsonify({
                    "error": "Amount must be greater than 0",
                    "status": "error"
                }), 400
        except (ValueError, TypeError):
            return jsonify({
                "error": "Invalid amount format",
                "status": "error"
            }), 400
        
        # Validate card number using Luhn algorithm
        card_number = data['cardNumber'].replace(' ', '').replace('-', '')
        if not luhn_check(card_number):
            return jsonify({
                "error": "Invalid card number",
                "status": "error"
            }), 400
        
        # Validate card code (CVV)
        card_code = data.get('cardCode', '')
        if not card_code or len(card_code) < 3 or len(card_code) > 4 or not card_code.isdigit():
            return jsonify({
                "error": "Invalid card code (CVV)",
                "status": "error"
            }), 400
        
        # Check if API credentials are configured
        if not Config.AUTHORIZE_NET_API_LOGIN_ID or not Config.AUTHORIZE_NET_TRANSACTION_KEY:
            return jsonify({
                "error": "Payment processing is not configured. Please contact administrator.",
                "status": "error"
            }), 500
        
        # Determine API endpoint (sandbox or production)
        api_url = 'https://apitest.authorize.net/xml/v1/request.api' if Config.AUTHORIZE_NET_SANDBOX else 'https://api.authorize.net/xml/v1/request.api'
        
        # Parse expiration date (MM/YY format)
        exp_parts = data['expirationDate'].split('/')
        if len(exp_parts) != 2:
            return jsonify({
                "error": "Invalid expiration date format. Use MM/YY",
                "status": "error"
            }), 400
        
        month = exp_parts[0].zfill(2)
        year = '20' + exp_parts[1] if len(exp_parts[1]) == 2 else exp_parts[1]
        expiration_date = f"{year}-{month}"
        
        # Prepare request payload
        payload = {
            "createTransactionRequest": {
                "merchantAuthentication": {
                    "name": Config.AUTHORIZE_NET_API_LOGIN_ID,
                    "transactionKey": Config.AUTHORIZE_NET_TRANSACTION_KEY
                },
                "refId": f"ref_{int(time.time() * 1000)}",
                "transactionRequest": {
                    "transactionType": "authCaptureTransaction",
                    "amount": f"{amount:.2f}",
                    "payment": {
                        "creditCard": {
                            "cardNumber": data['cardNumber'].replace(' ', ''),
                            "expirationDate": expiration_date,
                            "cardCode": data['cardCode']
                        }
                    },
                    "billTo": {
                        "firstName": data['firstName'],
                        "lastName": data['lastName'],
                        "address": data.get('address', ''),
                        "city": data.get('city', ''),
                        "state": data.get('state', ''),
                        "zip": data.get('zip', ''),
                        "country": data.get('country', 'USA')
                    }
                }
            }
        }
        
        # Make request to Authorize.net
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        response = requests.post(api_url, json=payload, headers=headers, timeout=30)
        
        # Log response (sanitized for production - no sensitive card data)
        masked_card = sanitize_card_number(data['cardNumber'])
        print(f"Payment request for card ending in {masked_card[-4:]}: Status {response.status_code}")
        if not Config.AUTHORIZE_NET_SANDBOX:
            # In production, only log minimal info
            print(f"Production payment processed - Response code: {response.status_code}")
        else:
            # In sandbox, can log more details for debugging
            print(f"Sandbox payment - Response status: {response.status_code}")
            print(f"Response text length: {len(response.text)}")
            if response.status_code != 200:
                print(f"Response text (first 500 chars): {response.text[:500]}")
        
        if response.status_code != 200:
            return jsonify({
                "error": f"Payment gateway error: HTTP {response.status_code}",
                "status": "error",
                "details": response.text
            }), 500
        
        # Parse response - handle BOM and encoding issues
        try:
            # Remove BOM if present and strip whitespace
            response_text = response.text.lstrip('\ufeff').strip()
            result = json.loads(response_text)
        except json.JSONDecodeError as e:
            # Try to extract transaction info from partial response if possible
            print(f"JSON decode error: {e}")
            print(f"Response text (first 1000 chars): {response_text[:1000]}")
            
            # Check if we can extract success info from the text
            if '"responseCode":"1"' in response_text or '"responseCode":1' in response_text:
                # Try to extract transaction ID
                trans_id_match = re.search(r'"transId":"(\d+)"', response_text)
                auth_code_match = re.search(r'"authCode":"([^"]+)"', response_text)
                
                if trans_id_match:
                    return jsonify({
                        "status": "success",
                        "message": "Payment processed successfully (parsed from response)",
                        "transactionId": trans_id_match.group(1),
                        "authCode": auth_code_match.group(1) if auth_code_match else None,
                        "responseCode": "1",
                        "note": "Response parsing had issues but transaction was successful"
                    }), 200
            
            return jsonify({
                "error": "Invalid response from payment gateway",
                "status": "error",
                "details": response_text[:500],
                "json_error": str(e)
            }), 500
        
        # Check transaction response
        transaction_response = result.get('transactionResponse', {})
        response_code = transaction_response.get('responseCode')
        
        if response_code == '1':
            # Success
            return jsonify({
                "status": "success",
                "message": "Payment processed successfully",
                "transactionId": transaction_response.get('transId'),
                "authCode": transaction_response.get('authCode'),
                "responseCode": response_code,
                "accountNumber": transaction_response.get('accountNumber', 'XXXX'),
                "accountType": transaction_response.get('accountType', '')
            }), 200
        else:
            # Transaction declined or error
            errors = transaction_response.get('errors', [])
            error_messages = transaction_response.get('messages', [])
            
            error_text = "Transaction was declined"
            if errors:
                error_text = errors[0].get('errorText', error_text)
            elif error_messages:
                error_text = error_messages[0].get('description', error_text)
            
            return jsonify({
                "status": "error",
                "error": error_text,
                "responseCode": response_code,
                "errors": errors,
                "messages": error_messages
            }), 400
            
    except requests.exceptions.RequestException as e:
        return jsonify({
            "error": f"Network error: {str(e)}",
            "status": "error"
        }), 500
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"❌ Payment processing error: {error_trace}")
        return jsonify({
            "error": f"Payment processing failed: {str(e)}",
            "status": "error"
        }), 500
