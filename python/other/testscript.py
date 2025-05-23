#%%
import os

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "omnipate-mobile-416a96733cad.json"

#%%
from google.cloud import aiplatform
aiplatform.init(project="omnipate-mobile", location="us-central1")
#%%
import os
import requests
import google.auth
from google.auth.transport.requests import Request
from google.oauth2 import service_account

# === Step 1: Set up credentials ===
SERVICE_ACCOUNT_FILE = "omnipate-mobile-416a96733cad.json"
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)

credentials.refresh(Request())  # Get access token
token = credentials.token

# === Step 2: Prepare request data ===
project_id = "omnipate-mobile"
model_id = "veo-2.0-generate-001"
region = "us-central1"
endpoint = f"https://us-central1-aiplatform.googleapis.com/v1/projects/{project_id}/locations/{region}/publishers/google/models/{model_id}:predictLongRunning"

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

payload = {
    "instances": [
        {
            "prompt": "A futuristic cityscape at night, neon lights glowing",
            "response_count": 1,
            "duration": 5,
            "storageUri": "gs://my_output_bucket_omnipate/videos/"
        }
    ]
}

# === Step 3: Make API call ===
response = requests.post(endpoint, headers=headers, json=payload)

print("Status Code:", response.status_code)
print("Response JSON:", response.json())
#%%
response_json = response.json()
name = response_json["name"]
print("Operation Name:", name)
# %%
operation_url = f"https://us-central1-aiplatform.googleapis.com/v1/{name}"
print("Operation URL:", operation_url)
response = requests.get(operation_url, headers=headers)
print(response)

# %%
operation_url = "https://us-central1-aiplatform.googleapis.com/v1/projects/omnipate-mobile/locations/us-central1/operations/ead7bacb-2280-49c2-9495-8b41afab7015"
#f"https://us-central1-aiplatform.googleapis.com/v1/{name}"
print("Operation URL:", operation_url)
response = requests.get(operation_url, headers=headers)
print(response)

# %%
requests.get("https://us-central1-aiplatform.googleapis.com/v1/projects/omnipate-mobile/locations/us-central1/publishers/google/models/veo-2.0-generate-001/operations/ead7bacb-2280-49c2-9495-8b41afab7015", headers=headers)
# %%
# %%
import time
from google.cloud import aiplatform

# Assuming aiplatform.init() is already called as in your script:
# aiplatform.init(project="omnipate-mobile", location="us-central1")

# Get the operation name from the previous API call's response


# Initialize the OperationsClient
# The endpoint should match your region (us-central1 in this case)
operations_client = aiplatform.gapic.OperationsClient(
    client_options={"api_endpoint": "us-central1-aiplatform.googleapis.com"}
)

print(f"Video generation operation started. Polling for completion: {name}")

while True:
    operation = operations_client.get_operation(name=name)

    # The metadata contains the state of the operation
    if operation.metadata:
        print(f"Operation status: {operation.metadata.state.name}")
    else:
        print("Operation status: Unknown (metadata not yet available)")

    if operation.done:
        if operation.error:
            print(f"Operation failed: {operation.error.message}")
        elif operation.response:
            print("Operation completed successfully!")
            # The 'response' field will contain the actual result of the video generation.
            # For Veo, this typically includes a list of generated videos,
            # each with a GCS URI where the video is stored.
            print("Response details:")
            try:
                # Assuming the structure is similar to documented Veo responses
                if operation.response.generated_videos:
                    for i, video in enumerate(operation.response.generated_videos):
                        print(f"  Generated Video {i+1} URI: {video.video.uri}")
                else:
                    print("  No generated videos found in response.")
            except AttributeError:
                print(f"  Raw response object: {operation.response}")
        else:
            print("Operation finished but no response or error found.")
        break

    time.sleep(15)  # Wait for 15 seconds before checking status again
print("Polling complete.")
# %%
# %%
import time
import requests

# Assuming 'headers' and 'name' (operation_name) are already defined from your script.
# If not, ensure you extract 'name' from the initial POST response:
# response_json = response.json()
# operation_name = response_json["name"]

# The operation URL is constructed from the 'name' field
operation_url = f"https://us-central1-aiplatform.googleapis.com/v1/{name}"

print(f"Video generation operation started. Polling for completion: {operation_url}")

while True:
    response = requests.get(operation_url, headers=headers)
    print("Status Code:", response.status_code)
    response_json = response.json()

    # Check if the operation is 'done'
    done = response_json.get("done", False)
    
    # Extract status from metadata if available
    metadata = response_json.get("metadata", {})
    state = metadata.get("state", "UNKNOWN")
    print(f"Operation status: {state}")

    if done:
        if "error" in response_json:
            print(f"Operation failed: {response_json['error'].get('message', 'Unknown error')}")
        elif "response" in response_json:
            print("Operation completed successfully!")
            # The 'response' field will contain the actual result of the video generation.
            # You'll need to inspect its structure based on the Veo API documentation.
            # It's likely under a 'value' key if it's a google.protobuf.Any type.
            print("Response details:")
            try:
                # If the response is a protobuf Any type, it might be nested under 'value'
                # and then need decoding. For simplicity here, we print the raw.
                if "response" in response_json:
                    # Depending on how the Veo response is packed, you might find
                    # the generated video details directly here or nested deeper.
                    # As per Veo documentation, look for 'generated_videos' list
                    # in the actual unpacked response.
                    print(response_json["response"])
                else:
                    print("  No specific response payload found.")
            except Exception as e:
                print(f"  Error parsing response: {e}")
                print(f"  Raw response payload: {response_json.get('response')}")
        else:
            print("Operation finished but no response or error found.")
        break

    time.sleep(15)  # Wait for 15 seconds before checking status again
print("Polling complete.")
# %%
