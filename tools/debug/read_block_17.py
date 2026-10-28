# @AUDIT-STATUS: PRUNE_FILE (v1)
# @REPLACED-BY: NONE
from PIL import Image
import os
from google import genai
img = Image.open("data/frames/test_output_frames/panthers_block_17.jpg")
prompt = "Look at this image. This is a Madden stats screen. Does this screen show ANY numbers greater than 0 for the players? Read the first player's row and tell me the numbers."
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
response = client.models.generate_content(
    model='gemini-2.5-flash',
    contents=[img, prompt]
)
print(response.text)
