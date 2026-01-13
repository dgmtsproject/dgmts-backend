from flask import Blueprint, request, jsonify
from services.email_service import send_email
from services.alert_service import check_and_send_tiltmeter_alerts, check_and_send_seismograph_alert, check_and_send_smg3_seismograph_alert
from services.connection_monitor_service import check_and_send_connection_lost_alerts
from supabase import create_client, Client
from config import Config
from datetime import datetime, timezone
import pytz
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import requests

# Initialize Supabase client
supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

# Create Blueprint
email_bp = Blueprint('email', __name__, url_prefix='/api')

@email_bp.route('/dgmts-static/send-mail', methods=['POST', 'OPTIONS'])
def dgmts_static_send_mail():
    """
    Universal email sending endpoint - replicates Deno serverless function
    
    Supports multiple email types:
    - test: Test email configuration
    - payment: Payment confirmation emails
    - newsletter: Newsletter welcome email
    - subscriber_notification: Admin-sent newsletters/updates
    - contact: Contact form submissions (default)
    
    Request body (JSON):
    {
        "type": "test|payment|newsletter|subscriber_notification|contact",
        "name": "Sender Name",
        "email": "recipient@example.com",
        "message": "Email message content",
        "subject": "Custom subject (optional)",
        "htmlContent": "Custom HTML content (optional)",
        "attachments": [{name, url/data, type, size}] (optional),
        "embeddedImages": [{cid, name, data, type}] (optional for CID images),
        "token": "Subscriber token (optional)",
        "includeHeaderFooter": true/false (optional),
        "paymentData": {...} (for payment type)
    }
    """
    # Handle OPTIONS for CORS
    if request.method == 'OPTIONS':
        return '', 200, {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'content-type, authorization, x-client-info, apikey',
            'Access-Control-Allow-Methods': 'POST, OPTIONS'
        }
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No request body provided'}), 400
        
        # Extract parameters
        email_type = data.get('type', 'contact')
        name = data.get('name')
        email = data.get('email')
        message = data.get('message')
        subject = data.get('subject')
        html_content = data.get('htmlContent')
        attachments = data.get('attachments')  # New: array of attachments
        embedded_images = data.get('embeddedImages')  # New: CID embedded images
        token = data.get('token')
        include_header_footer = data.get('includeHeaderFooter', False)  # New: header/footer toggle
        payment_data = data.get('paymentData')
        
        print(f'Email request received: type={email_type}, email={email}')
        
        # Get email configurations from DGMTS Static Supabase database
        try:
            # Debug: Print Supabase connection info
            print(f'Connecting to DGMTS Static Supabase...')
            print(f'DGMTS Static Supabase URL: {Config.DGMTS_STATIC_SUPABASE_URL}')
            print(f'DGMTS Static Supabase Key exists: {bool(Config.DGMTS_STATIC_SUPABASE_KEY)}')
            
            # Create Supabase client for DGMTS Static database (where email_config table exists)
            from supabase import create_client
            dgmts_supabase = create_client(Config.DGMTS_STATIC_SUPABASE_URL, Config.DGMTS_STATIC_SUPABASE_KEY)
            
            print('Querying email_config table from DGMTS Static Supabase...')
            email_configs_resp = dgmts_supabase.table('email_config').select('*').order('type').execute()
            
            print(f'Query response data: {email_configs_resp.data}')
            
            if not email_configs_resp.data:
                return jsonify({'error': 'Email configuration not found. Please configure email settings in the admin panel.'}), 500
            
            # Separate primary and secondary configs
            primary_config = next((c for c in email_configs_resp.data if c.get('type') == 'primary'), email_configs_resp.data[0])
            secondary_config = next((c for c in email_configs_resp.data if c.get('type') == 'secondary'), None)
            
            print(f'Primary config found: {primary_config.get("email_id") if primary_config else "None"}')
            print(f'Secondary config found: {secondary_config.get("email_id") if secondary_config else "None"}')
            
            if not primary_config or not primary_config.get('email_id') or not primary_config.get('email_password'):
                return jsonify({'error': 'Primary email configuration is incomplete'}), 500
                
        except Exception as db_error:
            import traceback
            print(f'Database error: {db_error}')
            traceback.print_exc()
            return jsonify({'error': f'Failed to fetch email configuration from database: {str(db_error)}'}), 500
        
        # Helper function to get SMTP settings
        def get_smtp_settings(email_id):
            email_lower = email_id.lower()
            if '@gmail.com' in email_lower:
                return {'host': 'smtp.gmail.com', 'port': 587}
            elif any(domain in email_lower for domain in ['@outlook.com', '@hotmail.com', '@live.com', 'dullesgeotechnical.com']):
                return {'host': 'smtp.office365.com', 'port': 587}
            else:
                return {'host': 'smtp.office365.com', 'port': 587}
        
        # Get primary SMTP settings
        primary_smtp = get_smtp_settings(primary_config['email_id'])
        from_email_name = primary_config.get('from_email_name', 'DGMTS').strip()
        
        # BCC and admin emails
        bcc_emails = ["iaziz@dullesgeotechnical.com", "info@dullesgeotechnical.com", "qhaider@dullesgeotechnical.com"]
        payment_cc_emails = ["dgmts.project@gmail.com"]
        
        # Function to send email with fallback
        def send_email_with_fallback(mail_options, config_to_use=None):
            config = config_to_use or primary_config
            smtp_settings = get_smtp_settings(config['email_id'])
            
            try:
                print(f"Attempting to send email using {config.get('type', 'primary')} config ({config['email_id']})...")
                
                # Check if we have embedded images - use 'related' for CID images
                has_embedded_images = 'embedded_images' in mail_options and mail_options['embedded_images']
                
                if has_embedded_images:
                    # Create multipart/related message for embedded images
                    msg = MIMEMultipart('related')
                    msg_alternative = MIMEMultipart('alternative')
                    msg.attach(msg_alternative)
                else:
                    msg = MIMEMultipart('alternative')
                    msg_alternative = msg
                
                msg['From'] = mail_options['from']
                msg['To'] = mail_options['to'] if isinstance(mail_options['to'], str) else ', '.join(mail_options['to'])
                msg['Subject'] = mail_options['subject']
                
                # Add BCC if present
                if 'bcc' in mail_options:
                    msg['Bcc'] = ', '.join(mail_options['bcc']) if isinstance(mail_options['bcc'], list) else mail_options['bcc']
                
                # Add Reply-To if present
                if 'reply_to' in mail_options:
                    msg['Reply-To'] = mail_options['reply_to']
                
                # Add text and HTML parts
                if 'text' in mail_options:
                    msg_alternative.attach(MIMEText(mail_options['text'], 'plain'))
                if 'html' in mail_options:
                    msg_alternative.attach(MIMEText(mail_options['html'], 'html'))
                
                # Add embedded images with CID
                if has_embedded_images:
                    import base64
                    for img in mail_options['embedded_images']:
                        try:
                            # Get base64 data (remove data URL prefix if present)
                            img_data = img.get('data', '')
                            if ',' in img_data:
                                img_data = img_data.split(',')[1]
                            
                            # Determine MIME type
                            img_type = img.get('type', 'image/png')
                            maintype, subtype = img_type.split('/')
                            
                            img_part = MIMEBase(maintype, subtype)
                            img_part.set_payload(base64.b64decode(img_data))
                            encoders.encode_base64(img_part)
                            img_part.add_header('Content-ID', f'<{img.get("cid")}>')
                            img_part.add_header('Content-Disposition', 'inline', filename=img.get('name', 'image.png'))
                            msg.attach(img_part)
                        except Exception as img_err:
                            print(f"Error adding embedded image: {img_err}")
                
                # Add attachments (supports multiple file types)
                if 'attachments' in mail_options and mail_options['attachments']:
                    import base64
                    for attachment in mail_options['attachments']:
                        try:
                            attachment_data = None
                            
                            # Get attachment data from URL or base64
                            if attachment.get('url'):
                                try:
                                    response = requests.get(attachment['url'], timeout=10)
                                    if response.status_code == 200:
                                        attachment_data = response.content
                                except Exception as url_err:
                                    print(f"Error fetching attachment URL: {url_err}")
                            elif attachment.get('data'):
                                # Base64 data
                                data_str = attachment['data']
                                if ',' in data_str:
                                    data_str = data_str.split(',')[1]
                                attachment_data = base64.b64decode(data_str)
                            
                            if attachment_data:
                                # Determine MIME type
                                mime_type = attachment.get('type', 'application/octet-stream')
                                if '/' in mime_type:
                                    maintype, subtype = mime_type.split('/', 1)
                                else:
                                    maintype, subtype = 'application', 'octet-stream'
                                
                                part = MIMEBase(maintype, subtype)
                                part.set_payload(attachment_data)
                                encoders.encode_base64(part)
                                part.add_header('Content-Disposition', 'attachment', filename=attachment.get('name', 'attachment'))
                                msg.attach(part)
                        except Exception as att_err:
                            print(f"Error adding attachment: {att_err}")
                
                # Connect and send
                server = smtplib.SMTP(smtp_settings['host'], smtp_settings['port'])
                server.starttls()
                server.login(config['email_id'].strip(), config['email_password'].strip())
                
                # Get all recipients
                recipients = []
                if isinstance(mail_options['to'], list):
                    recipients.extend(mail_options['to'])
                else:
                    recipients.append(mail_options['to'])
                if 'bcc' in mail_options:
                    if isinstance(mail_options['bcc'], list):
                        recipients.extend(mail_options['bcc'])
                    else:
                        recipients.append(mail_options['bcc'])
                
                server.sendmail(config['email_id'], recipients, msg.as_string())
                server.quit()
                
                print(f"Email sent successfully using {config.get('type', 'primary')} config")
                return {'success': True, 'used_config': config.get('type', 'primary')}
                
            except smtplib.SMTPAuthenticationError as e:
                print(f"SMTP Authentication failed: {e}")
                
                # Try secondary config if available
                if secondary_config and config == primary_config:
                    print(f"Attempting secondary config ({secondary_config['email_id']})...")
                    return send_email_with_fallback(mail_options, secondary_config)
                else:
                    raise Exception(f"Email authentication failed: {str(e)}")
                    
            except Exception as e:
                print(f"Email send failed: {e}")
                raise
        
        # Build mail options based on type
        mail_options = {}
        
        if email_type == 'test':
            # Test email
            if not email:
                return jsonify({'error': 'Missing required field: email'}), 400
            
            mail_options = {
                'from': f"{from_email_name} <{primary_config['email_id']}>",
                'to': email,
                'subject': 'Test Email from DGMTS Email Configuration',
                'text': 'TEST EMAIL FROM DGMTS\n\nThis is a test email. If you received this, your email configuration is working correctly!',
                'html': '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; }
        .header { background: linear-gradient(135deg, #4a90e2 0%, #357abd 100%); color: white; padding: 30px; text-align: center; }
        .content { padding: 30px; background: #f9f9f9; }
        .success-box { background: white; padding: 25px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #28a745; }
        .footer { background: #333; color: white; padding: 15px; text-align: center; font-size: 12px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>✅ Test Email Successful!</h1>
        <p>DGMTS Email Configuration</p>
    </div>
    <div class="content">
        <div class="success-box">
            <h2 style="margin-top: 0; color: #28a745;">Email Configuration Working</h2>
            <p>If you received this email, your email configuration is working correctly!</p>
        </div>
        <p>Best regards,<br><strong>DGMTS Email System</strong></p>
    </div>
    <div class="footer">
        <p>This is an automated test email from the DGMTS email configuration system.</p>
    </div>
</body>
</html>
                '''
            }
            
        elif email_type == 'payment':
            # Payment confirmation email
            if not payment_data or not email:
                return jsonify({'error': 'Missing required fields for payment email: paymentData and email'}), 400
            
            customer_name = payment_data.get('customerName', 'Valued Customer')
            customer_email = payment_data.get('customerEmail', email)
            customer_address = payment_data.get('customerAddress', '')
            invoice_no = payment_data.get('invoiceNo', 'N/A')
            payment_note = payment_data.get('paymentNote', '')
            transaction_id = payment_data.get('transactionId', 'N/A')
            amount = float(payment_data.get('amount', 0))
            invoice_amount = float(payment_data.get('invoiceAmount', 0))
            service_charge = float(payment_data.get('serviceCharge', 0))
            payment_method = payment_data.get('paymentMethod', 'Credit Card')
            
            formatted_amount = f"${amount:,.2f}"
            formatted_invoice_amount = f"${invoice_amount:,.2f}"
            formatted_service_charge = f"${service_charge:,.2f}"
            payment_date = datetime.now().strftime('%B %d, %Y')
            
            # Customer email
            mail_options = {
                'from': f"{from_email_name} <{primary_config['email_id']}>",
                'to': customer_email,
                'bcc': payment_cc_emails,
                'subject': f"✅ Payment Confirmation - Invoice #{invoice_no}",
                'text': f'''
PAYMENT CONFIRMATION

Dear {customer_name},

Thank you for your Payment. Your transaction has been processed successfully.

TRANSACTION DETAILS:
- Transaction ID: {transaction_id}
- Invoice Number: {invoice_no}
- Payment Method: {payment_method}
- Payment Date: {payment_date}

PAYMENT SUMMARY:
- Invoice Amount: {formatted_invoice_amount}
- Service Charge: {formatted_service_charge}
- Total Amount Paid: {formatted_amount}

{f"PAYMENT NOTE:\\n{payment_note}\\n" if payment_note else ""}

Best regards,
DGMTS Team
                ''',
                'html': f'''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; }}
        .header {{ background: linear-gradient(135deg, #28a745 0%, #20c997 100%); color: white; padding: 30px; text-align: center; }}
        .content {{ padding: 30px; background: #f9f9f9; }}
        .success-box {{ background: white; padding: 25px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #28a745; }}
        .details-box {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; }}
        .summary-box {{ background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0; border: 2px solid #28a745; }}
        .footer {{ background: #333; color: white; padding: 15px; text-align: center; font-size: 12px; }}
        .label {{ font-weight: bold; color: #2795d0; }}
        .amount {{ font-size: 1.2em; font-weight: bold; color: #28a745; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>✅ Payment Confirmation</h1>
        <p>Thank You for Your Payment</p>
    </div>
    <div class="content">
        <p>Dear <strong>{customer_name}</strong>,</p>
        <p>Thank you for your Payment. Your transaction has been processed successfully.</p>
        <div class="success-box">
            <h2 style="margin-top: 0; color: #28a745;">Payment Processed</h2>
            <p>Your payment has been processed. Please keep this email for your records.</p>
        </div>
        <div class="details-box">
            <h3>Transaction Details</h3>
            <p><span class="label">Transaction ID:</span> {transaction_id}</p>
            <p><span class="label">Invoice Number:</span> {invoice_no}</p>
            <p><span class="label">Payment Method:</span> {payment_method}</p>
            <p><span class="label">Payment Date:</span> {payment_date}</p>
        </div>
        <div class="summary-box">
            <h3 style="color: #28a745; margin-top: 0;">Payment Summary</h3>
            <p><span class="label">Invoice Amount:</span> {formatted_invoice_amount}</p>
            <p><span class="label">Service Charge:</span> {formatted_service_charge}</p>
            <hr>
            <p><span class="label">Total Amount Paid:</span> <span class="amount">{formatted_amount}</span></p>
        </div>
        <p>Best regards,<br><strong>DGMTS Team</strong></p>
    </div>
    <div class="footer">
        <p>This is an automated payment confirmation from DGMTS.</p>
    </div>
</body>
</html>
                '''
            }
            
        elif email_type == 'newsletter':
            # Newsletter subscription welcome email
            if not email:
                return jsonify({'error': 'Missing required field: email'}), 400
            
            subscriber_name = name or email.split('@')[0]
            subscriber_token = token
            
            if not subscriber_token:
                subscriber_resp = dgmts_supabase.table('subscribers').select('token').eq('email', email).execute()
                if subscriber_resp.data:
                    subscriber_token = subscriber_resp.data[0].get('token')
            
            unsubscribe_url = f"https://dullesgeotechnical.com/unsubscribe?token={subscriber_token}" if subscriber_token else f"https://dullesgeotechnical.com/unsubscribe?email={email}"
            
            mail_options = {
                'from': f"{from_email_name} <{primary_config['email_id']}>",
                'to': email,
                'bcc': bcc_emails,
                'subject': '🎉 Welcome to DGMTS Newsletter!',
                'text': f'''
WELCOME TO DGMTS NEWSLETTER

Dear {subscriber_name},

Thank you for subscribing to the DGMTS newsletter! We're excited to have you join our community.

Best regards,
The DGMTS Team

---
You can unsubscribe at any time by visiting: {unsubscribe_url}
                ''',
                'html': f'''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; }}
        .header {{ background: linear-gradient(135deg, #2795d0 0%, #28a745 100%); color: white; padding: 30px; text-align: center; }}
        .content {{ padding: 30px; background: #f9f9f9; }}
        .welcome-box {{ background: white; padding: 25px; border-radius: 8px; margin: 20px 0; }}
        .footer {{ background: #333; color: white; padding: 15px; text-align: center; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🎉 Welcome to DGMTS Newsletter!</h1>
        <p>Thank You for Subscribing</p>
    </div>
    <div class="content">
        <p>Dear <strong>{subscriber_name}</strong>,</p>
        <p>Thank you for subscribing to the DGMTS newsletter!</p>
        <p>Best regards,<br><strong>The DGMTS Team</strong></p>
    </div>
    <div class="footer">
        <p>This email was sent to {email} because you subscribed to our newsletter.</p>
        <p><a href="{unsubscribe_url}" style="color: #4a90e2;">Unsubscribe</a></p>
    </div>
</body>
</html>
                '''
            }
            
        elif email_type == 'subscriber_notification':
            # Admin-sent newsletter/update
            if not email or not message:
                return jsonify({'error': 'Missing required fields: email and message'}), 400
            
            subscriber_name = name or email.split('@')[0]
            email_subject = subject or '📢 Important Update from DGMTS'
            subscriber_token = token
            
            if not subscriber_token:
                subscriber_resp = dgmts_supabase.table('subscribers').select('token').eq('email', email).execute()
                if subscriber_resp.data:
                    subscriber_token = subscriber_resp.data[0].get('token')
            
            unsubscribe_url = f"https://dullesgeotechnical.com/unsubscribe?token={subscriber_token}" if subscriber_token else f"https://dullesgeotechnical.com/unsubscribe?email={email}"
            
            # Check if complete HTML document
            is_complete_html = html_content and (html_content.strip().lower().startswith('<!doctype') or html_content.strip().lower().startswith('<html'))
            
            # Determine if we should use header/footer template
            use_template = include_header_footer and not is_complete_html
            
            if is_complete_html:
                # Inject unsubscribe footer
                unsubscribe_footer = f'''
<div style="text-align: center; padding: 20px; margin-top: 20px; border-top: 1px solid #ddd; font-size: 12px; color: #666;">
    <p>This email was sent to {email} because you are subscribed to our newsletter.</p>
    <p><a href="{unsubscribe_url}" style="color: #4a90e2;">Unsubscribe</a></p>
</div>
                '''
                if '</body>' in html_content.lower():
                    html_body = html_content.replace('</body>', unsubscribe_footer + '</body>')
                else:
                    html_body = html_content + unsubscribe_footer
            elif use_template:
                # Use DGMTS template wrapper with header and footer
                html_body = f'''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; }}
        .header {{ background: linear-gradient(135deg, #4a90e2 0%, #357abd 100%); color: white; padding: 40px 30px; text-align: center; }}
        .content {{ padding: 40px 30px; background: #ffffff; }}
        .footer {{ background: #2c3e50; color: white; padding: 25px; text-align: center; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📰 DGMTS Newsletter</h1>
    </div>
    <div class="content">
        <p>Dear <strong>{subscriber_name}</strong>,</p>
        <div>{html_content or message.replace(chr(10), '<br>')}</div>
        <p>Best regards,<br><strong>The DGMTS Team</strong></p>
    </div>
    <div class="footer">
        <p>This email was sent to {email} because you are subscribed to our newsletter.</p>
        <p><a href="{unsubscribe_url}" style="color: #4a90e2;">Unsubscribe</a></p>
    </div>
</body>
</html>
                '''
            else:
                # No template - send content as-is with just unsubscribe footer
                content_html = html_content or message.replace(chr(10), '<br>')
                unsubscribe_footer = f'''
<div style="text-align: center; padding: 20px; margin-top: 20px; border-top: 1px solid #ddd; font-size: 12px; color: #666;">
    <p>This email was sent to {email} because you are subscribed to our newsletter.</p>
    <p><a href="{unsubscribe_url}" style="color: #4a90e2;">Unsubscribe</a></p>
</div>
                '''
                html_body = f'''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }}
        img {{ max-width: 100%; height: auto; }}
    </style>
</head>
<body>
    {content_html}
    {unsubscribe_footer}
</body>
</html>
                '''
            
            mail_options = {
                'from': f"{from_email_name} <{primary_config['email_id']}>",
                'to': email,
                'subject': email_subject,
                'text': f'{email_subject}\n\n{message}\n\nBest regards,\nThe DGMTS Team',
                'html': html_body
            }
            
            # Add embedded images for CID references
            if embedded_images and isinstance(embedded_images, list) and len(embedded_images) > 0:
                mail_options['embedded_images'] = embedded_images
            
            # Add attachments (supports multiple file types)
            if attachments and isinstance(attachments, list) and len(attachments) > 0:
                mail_options['attachments'] = attachments
        
        else:
            # Contact form (default)
            if not name or not email or not message:
                return jsonify({'error': 'Missing required fields: name, email, message'}), 400
            
            mail_options = {
                'from': f"{from_email_name} Contact Form <{primary_config['email_id']}>",
                'to': 'info@dullesgeotechnical.com',
                'bcc': bcc_emails,
                'reply_to': email,
                'subject': f"🔔 New Contact Form Submission from {name}",
                'text': f'''
NEW CONTACT FORM SUBMISSION

You have received a new message through your website contact form.

SENDER DETAILS:
Name: {name}
Email: {email}

MESSAGE:
{message}

---
Reply directly to this email to respond to {name}.
                ''',
                'html': f'''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; text-align: center; }}
        .content {{ padding: 30px; background: #f9f9f9; }}
        .sender-info {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; }}
        .footer {{ background: #333; color: white; padding: 15px; text-align: center; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🔔 New Contact Form Submission</h1>
        <p>DGMTS Website</p>
    </div>
    <div class="content">
        <div class="sender-info">
            <h3>👤 Sender Information</h3>
            <p><strong>Name:</strong> {name}</p>
            <p><strong>Email:</strong> {email}</p>
        </div>
        <div class="sender-info">
            <h3>💬 Message</h3>
            <p>{message.replace(chr(10), '<br>')}</p>
        </div>
    </div>
    <div class="footer">
        <p>This email was automatically generated from your DGMTS website contact form.</p>
    </div>
</body>
</html>
                '''
            }
        
        # Send email with fallback
        result = send_email_with_fallback(mail_options)
        
        return jsonify({
            'message': 'Email sent successfully',
            'config_used': result['used_config']
        }), 200, {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'content-type, authorization, x-client-info, apikey',
            'Access-Control-Allow-Methods': 'POST, OPTIONS'
        }
        
    except Exception as e:
        print(f'Error in send-mail function: {e}')
        import traceback
        traceback.print_exc()
        return jsonify({
            'message': str(e),
            'error': str(e)
        }), 500, {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'content-type, authorization, x-client-info, apikey',
            'Access-Control-Allow-Methods': 'POST, OPTIONS'
        }

@email_bp.route('/test-email', methods=['POST'])
def test_email():
    """Test endpoint to verify email functionality"""
    data = request.get_json()
    test_email = data.get('email', 'mahmerraza19@gmail.com')
    
    subject = "Test Email - DGMTS"
    body = """
    <html>
    <body>
        <h2>Test Email</h2>
        <p>This is a test email to verify the email functionality is working.</p>
        <p>If you receive this email, the email configuration is correct.</p>
        <p>Best regards,<br>DGMTS Team</p>
    </body>
    </html>
    """
    
    if send_email(test_email, subject, body):
        return jsonify({"message": "Test email sent successfully"})
    else:
        return jsonify({"error": "Failed to send test email"}), 500

@email_bp.route('/test-tiltmeter-alert', methods=['POST'])
def test_tiltmeter_alert():
    """Test endpoint to send a sample tiltmeter alert email using actual data"""
    try:
        # Get email addresses from request body
        data = request.get_json() or {}
        test_emails = data.get('emails', ['mahmerraza19@gmail.com'])
        
        # Ensure test_emails is a list
        if isinstance(test_emails, str):
            test_emails = [email.strip() for email in test_emails.split(',') if email.strip()]
        elif not isinstance(test_emails, list):
            test_emails = ['mahmerraza19@gmail.com']
        
        # Get latest sensor readings for both nodes
        node_ids = [142939, 143969]
        actual_alerts = {}
        
        for node_id in node_ids:
            instrument_id = Config.NODE_TO_INSTRUMENT_ID.get(node_id)
            if not instrument_id:
                continue
                
            # Get instrument settings
            instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', instrument_id).execute()
            instrument = instrument_resp.data[0] if instrument_resp.data else None
            if not instrument:
                continue
            
            # Get reference values
            reference_resp = supabase.table('reference_values').select('*').eq('instrument_id', instrument_id).execute()
            reference_values = reference_resp.data[0] if reference_resp.data else None
            
            # Get latest sensor reading for this node
            latest_resp = supabase.table('sensor_readings') \
                .select('*') \
                .eq('node_id', node_id) \
                .order('timestamp', desc=True) \
                .limit(1) \
                .execute()
            latest_reading = latest_resp.data[0] if latest_resp.data else None
            
            # Get threshold values
            xyz_alert_values = instrument.get('x_y_z_alert_values')
            xyz_warning_values = instrument.get('x_y_z_warning_values')
            xyz_shutdown_values = instrument.get('x_y_z_shutdown_values')
            
            print(f"DEBUG TEST {node_id}: Latest reading found: {latest_reading is not None}")
            print(f"DEBUG TEST {node_id}: xyz_alert_values={xyz_alert_values}")
            print(f"DEBUG TEST {node_id}: reference_values enabled={reference_values.get('enabled', False) if reference_values else False}")
            
            if not latest_reading:
                continue
            
            # Process the latest reading
            timestamp = latest_reading['timestamp']
            x = latest_reading.get('x_value')
            y = latest_reading.get('y_value')
            z = latest_reading.get('z_value')
            
            # Format timestamp to EST
            try:
                dt_utc = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                est = pytz.timezone('US/Eastern')
                dt_est = dt_utc.astimezone(est)
                formatted_time = dt_est.strftime('%Y-%m-%d %I:%M %p EST')
            except Exception as e:
                print(f"Failed to parse/convert timestamp: {timestamp}, error: {e}")
                formatted_time = timestamp
            
            messages = []
            
            # Calculate calibrated values when reference values are enabled
            if reference_values and reference_values.get('enabled', False):
                ref_x = reference_values.get('reference_x_value', 0)
                ref_y = reference_values.get('reference_y_value', 0)
                ref_z = reference_values.get('reference_z_value', 0)
                
                # Convert to float to ensure proper calculation
                ref_x = float(ref_x) if ref_x is not None else 0.0
                ref_y = float(ref_y) if ref_y is not None else 0.0
                ref_z = float(ref_z) if ref_z is not None else 0.0
                
                # Calculate calibrated values (raw - reference)
                calibrated_x = float(x) - ref_x if x is not None else None
                calibrated_y = float(y) - ref_y if y is not None else None
                calibrated_z = float(z) - ref_z if z is not None else None
                
                print(f"DEBUG TEST {node_id}: Calibrated values - x={calibrated_x}, y={calibrated_y}, z={calibrated_z}")
                print(f"DEBUG TEST {node_id}: Reference values - ref_x={ref_x}, ref_y={ref_y}, ref_z={ref_z}")
                
                # Use original (unadjusted) thresholds for comparison
                base_xyz_alert_values = instrument.get('x_y_z_alert_values')
                base_xyz_warning_values = instrument.get('x_y_z_warning_values')
                base_xyz_shutdown_values = instrument.get('x_y_z_shutdown_values')
                
                # Check shutdown thresholds using calibrated values (X and Z only, no Y)
                for axis, calibrated_value, axis_key, axis_desc in [('X', calibrated_x, 'x', 'Longitudinal'), ('Z', calibrated_z, 'z', 'Transverse')]:
                    if calibrated_value is None:
                        continue
                    axis_shutdown_value = base_xyz_shutdown_values.get(axis_key) if base_xyz_shutdown_values else None
                    if axis_shutdown_value and abs(calibrated_value) >= axis_shutdown_value:
                        messages.append(f"<b>Shutdown threshold reached on {axis}-axis ({axis_desc}) > {axis_shutdown_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}")
                
                # Check warning thresholds using calibrated values (X and Z only, no Y)
                for axis, calibrated_value, axis_key, axis_desc in [('X', calibrated_x, 'x', 'Longitudinal'), ('Z', calibrated_z, 'z', 'Transverse')]:
                    if calibrated_value is None:
                        continue
                    axis_warning_value = base_xyz_warning_values.get(axis_key) if base_xyz_warning_values else None
                    if axis_warning_value and abs(calibrated_value) >= axis_warning_value:
                        messages.append(f"<b>Warning threshold reached on {axis}-axis ({axis_desc}) > {axis_warning_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}")
                
                # Check alert thresholds using calibrated values (X and Z only, no Y)
                for axis, calibrated_value, axis_key, axis_desc in [('X', calibrated_x, 'x', 'Longitudinal'), ('Z', calibrated_z, 'z', 'Transverse')]:
                    if calibrated_value is None:
                        continue
                    axis_alert_value = base_xyz_alert_values.get(axis_key) if base_xyz_alert_values else None
                    print(f"DEBUG TEST {node_id} {axis}: calibrated_value={calibrated_value}, axis_alert_value={axis_alert_value}, abs(calibrated_value)={abs(calibrated_value)}, threshold_check={abs(calibrated_value) >= axis_alert_value if axis_alert_value else False}")
                    if axis_alert_value and abs(calibrated_value) >= axis_alert_value:
                        messages.append(f"<b>Alert threshold reached on {axis}-axis ({axis_desc}) > {axis_alert_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}")
            else:
                print(f"DEBUG TEST {node_id}: Reference values not enabled, using raw values")
                print(f"DEBUG TEST {node_id}: Raw values - x={x}, y={y}, z={z}")
                print(f"DEBUG TEST {node_id}: Threshold values - alert={xyz_alert_values}, warning={xyz_warning_values}, shutdown={xyz_shutdown_values}")
                
                # Use original logic when reference values are not enabled (X and Z only, no Y)
                # Check shutdown thresholds
                for axis, value, axis_key, axis_desc in [('X', x, 'x', 'Longitudinal'), ('Z', z, 'z', 'Transverse')]:
                    if value is None:
                        continue
                    axis_shutdown_value = xyz_shutdown_values.get(axis_key) if xyz_shutdown_values else None
                    if axis_shutdown_value and abs(value) >= axis_shutdown_value:
                        messages.append(f"<b>Shutdown threshold reached on {axis}-axis ({axis_desc}) > {axis_shutdown_value:.3f}: value- {value:.6f} at {formatted_time}")
                
                # Check warning thresholds
                for axis, value, axis_key, axis_desc in [('X', x, 'x', 'Longitudinal'), ('Z', z, 'z', 'Transverse')]:
                    if value is None:
                        continue
                    axis_warning_value = xyz_warning_values.get(axis_key) if xyz_warning_values else None
                    if axis_warning_value and abs(value) >= axis_warning_value:
                        messages.append(f"<b>Warning threshold reached on {axis}-axis ({axis_desc}) > {axis_warning_value:.3f}: value- {value:.6f} at {formatted_time}")
                
                # Check alert thresholds
                for axis, value, axis_key, axis_desc in [('X', x, 'x', 'Longitudinal'), ('Z', z, 'z', 'Transverse')]:
                    if value is None:
                        continue
                    axis_alert_value = xyz_alert_values.get(axis_key) if xyz_alert_values else None
                    if axis_alert_value and abs(value) >= axis_alert_value:
                        messages.append(f"<b>Alert threshold reached on {axis}-axis ({axis_desc}) > {axis_alert_value:.3f}: value- {value:.6f} at {formatted_time}")
            
            if messages:
                node_messages = [f"<u><b>Timestamp: {formatted_time}</b></u><br>" + "<br>".join(messages)]
                actual_alerts[node_id] = node_messages
        
        # If no actual alerts found, return empty response
        if not actual_alerts:
            return jsonify({
                "message": "No tiltmeter alerts found in latest readings. No email sent.",
                "note": "Only sends emails when actual thresholds are exceeded"
            })
        
        # Create email body with professional styling
        body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; background-color: #f5f5f5; }}
                .container {{ max-width: 600px; margin: 0 auto; background-color: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow: hidden; }}
                .header {{ background: linear-gradient(135deg, #0056d2 0%, #007bff 100%); color: white; padding: 20px; text-align: center; }}
                .header h1 {{ margin: 0; font-size: 24px; font-weight: bold; }}
                .header p {{ margin: 5px 0 0 0; opacity: 0.9; }}
                .content {{ padding: 30px; }}
                .alert-section {{ margin-bottom: 25px; }}
                .alert-section h3 {{ color: #0056d2; border-bottom: 2px solid #0056d2; padding-bottom: 10px; margin-bottom: 15px; }}
                .alert-item {{ background-color: #f8f9fa; border-left: 4px solid #dc3545; padding: 15px; margin-bottom: 10px; border-radius: 4px; }}
                .alert-item.warning {{ border-left-color: #ffc107; }}
                .alert-item.alert {{ border-left-color: #fd7e14; }}
                .alert-item.shutdown {{ border-left-color: #dc3545; }}
                .timestamp {{ font-weight: bold; color: #495057; margin-bottom: 10px; }}
                .alert-message {{ color: #212529; line-height: 1.5; }}
                .footer {{ background-color: #f8f9fa; padding: 20px; text-align: center; color: #6c757d; border-top: 1px solid #dee2e6; }}
                .footer p {{ margin: 0; }}
                .company-info {{ font-weight: bold; color: #0056d2; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🚨 TILTMETER ALERT NOTIFICATION</h1>
                    <p>Dulles Geotechnical Monitoring System</p>
                </div>
                
                <div class="content">
                    <p style="font-size: 16px; color: #495057; margin-bottom: 25px;">
                        This is an automated alert notification from the DGMTS monitoring system. 
                        The following tiltmeter thresholds have been exceeded in the latest readings:
                    </p>
        """
        
        # Add alerts for each node
        for node_id, alerts in actual_alerts.items():
            body += f"""
                    <div class="alert-section">
                        <h3>📊 Node {node_id} - Tiltmeter Alerts</h3>
            """
            
            for alert in alerts:
                # Determine alert type for styling
                alert_class = "alert-item"
                if "Shutdown" in alert:
                    alert_class += " shutdown"
                elif "Warning" in alert:
                    alert_class += " warning"
                elif "Alert" in alert:
                    alert_class += " alert"
                elif "Test Alert" in alert:
                    alert_class += " alert"  # Use alert styling for test alerts
                
                # Extract timestamp and message
                alert_parts = alert.split('<br>')
                timestamp = alert_parts[0].replace('<u><b>', '').replace('</b></u>', '')
                message = '<br>'.join(alert_parts[1:]) if len(alert_parts) > 1 else alert
                
                body += f"""
                        <div class="{alert_class}">
                            <div class="timestamp">{timestamp}</div>
                            <div class="alert-message">{message}</div>
                        </div>
                """
            
            body += """
                    </div>
            """
        
        body += f"""
                    <div style="background-color: #e7f3ff; border: 1px solid #b3d9ff; border-radius: 4px; padding: 15px; margin-top: 20px;">
                        <p style="margin: 0; color: #0056d2; font-weight: bold;">⚠️ Action Required:</p>
                        <p style="margin: 5px 0 0 0; color: #495057;">
                            Please review the tiltmeter data and take appropriate action if necessary.                    
                        </p>
                    </div>
                </div>
                
                <div class="footer">
                    <p><span class="company-info">Dulles Geotechnical</span> | Instrumentation Monitoring System</p>
                    <p style="font-size: 12px; margin-top: 5px;">
                        This is an automated message. Please do not reply to this email.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        current_time = datetime.now(timezone.utc)
        est = pytz.timezone('US/Eastern')
        current_time_est = current_time.astimezone(est)
        formatted_current_time = current_time_est.strftime('%Y-%m-%d %I:%M %p EST')
        
        subject = f"🚨 Tiltmeter Alert Notification - {formatted_current_time}"
        
        # Send to test emails
        if send_email(test_emails, subject, body):
            return jsonify({
                "message": f"Tiltmeter alert email sent successfully to {', '.join(test_emails)}",
                "subject": subject,
                "note": "This shows actual threshold breaches from latest readings",
                "emails_sent_to": test_emails
            })
        else:
            return jsonify({"error": "Failed to send tiltmeter alert email"}), 500
            
    except Exception as e:
        return jsonify({"error": f"Failed to send tiltmeter alert: {str(e)}"}), 500

@email_bp.route('/trigger-tiltmeter-alerts', methods=['POST'])
def trigger_tiltmeter_alerts():
    """Manually trigger the actual tiltmeter alert system"""
    try:
        print("Manually triggering tiltmeter alert system...")
        check_and_send_tiltmeter_alerts()
        return jsonify({
            "message": "Tiltmeter alert system triggered successfully",
            "status": "success"
        })
    except Exception as e:
        print(f"Error triggering tiltmeter alerts: {e}")
        return jsonify({"error": f"Failed to trigger tiltmeter alerts: {str(e)}"}), 500

@email_bp.route('/test-tiltmeter-alerts-with-time-based-refs', methods=['POST'])
def test_tiltmeter_alerts_with_time_based_refs():
    """Test endpoint to trigger tiltmeter alerts and see time-based reference system in action"""
    try:
        # Get email addresses from request body for testing
        data = request.get_json() or {}
        test_emails = data.get('emails', [])
        
        # Ensure test_emails is a list
        if isinstance(test_emails, str):
            test_emails = [email.strip() for email in test_emails.split(',') if email.strip()]
        elif not isinstance(test_emails, list):
            test_emails = []
        
        print("🧪 Testing Tiltmeter Alerts with Time-Based References")
        
        if test_emails:
            print(f"📧 Test emails provided: {', '.join(test_emails)}")
        else:
            print("📧 No test emails provided - will use configured alert emails")
        
        # First show the time-based reference system test
        from services.alert_service import test_time_based_reference_system
        test_time_based_reference_system()
        
        print("🚨 Checking for threshold violations...")
        
        # Then trigger the actual alert system
        check_and_send_tiltmeter_alerts()
        
        return jsonify({
            "message": "Tiltmeter alert test completed successfully - check console logs for time-based reference details",
            "status": "success",
            "note": "Check your email and console logs to see the time-based reference system in action",
            "test_emails_used": test_emails if test_emails else "Using configured alert emails"
        })
    except Exception as e:
        print(f"Error testing tiltmeter alerts with time-based references: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Failed to test tiltmeter alerts: {str(e)}"}), 500

@email_bp.route('/test-time-based-references', methods=['POST'])
def test_time_based_references():
    """Test endpoint to verify the time-based reference system"""
    try:
        from services.alert_service import test_time_based_reference_system
        print("Testing time-based reference system...")
        test_time_based_reference_system()
        return jsonify({
            "message": "Time-based reference system test completed successfully",
            "status": "success"
        })
    except Exception as e:
        print(f"Error testing time-based references: {e}")
        return jsonify({"error": f"Failed to test time-based references: {str(e)}"}), 500

@email_bp.route('/test-tiltmeter-alert-simple', methods=['POST'])
def test_tiltmeter_alert_simple():
    """Test endpoint to send a sample tiltmeter alert email with time-based references"""
    try:
        # Get email addresses from request body
        data = request.get_json() or {}
        test_emails = data.get('emails', ['mahmerraza19@gmail.com'])
        
        # Ensure test_emails is a list
        if isinstance(test_emails, str):
            test_emails = [email.strip() for email in test_emails.split(',') if email.strip()]
        elif not isinstance(test_emails, list):
            test_emails = ['mahmerraza19@gmail.com']
        
        print(f"🧪 Testing Tiltmeter Alert with Time-Based References")
        print(f"📧 Sending to: {', '.join(test_emails)}")
        
        # First show the time-based reference system test
        from services.alert_service import test_time_based_reference_system
        test_time_based_reference_system()
        
        # Get latest sensor readings for both nodes
        node_ids = [142939, 143969]
        actual_alerts = {}
        
        for node_id in node_ids:
            instrument_id = Config.NODE_TO_INSTRUMENT_ID.get(node_id)
            if not instrument_id:
                continue
                
            # Get instrument settings
            instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', instrument_id).execute()
            instrument = instrument_resp.data[0] if instrument_resp.data else None
            if not instrument:
                continue
            
            # Get time-based reference values first, then fallback to global
            from services.alert_service import get_time_based_reference_values
            time_based_ref = get_time_based_reference_values(instrument_id)
            
            if time_based_ref:
                reference_values = time_based_ref
                print(f"Using time-based reference values for {instrument_id}")
            else:
                # Fall back to global reference values
                reference_resp = supabase.table('reference_values').select('*').eq('instrument_id', instrument_id).execute()
                reference_values = reference_resp.data[0] if reference_resp.data else None
                if reference_values:
                    print(f"Using global reference values for {instrument_id}")
                else:
                    print(f"No reference values found for {instrument_id}")
            
            # Get latest sensor reading for this node
            latest_resp = supabase.table('sensor_readings') \
                .select('*') \
                .eq('node_id', node_id) \
                .order('timestamp', desc=True) \
                .limit(1) \
                .execute()
            latest_reading = latest_resp.data[0] if latest_resp.data else None
            
            if not latest_reading:
                print(f"No readings found for node {node_id}")
                continue
            
            # Get threshold values
            xyz_alert_values = instrument.get('x_y_z_alert_values')
            xyz_warning_values = instrument.get('x_y_z_warning_values')
            xyz_shutdown_values = instrument.get('x_y_z_shutdown_values')
            
            # Extract values
            x = latest_reading.get('x_value')
            y = latest_reading.get('y_value')
            z = latest_reading.get('z_value')
            timestamp = latest_reading.get('timestamp')
            
            # Format timestamp
            try:
                dt_utc = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                est = pytz.timezone('US/Eastern')
                dt_est = dt_utc.astimezone(est)
                formatted_time = dt_est.strftime('%Y-%m-%d %I:%M %p EST')
            except Exception as e:
                formatted_time = timestamp
            
            messages = []
            
            # Calculate calibrated values when reference values are enabled
            if reference_values and reference_values.get('enabled', False):
                ref_x = reference_values.get('x_reference_value') or 0
                ref_y = reference_values.get('y_reference_value') or 0
                ref_z = reference_values.get('z_reference_value') or 0
                
                # Calculate calibrated values (raw - reference)
                calibrated_x = x - ref_x if x is not None else None
                calibrated_y = y - ref_y if y is not None else None
                calibrated_z = z - ref_z if z is not None else None
                
                ref_type = "time-based" if reference_values.get('time_based', False) else "global"
                print(f"Reference values enabled for {instrument_id} ({ref_type}): X={ref_x}, Y={ref_y}, Z={ref_z}")
                if reference_values.get('time_based', False):
                    print(f"Time-based period: {reference_values.get('from_date')} to {reference_values.get('to_date')}")
                print(f"Raw values: X={x}, Y={y}, Z={z}")
                print(f"Calibrated values: X={calibrated_x}, Y={calibrated_y}, Z={calibrated_z}")
                
                # Check thresholds using calibrated values (X and Z only, no Y)
                for axis, calibrated_value, axis_key, axis_desc in [('X', calibrated_x, 'x', 'Longitudinal'), ('Z', calibrated_z, 'z', 'Transverse')]:
                    if calibrated_value is None:
                        continue
                    
                    # Check shutdown thresholds
                    axis_shutdown_value = xyz_shutdown_values.get(axis_key) if xyz_shutdown_values else None
                    if axis_shutdown_value and abs(calibrated_value) >= axis_shutdown_value:
                        messages.append(f"<b>Shutdown threshold reached on {axis}-axis ({axis_desc}) > {axis_shutdown_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}</b>")
                    
                    # Check warning thresholds
                    axis_warning_value = xyz_warning_values.get(axis_key) if xyz_warning_values else None
                    if axis_warning_value and abs(calibrated_value) >= axis_warning_value:
                        messages.append(f"<b>Warning threshold reached on {axis}-axis ({axis_desc}) > {axis_warning_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}</b>")
                    
                    # Check alert thresholds
                    axis_alert_value = xyz_alert_values.get(axis_key) if xyz_alert_values else None
                    if axis_alert_value and abs(calibrated_value) >= axis_alert_value:
                        messages.append(f"<b>Alert threshold reached on {axis}-axis ({axis_desc}) > {axis_alert_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}</b>")
            else:
                # Use original logic when reference values are not enabled
                for axis, value, axis_key, axis_desc in [('X', x, 'x', 'Longitudinal'), ('Z', z, 'z', 'Transverse')]:
                    if value is None:
                        continue
                    
                    # Check shutdown thresholds
                    axis_shutdown_value = xyz_shutdown_values.get(axis_key) if xyz_shutdown_values else None
                    if axis_shutdown_value and abs(value) >= axis_shutdown_value:
                        messages.append(f"<b>Shutdown threshold reached on {axis}-axis ({axis_desc}) > {axis_shutdown_value:.3f}: value- {value:.6f} at {formatted_time}</b>")
                    
                    # Check warning thresholds
                    axis_warning_value = xyz_warning_values.get(axis_key) if xyz_warning_values else None
                    if axis_warning_value and abs(value) >= axis_warning_value:
                        messages.append(f"<b>Warning threshold reached on {axis}-axis ({axis_desc}) > {axis_warning_value:.3f}: value- {value:.6f} at {formatted_time}</b>")
                    
                    # Check alert thresholds
                    axis_alert_value = xyz_alert_values.get(axis_key) if xyz_alert_values else None
                    if axis_alert_value and abs(value) >= axis_alert_value:
                        messages.append(f"<b>Alert threshold reached on {axis}-axis ({axis_desc}) > {axis_alert_value:.3f}: value- {value:.6f} at {formatted_time}</b>")
            
            if messages:
                actual_alerts[node_id] = [f"<u><b>Timestamp: {formatted_time}</b></u><br>" + "<br>".join(messages)]
        
        if actual_alerts:
            # Create email body
            body = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; background-color: #f5f5f5; }}
                    .container {{ max-width: 600px; margin: 0 auto; background-color: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow: hidden; }}
                    .header {{ background: linear-gradient(135deg, #0056d2 0%, #007bff 100%); color: white; padding: 20px; text-align: center; }}
                    .header h1 {{ margin: 0; font-size: 24px; font-weight: bold; }}
                    .header p {{ margin: 5px 0 0 0; opacity: 0.9; }}
                    .content {{ padding: 30px; }}
                    .alert-section {{ margin-bottom: 25px; }}
                    .alert-section h3 {{ color: #0056d2; border-bottom: 2px solid #0056d2; padding-bottom: 10px; margin-bottom: 15px; }}
                    .alert-item {{ background-color: #f8f9fa; border-left: 4px solid #dc3545; padding: 15px; margin-bottom: 10px; border-radius: 4px; }}
                    .footer {{ background-color: #f8f9fa; padding: 20px; text-align: center; color: #6c757d; font-size: 14px; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>🚨 Tiltmeter Alert</h1>
                        <p>Dulles Airport Monitoring System</p>
                    </div>
                    <div class="content">
                        <div class="alert-section">
                            <h3>Alert Details</h3>
            """
            
            for node_id, node_messages in actual_alerts.items():
                body += f"<h4>Node {node_id}</h4>"
                for message in node_messages:
                    body += f'<div class="alert-item">{message}</div>'
            
            body += """
                        </div>
                    </div>
                    <div class="footer">
                        <p>This is a test alert to verify the time-based reference system.</p>
                        <p>Dulles Geotechnical Monitoring & Testing Services</p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            # Format current time for subject
            try:
                current_time = datetime.now(pytz.timezone('US/Eastern'))
                formatted_current_time = current_time.strftime('%Y-%m-%d %I:%M %p EST')
            except Exception as e:
                formatted_current_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            
            subject = f"🚨 Tiltmeter Alert Notification - {formatted_current_time}"
            
            # Send email to all test emails
            for email in test_emails:
                if send_email(email, subject, body):
                    print(f"✅ Test alert email sent successfully to {email}")
                else:
                    print(f"❌ Failed to send test alert email to {email}")
            
            return jsonify({
                "message": "Test tiltmeter alert sent successfully",
                "status": "success",
                "emails_sent_to": test_emails,
                "alerts_found": len(actual_alerts),
                "note": "Check console logs for time-based reference system details"
            })
        else:
            return jsonify({
                "message": "No alerts found - thresholds not exceeded",
                "status": "success",
                "emails_sent_to": test_emails,
                "note": "Check console logs for time-based reference system details"
            })
            
    except Exception as e:
        print(f"Error in test tiltmeter alert: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Failed to send test tiltmeter alert: {str(e)}"}), 500

@email_bp.route('/test-seismograph-alert', methods=['POST'])
def test_seismograph_alert():
    """Test endpoint to send a sample seismograph alert email calling the actual alert service from services/alert_service.py"""
    try:
        # Get email addresses from request body
        data = request.get_json() or {}
        test_emails = data.get('emails', ['mahmerraza19@gmail.com'])
        seismograph_type = data.get('type', 'SMG-1')  # Default to SMG-1
        
        # Ensure test_emails is a list
        if isinstance(test_emails, str):
            test_emails = [email.strip() for email in test_emails.split(',') if email.strip()]
        elif not isinstance(test_emails, list):
            test_emails = ['mahmerraza19@gmail.com']
        
        # Get instrument settings to verify it exists
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', seismograph_type).execute()
        instrument = instrument_resp.data[0] if instrument_resp.data else None
        if not instrument:
            return jsonify({"error": f"No instrument found for {seismograph_type}"}), 404
        
        # Call the actual alert service with custom emails
        if seismograph_type == 'SMG-1':
            check_and_send_seismograph_alert(custom_emails=test_emails)
        elif seismograph_type == 'SMG-3':
            # Note: SMG-3 function doesn't have custom_emails parameter yet
            check_and_send_smg3_seismograph_alert()
        else:
            return jsonify({"error": f"Unsupported seismograph type: {seismograph_type}"}), 400
        
        return jsonify({
            "message": f"Test seismograph alert sent successfully for {seismograph_type}",
            "emails_sent_to": test_emails,
            "instrument_id": seismograph_type
        }), 200
        
    except Exception as e:
        print(f"Error in test_seismograph_alert: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Failed to send test seismograph alert: {str(e)}"}), 500

@email_bp.route('/test-rock-seismograph-alert', methods=['POST'])
def test_rock_seismograph_alert():
    """Test endpoint to send a sample Rock Seismograph alert email"""
    try:
        # Get email addresses and instrument type from request body
        data = request.get_json() or {}
        test_emails = data.get('emails', ['mahmerraza19@gmail.com'])
        instrument_id = data.get('instrument_id', 'ROCKSMG-1')  # ROCKSMG-1 or ROCKSMG-2
        
        # Ensure test_emails is a list
        if isinstance(test_emails, str):
            test_emails = [email.strip() for email in test_emails.split(',') if email.strip()]
        elif not isinstance(test_emails, list):
            test_emails = ['mahmerraza19@gmail.com']
        
        # Validate instrument_id
        if instrument_id not in Config.ROCK_SEISMOGRAPH_INSTRUMENTS:
            return jsonify({"error": f"Invalid instrument_id. Must be one of: {list(Config.ROCK_SEISMOGRAPH_INSTRUMENTS.keys())}"}), 400
        
        # Get instrument settings
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', instrument_id).execute()
        instrument = instrument_resp.data[0] if instrument_resp.data else None
        if not instrument:
            return jsonify({"error": f"No instrument found for {instrument_id}"}), 404
        
        # Create test alert data
        test_alerts = {
            'test_hour': {
                'messages': [
                    "<b>Test Alert threshold reached on X-axis:</b> 0.001234",
                    "<b>Test Warning threshold reached on Y-axis:</b> 0.002345",
                    "<b>Test Shutdown threshold reached on Z-axis:</b> 0.003456"
                ],
                'timestamp': datetime.now(pytz.timezone('US/Eastern')).strftime('%Y-%m-%d %I:%M %p EST'),
                'max_values': {'X': 0.001234, 'Y': 0.002345, 'Z': 0.003456}
            }
        }
        
        # Get instrument details from config
        seismograph_name = Config.ROCK_SEISMOGRAPH_INSTRUMENTS[instrument_id]['name']
        project_name = Config.ROCK_SEISMOGRAPH_INSTRUMENTS[instrument_id]['project_name']
        
        # Create email body
        body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; background-color: #f5f5f5; }}
                .container {{ max-width: 600px; margin: 0 auto; background-color: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow: hidden; }}
                .header {{ background: linear-gradient(135deg, #0056d2 0%, #007bff 100%); color: white; padding: 20px; text-align: center; }}
                .header h1 {{ margin: 0; font-size: 24px; font-weight: bold; }}
                .header p {{ margin: 5px 0 0 0; opacity: 0.9; }}
                .content {{ padding: 30px; }}
                .alert-section {{ margin-bottom: 25px; }}
                .alert-section h3 {{ color: #0056d2; border-bottom: 2px solid #0056d2; padding-bottom: 10px; margin-bottom: 15px; }}
                .alert-item {{ background-color: #f8f9fa; border-left: 4px solid #dc3545; padding: 15px; margin-bottom: 10px; border-radius: 4px; }}
                .alert-item.warning {{ border-left-color: #ffc107; }}
                .alert-item.alert {{ border-left-color: #fd7e14; }}
                .alert-item.shutdown {{ border-left-color: #dc3545; }}
                .timestamp {{ font-weight: bold; color: #495057; margin-bottom: 10px; }}
                .alert-message {{ color: #212529; line-height: 1.5; }}
                .max-values {{ background-color: #e9ecef; padding: 10px; border-radius: 4px; margin-top: 10px; }}
                .max-values table {{ width: 100%; border-collapse: collapse; }}
                .max-values th, .max-values td {{ padding: 8px; text-align: center; border: 1px solid #dee2e6; }}
                .max-values th {{ background-color: #f8f9fa; font-weight: bold; }}
                .footer {{ background-color: #f8f9fa; padding: 20px; text-align: center; color: #6c757d; border-top: 1px solid #dee2e6; }}
                .footer p {{ margin: 0; }}
                .company-info {{ font-weight: bold; color: #0056d2; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🌊 {seismograph_name.upper()} ALERT NOTIFICATION</h1>
                    <p>Dulles Geotechnical Monitoring System - {project_name}</p>
                </div>
                
                <div class="content">
                    <p style="font-size: 16px; color: #495057; margin-bottom: 25px;">
                        This is a <strong>TEST</strong> alert notification from the DGMTS monitoring system. 
                        The following {seismograph_name} ({instrument_id}) thresholds have been exceeded:
                    </p>
        """
        
        # Add alerts for each hour
        for hour_key, alert_data in test_alerts.items():
            body += f"""
                    <div class="alert-section">
                        <h3>📊 Hour: {hour_key.replace('_', ' ').title()} - {seismograph_name} Alerts ({instrument_id})</h3>
            """
            
            for message in alert_data['messages']:
                # Determine alert type for styling
                alert_class = "alert-item"
                if "Shutdown" in message:
                    alert_class += " shutdown"
                elif "Warning" in message:
                    alert_class += " warning"
                elif "Alert" in message:
                    alert_class += " alert"
                
                body += f"""
                        <div class="{alert_class}">
                            <div class="timestamp">{alert_data['timestamp']}</div>
                            <div class="alert-message">{message}</div>
                            <div class="max-values">
                                <table>
                                    <thead>
                                        <tr>
                                            <th>Axis</th>
                                            <th>Max Value (in/s)</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        <tr>
                                            <td>X (Longitudinal)</td>
                                            <td>{alert_data['max_values']['X']:.6f}</td>
                                        </tr>
                                        <tr>
                                            <td>Y (Vertical)</td>
                                            <td>{alert_data['max_values']['Y']:.6f}</td>
                                        </tr>
                                        <tr>
                                            <td>Z (Transverse)</td>
                                            <td>{alert_data['max_values']['Z']:.6f}</td>
                                        </tr>
                                    </tbody>
                                </table>
                            </div>
                        </div>
                """
            
            body += """
                    </div>
            """
        
        body += f"""
                    <div style="background-color: #e7f3ff; border: 1px solid #b3d9ff; border-radius: 4px; padding: 15px; margin-top: 20px;">
                        <p style="margin: 0; color: #0056d2; font-weight: bold;">⚠️ Action Required:</p>
                        <p style="margin: 5px 0 0 0; color: #495057;">
                            Please review the {seismograph_name} data and take appropriate action if necessary. 
                            This is a test email to verify the alert system is working correctly.
                            <br><br>
                            <strong>Project:</strong> {project_name}<br>
                            <strong>Instrument:</strong> {instrument_id}
                        </p>
                    </div>
                </div>
                
                <div class="footer">
                    <p><span class="company-info">Dulles Geotechnical</span> | Instrumentation Monitoring System</p>
                    <p style="font-size: 12px; margin-top: 5px;">
                        This is a test message. Please do not reply to this email.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        current_time = datetime.now(timezone.utc)
        current_time_est = current_time.astimezone(pytz.timezone('US/Eastern'))
        formatted_time = current_time_est.strftime('%Y-%m-%d %I:%M %p EST')
        subject = f"🌊 {seismograph_name} Test Alert Notification - {formatted_time}"
        
        if send_email(test_emails, subject, body):
            return jsonify({
                "message": f"Test {seismograph_name} alert email sent successfully",
                "instrument_id": instrument_id,
                "project_name": project_name,
                "emails_sent_to": test_emails
            })
        else:
            return jsonify({"error": f"Failed to send test {seismograph_name} alert email"}), 500
            
    except Exception as e:
        print(f"Error in test_rock_seismograph_alert: {e}")
        return jsonify({"error": str(e)}), 500

@email_bp.route('/check-connection-lost', methods=['POST'])
def check_connection_lost():
    """Manual trigger to check for connection lost errors and send notifications"""
    try:
        # Call the connection monitor service
        check_and_send_connection_lost_alerts()
        
        return jsonify({
            "message": "Connection lost check completed successfully",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }), 200
        
    except Exception as e:
        print(f"Error in check_connection_lost: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Failed to check connection lost alerts: {str(e)}"}), 500

@email_bp.route('/test-dullesgeotechnical-mail', methods=['POST'])
def test_dullesgeotechnical_mail():
    """
    Test email endpoint with custom SMTP credentials
    
    Request body (JSON):
    {
        "provider_email": "your-email@gmail.com",
        "app_password": "your-app-password",
        "recipient_email": "recipient@example.com",
        "from_name": "Dulles Geotechnical"
    }
    """
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        
        # Get request data
        data = request.get_json()
        
        if not data:
            return jsonify({
                'error': 'No request body provided',
                'message': 'Please provide provider_email, app_password, recipient_email, and from_name'
            }), 400
        
        provider_email = data.get('provider_email')
        app_password = data.get('app_password')
        recipient_email = data.get('recipient_email')
        from_name = data.get('from_name', 'Dulles Geotechnical')
        
        # Validate required fields
        if not provider_email:
            return jsonify({'error': 'provider_email is required'}), 400
        if not app_password:
            return jsonify({'error': 'app_password is required'}), 400
        if not recipient_email:
            return jsonify({'error': 'recipient_email is required'}), 400
        
        # Create test email
        msg = MIMEMultipart()
        msg['From'] = f"{from_name} <{provider_email}>"
        msg['To'] = recipient_email
        msg['Subject'] = "Test Email - Dulles Geotechnical Monitoring System"
        
        # Email body
        body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; background-color: #f5f5f5; }}
                .container {{ max-width: 600px; margin: 0 auto; background-color: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow: hidden; }}
                .header {{ background: linear-gradient(135deg, #0056d2 0%, #007bff 100%); color: white; padding: 30px 20px; text-align: center; }}
                .header h1 {{ margin: 0; font-size: 28px; font-weight: bold; }}
                .header p {{ margin: 10px 0 0 0; opacity: 0.9; font-size: 14px; }}
                .content {{ padding: 30px; }}
                .content h2 {{ color: #0056d2; margin-top: 0; }}
                .content p {{ line-height: 1.6; color: #495057; }}
                .info-box {{ background-color: #e7f3ff; border: 1px solid #b3d9ff; border-radius: 4px; padding: 15px; margin: 20px 0; }}
                .info-box p {{ margin: 5px 0; color: #0056d2; }}
                .success-badge {{ display: inline-block; background-color: #28a745; color: white; padding: 5px 15px; border-radius: 20px; font-size: 14px; font-weight: bold; }}
                .footer {{ background-color: #f8f9fa; padding: 20px; text-align: center; color: #6c757d; border-top: 1px solid #dee2e6; }}
                .footer p {{ margin: 5px 0; font-size: 12px; }}
                .company-info {{ font-weight: bold; color: #0056d2; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>✅ Test Email Successful!</h1>
                    <p>Dulles Geotechnical Monitoring System</p>
                </div>
                
                <div class="content">
                    <p><span class="success-badge">✓ SUCCESS</span></p>
                    
                    <h2>Email Configuration Test</h2>
                    <p>Congratulations! Your email configuration is working correctly.</p>
                    <p>This test email was sent successfully from the Dulles Geotechnical Monitoring System.</p>
                    
                    <div class="info-box">
                        <p><strong>Test Details:</strong></p>
                        <p>📧 <strong>From:</strong> {from_name}</p>
                        <p>📬 <strong>Sender Email:</strong> {provider_email}</p>
                        <p>📨 <strong>Recipient:</strong> {recipient_email}</p>
                        <p>🕒 <strong>Sent At:</strong> {datetime.now(pytz.timezone('US/Eastern')).strftime('%Y-%m-%d %I:%M:%S %p EST')}</p>
                    </div>
                    
                    <p>If you received this email, your SMTP configuration is set up correctly and ready to send alert notifications.</p>
                    
                    <p style="color: #28a745; font-weight: bold;">✓ Email system is operational</p>
                </div>
                
                <div class="footer">
                    <p><span class="company-info">Dulles Geotechnical</span> | Instrumentation Monitoring System</p>
                    <p>This is a test message to verify email functionality.</p>
                    <p>Please do not reply to this email.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(body, 'html'))
        
        print(f"Attempting to send test email...")
        print(f"From: {provider_email}")
        print(f"To: {recipient_email}")
        print(f"From Name: {from_name}")
        
        # Connect to Gmail SMTP server
        smtp_server = "smtp.gmail.com"
        smtp_port = 465
        
        server = smtplib.SMTP_SSL(smtp_server, smtp_port)
        print("SMTP SSL connection established")
        
        # Login with provided credentials
        server.login(provider_email, app_password)
        print("Login successful")
        
        # Send email
        server.sendmail(provider_email, recipient_email, msg.as_string())
        print("Email sent successfully")
        
        server.quit()
        
        return jsonify({
            'success': True,
            'message': 'Test email sent successfully!',
            'details': {
                'from': f"{from_name} <{provider_email}>",
                'to': recipient_email,
                'subject': 'Test Email - Dulles Geotechnical Monitoring System',
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
        }), 200
        
    except smtplib.SMTPAuthenticationError as e:
        print(f'SMTP Authentication Error: {e}')
        return jsonify({
            'success': False,
            'error': 'SMTP Authentication Error',
            'message': 'Failed to authenticate with the email server. Please check your email and app password.',
            'details': str(e)
        }), 401
        
    except smtplib.SMTPException as e:
        print(f'SMTP Error: {e}')
        return jsonify({
            'success': False,
            'error': 'SMTP Error',
            'message': 'An error occurred while sending the email.',
            'details': str(e)
        }), 500
        
    except Exception as e:
        print(f'Failed to send test email: {e}')
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': 'Failed to send test email',
            'message': str(e)
        }), 500