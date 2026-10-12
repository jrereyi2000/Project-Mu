from google import genai

# Initialize the client (ensure GEMINI_API_KEY is set in your environment)
client = genai.Client()
JOB_NAME = "batches/iijgbjf2i67598h3zjbnb8tpsmxvahwtc26b"

# # 1. Upload the JSONL file
# print("Uploading batch file...")
# uploaded_file = client.files.upload(
#     file="data/headshots/batch_headshot_requests.jsonl", 
#     config={
#         'display_name': 'Roster Headshots Input',
#         'mime_type': 'text/plain'
#     }
# )
# print(f"File uploaded successfully as: {uploaded_file.name}")

# # 2. Create the Batch Job
# # Note: Use the exact image model string you used in your make_headshot.py script
# IMAGE_MODEL = "gemini-nano-banana-2.1" 

# print("Submitting batch job...")
# batch_job = client.batches.create(
#     model=IMAGE_MODEL,
#     src=uploaded_file.name,
#     config={
#         'display_name': 'Headshot Batch Generation Job'
#     }
# )

# print(f"Success! Created batch job ID: {batch_job.name}")
# print(f"Current Status: {batch_job.state}")

# 1. Retrieve the current job state
job = client.batches.get(name=JOB_NAME)
print(f"Current Job Status: {job.state.name}")

# 2. If the job is finished, download the output file
if job.state.name == "JOB_STATE_SUCCEEDED":
    # Get the internal reference name of the output file
    result_file_name = job.dest.file_name
    print(f"Job complete! Downloading results from: {result_file_name}")

    # Download the file contents directly into memory
    file_content_bytes = client.files.download(file=result_file_name)
    file_content = file_content_bytes.decode("utf-8")

    # Save the downloaded JSONL file locally
    output_path = "data/headshots/batch_results.jsonl"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(file_content)
    
    print(f"Success! Results saved to {output_path}")
    
elif job.state == "FAILED":
    print("The batch job failed. Check your input formatting.")