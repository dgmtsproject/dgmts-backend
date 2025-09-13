import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import Config

def send_email(to_email, subject, body):
    """Send email using Gmail SMTP"""
    try:
        msg = MIMEMultipart()
        # Accept both string and list for to_email
        if isinstance(to_email, str):
            recipients = [email.strip() for email in to_email.split(',') if email.strip()]
        else:
            recipients = to_email

        msg['From'] = Config.EMAIL_USERNAME
        msg['To'] = ", ".join(recipients)
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))

        print(f"Attempting to send email to {recipients}")
        print(f"SMTP Server: {Config.SMTP_SERVER}:{Config.SMTP_PORT}")
        print(f"Username: {Config.EMAIL_USERNAME}")
        
        server = smtplib.SMTP_SSL(Config.SMTP_SERVER, Config.SMTP_PORT)
        print("SMTP SSL connection established")
        
        server.login(Config.EMAIL_USERNAME, Config.EMAIL_PASSWORD)
        print("Login successful")
        
        server.sendmail(Config.EMAIL_USERNAME, recipients, msg.as_string())
        print("Email sent successfully")
        
        server.quit()
        return True
    except smtplib.SMTPAuthenticationError as e:
        print(f'SMTP Authentication Error: {e}')
        return False
    except smtplib.SMTPException as e:
        print(f'SMTP Error: {e}')
        return False
    except Exception as e:
        print(f'Failed to send email: {e}')
        return False
