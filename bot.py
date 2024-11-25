from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import os
import requests
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth
import boto3
from datetime import datetime

app = Flask(__name__)

# Load environment variables from .env file
load_dotenv()

# Twilio API credentials
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_API_KEY = os.getenv('TWILIO_API_KEY')  # Your Twilio API Key SID
TWILIO_API_SECRET = os.getenv('TWILIO_API_SECRET')  # Your Twilio API Secret
TWILIO_WHATSAPP_NUMBER = os.getenv('TWILIO_WA_NUMBER')  # Your Twilio number

# AWS credentials
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
AWS_SESSION_TOKEN = os.getenv('AWS_SESSION_TOKEN')
AWS_DEFAULT_REGION = os.getenv('AWS_DEFAULT_REGION')

# Initialize Twilio Client using API Key and Secret
client = Client(TWILIO_API_KEY, TWILIO_API_SECRET, TWILIO_ACCOUNT_SID)

# Initialize S3 client
s3_client = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    aws_session_token=AWS_SESSION_TOKEN,
    region_name=AWS_DEFAULT_REGION
)
S3_BUCKET_NAME = 'pcloud-ur'

# Chatbot State Management
user_state = {}

# Define the chatbot logic
@app.route('/whatsapp', methods=['POST'])
def whatsapp_bot():
    incoming_msg = request.values.get('Body', '').strip().lower()
    from_number = request.values.get('From')
    media_url = request.values.get('MediaUrl0')
    response = MessagingResponse()
    msg = response.message()
    print(request.values)

    # Check if the user is new or existing
    if from_number not in user_state:
        user_state[from_number] = {'stage': 'ask_name'}
        msg.body("👋 Hola! Por favor ingresa el nombre del paciente")
    else:
        state = user_state[from_number]

        # Asking for the user's name
        if state['stage'] == 'ask_name':
            user_state[from_number]['name'] = incoming_msg.title()
            user_state[from_number]['stage'] = 'ask_image'
            msg.body(f"Por favor adjunta la imagen para el paciente: {user_state[from_number]['name']}")
        
        # Asking for the image
        elif state['stage'] == 'ask_image':
            if media_url:
                user_state[from_number]['image'] = media_url
                user_state[from_number]['timestamp'] = datetime.now().strftime('%Y%m%d_%H%M%S')
                user_state[from_number]['stage'] = 'ask_description'
                msg.body("Por favor proporciona una descripción de la imagen.")
            else:
                msg.body("Envía la imagen 📷.")
        
        elif state['stage'] == 'ask_description':
            user_state[from_number]['description'] = incoming_msg

            # Attempt to save the image and description with error handling
            save_status = save_image(user_state[from_number]['image'], from_number, user_state[from_number]['description'], user_state[from_number]['timestamp'])

            if save_status:
                msg.body(f"La imagen y la descripción se han guardado correctamente.")
            else:
                msg.body("⚠️ Ha ocurrido un error. Por favor intenta de nuevo.")
            
            # Reset state for this user
            del user_state[from_number]

    return str(response)

def save_image(image_url, from_number, description, timestamp):
    """
    Downloads the image from the URL and saves it to S3 with Basic Auth for Twilio.
    The image file name will be the user's name and the timestamp of the message.
    Returns True if successful, False otherwise.
    """
    try:
        # Get the user's name to generate the filename
        name = user_state.get(from_number, {}).get('name', 'unknown_user').replace(' ', '-').lower()

        # Make a GET request to fetch the image with Twilio Basic HTTP Authentication
        response = requests.get(
            image_url, 
            auth=HTTPBasicAuth(TWILIO_API_KEY, TWILIO_API_SECRET),
            stream=True,
            timeout=10  # Set a timeout for the request
        )
        
        # Raise an exception if the request was unsuccessful
        response.raise_for_status()

        # Check if the response is an image
        content_type = response.headers['Content-Type']
        if 'image' not in content_type:
            print(f"Invalid content type: {content_type}")
            return False

        # Get the file extension from the content type
        extension = content_type.split('/')[-1]
        filename = f"{name}_{timestamp}.{extension}"
        description_filename = f"{name}_{timestamp}.txt"

        # Upload the image to S3
        s3_client.upload_fileobj(response.raw, S3_BUCKET_NAME, filename)

        # Upload the description to S3
        s3_client.put_object(Bucket=S3_BUCKET_NAME, Key=description_filename, Body=description)

        print(f"Image and description saved to S3 as {filename} and {description_filename}")
        return True

    except requests.exceptions.Timeout:
        print("Request timed out while downloading the image.")
        return False
    except requests.exceptions.RequestException as e:
        print(f"Error downloading image: {e}")
        return False
    except IOError as e:
        print(f"File I/O error: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error: {e}")
        return False

if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=5000)