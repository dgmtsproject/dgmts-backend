from flask import Blueprint, request, jsonify
from config import Config
import requests
import json
import time

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
            if amount < 100 or amount > 300:
                return jsonify({
                    "error": "Amount must be between $100 and $300",
                    "status": "error"
                }), 400
        except (ValueError, TypeError):
            return jsonify({
                "error": "Invalid amount format",
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
            'Content-Type': 'application/json'
        }
        
        response = requests.post(api_url, json=payload, headers=headers, timeout=30)
        
        if response.status_code != 200:
            return jsonify({
                "error": f"Payment gateway error: HTTP {response.status_code}",
                "status": "error",
                "details": response.text
            }), 500
        
        # Parse response
        try:
            result = response.json()
        except json.JSONDecodeError:
            return jsonify({
                "error": "Invalid response from payment gateway",
                "status": "error",
                "details": response.text[:500]
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

