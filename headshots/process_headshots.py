#!/usr/bin/env python3
import json
import base64
import io
import os
import sys
import time
import multiprocessing as mp
from queue import Empty
from PIL import Image
import rembg

BATCH_RESULTS = "data/headshots/batch_results.jsonl"
OUTPUT_DIR = "static/players"
NUM_WORKERS = min(6, os.cpu_count() or 4)
MODEL_NAME = "silueta"

def worker_process(input_queue, progress_counter, total_items):
    # Initialize a single session per worker process
    session = rembg.new_session(model_name=MODEL_NAME)
    
    while True:
        try:
            item = input_queue.get(timeout=2)
        except Empty:
            break
        except Exception:
            break
            
        if item is None:  # Sentinel
            break
            
        key, img_b64 = item
        out_path = os.path.join(OUTPUT_DIR, f"{key}.webp")
        
        try:
            img_bytes = base64.b64decode(img_b64)
            img = Image.open(io.BytesIO(img_bytes))
            
            # Remove background using silueta model
            nobg = rembg.remove(img, session=session)
            
            # Save directly as WebP
            nobg.save(out_path, format="WEBP", quality=90, method=4)
        except Exception as e:
            print(f"[Worker Error] Failed processing {key}: {e}", file=sys.stderr)
            
        with progress_counter.get_lock():
            progress_counter.value += 1
            done = progress_counter.value
            if done % 50 == 0 or done == total_items:
                print(f"Progress: {done}/{total_items} ({done*100.0/total_items:.1f}%)", flush=True)

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    if not os.path.exists(BATCH_RESULTS):
        print(f"Error: {BATCH_RESULTS} not found.")
        sys.exit(1)
        
    print(f"Starting batch headshot extraction with rembg ('{MODEL_NAME}') across {NUM_WORKERS} concurrent workers...")
    
    # Check already processed files to enable resuming
    existing_files = set(f.removesuffix(".webp") for f in os.listdir(OUTPUT_DIR) if f.endswith(".webp"))
    print(f"Found {len(existing_files)} already processed files in {OUTPUT_DIR}.")
    
    queue = mp.Queue(maxsize=100)
    progress_counter = mp.Value('i', len(existing_files))
    
    # We first count total lines in file
    print("Reading and queuing items...")
    total_in_batch = 0
    items_to_process = []
    
    with open(BATCH_RESULTS, "r", encoding="utf-8") as f:
        for line in f:
            total_in_batch += 1
            data = json.loads(line)
            key = data.get("key")
            if key in existing_files:
                continue
            candidates = data.get("response", {}).get("candidates", [])
            if not candidates:
                continue
            parts = candidates[0].get("content", {}).get("parts", [])
            inline_part = next((p["inlineData"]["data"] for p in parts if "inlineData" in p), None)
            if inline_part:
                items_to_process.append((key, inline_part))

    print(f"Total in file: {total_in_batch}. Need to process: {len(items_to_process)}.")
    
    if not items_to_process:
        print("All images already processed!")
        return

    # Start workers
    workers = []
    for _ in range(NUM_WORKERS):
        p = mp.Process(target=worker_process, args=(queue, progress_counter, total_in_batch))
        p.start()
        workers.append(p)

    t0 = time.time()
    
    # Feed the queue
    for item in items_to_process:
        queue.put(item)
        
    # Send sentinel for each worker
    for _ in range(NUM_WORKERS):
        queue.put(None)
        
    # Wait for workers to finish
    for p in workers:
        p.join()
        
    elapsed = time.time() - t0
    print(f"\nAll done! Processed {len(items_to_process)} images in {elapsed:.2f}s ({len(items_to_process)/max(elapsed, 1):.2f} img/s).")
    print(f"Total files in {OUTPUT_DIR}: {len(os.listdir(OUTPUT_DIR))}")

if __name__ == "__main__":
    main()
