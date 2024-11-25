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

# DynamoDB table name
DYNAMODB_TABLE_NAME = os.getenv('DYNAMODB_TABLE_NAME')

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

# Initialize DynamoDB client
dynamodb = boto3.resource(
    'dynamodb',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    aws_session_token=AWS_SESSION_TOKEN,
    region_name=AWS_DEFAULT_REGION
)
table = dynamodb.Table(DYNAMODB_TABLE_NAME)
print("Connected to DynamoDB")

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

    print(f"Incoming message from {from_number}: {incoming_msg}")
    print(f"Current state: {user_state.get(from_number, {}).get('stage')}")

    if from_number not in user_state:
        user_state[from_number] = {'stage': 'ask_id'}
        msg.body("👋 Hola! Por favor ingresa el ID del paciente")
    else:
        state = user_state[from_number]

        if state['stage'] == 'ask_id':
            user_state[from_number]['id'] = incoming_msg
            user_state[from_number]['stage'] = 'ask_image'
            msg.body(f"Por favor adjunta la imagen para el paciente con ID: {user_state[from_number]['id']}")
        
        elif state['stage'] == 'ask_image':
            if media_url:
                user_state[from_number]['image'] = media_url
                user_state[from_number]['timestamp'] = datetime.now().strftime('%Y%m%d_%H%M%S')
                user_state[from_number]['stage'] = 'ask_description'
                msg.body("Por favor proporciona una descripción de la imagen.")
            else:
                msg.body("Envía la imagen 📷.")
        
        elif state['stage'] == 'ask_description':
            print("Processing description stage")
            user_state[from_number]['description'] = incoming_msg

            # Attempt to save the image and description with error handling
            save_status, image_url = save_image(user_state[from_number]['image'], from_number, user_state[from_number]['description'], user_state[from_number]['timestamp'])

            if save_status:
                msg.body(f"La imagen y la descripción se han guardado correctamente.\nImagen: {image_url}")
            else:
                msg.body("⚠️ Ha ocurrido un error. Por favor intenta de nuevo.")
            
            # Reset state for this user
            del user_state[from_number]

    print("Returning response to Twilio")
    return str(response)

def save_image(image_url, from_number, description, timestamp):
    """
    Downloads the image from the URL and saves it to S3 with Basic Auth for Twilio.
    The image file name will be the patient's ID and the timestamp of the message.
    Returns True if successful, False otherwise.
    """
    try:
        # Get the patient's ID to generate the filename
        patient_id = user_state.get(from_number, {}).get('id', 'unknown_id').replace(' ', '-').lower()

        # Make a GET request to fetch the image with Twilio Basic HTTP Authentication
        response = requests.get(
            image_url, 
            auth=HTTPBasicAuth(TWILIO_API_KEY, TWILIO_API_SECRET),
            stream=True,
        )

        # Check if the request was successful
        response.raise_for_status()

        # Get the content type of the response
        content_type = response.headers.get('Content-Type')
        if not content_type or 'image' not in content_type:
            print(f"Invalid content type: {content_type}")
            return False, None

        # Get the file extension from the content type
        extension = content_type.split('/')[-1]
        filename = f"{patient_id}_{timestamp}.{extension}"

        # Upload the image to S3 with the correct Content-Type and public-read ACL
        s3_client.upload_fileobj(
            response.raw, 
            S3_BUCKET_NAME, 
            filename,
            ExtraArgs={'ContentType': content_type, 'ACL': 'public-read'}
        )

        # Generate public URL for the uploaded image
        image_url = f"https://{S3_BUCKET_NAME}.s3.amazonaws.com/{filename}"

        # Save image URL and description to DynamoDB
        document = {
            'user_id': from_number,  # Assuming 'user_id' is the primary key in DynamoDB
            'patient_id': patient_id,
            'timestamp': timestamp,
            'image_url': image_url,
            'description': description
        }
        print(f"Saving document to DynamoDB: {document}")
        table.put_item(Item=document)
        print("Document saved to DynamoDB")

        print(f"Image saved to S3 as {filename}")
        print(f"Generated public URL: Image URL: {image_url}")
        return True, image_url

    except requests.exceptions.Timeout:
        print("Request timed out while downloading the image.")
        return False, None
    except requests.exceptions.RequestException as e:
        print(f"Error downloading image: {e}")
        return False, None
    except IOError as e:
        print(f"File I/O error: {e}")
        return False, None
    except Exception as e:
        print(f"Unexpected error: {e}")
        return False, None

if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=5000)