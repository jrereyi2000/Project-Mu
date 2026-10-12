import cv2
import argparse
import os
import shutil
import numpy as np

def run_dedupe_test(video_path: str, threshold: float = 3.0):
    print(f"Starting Smart Deduplication Test on {video_path}...")
    
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error: Could not open video {video_path}")
        return
        
    native_fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"Native Video Specs: {native_fps} FPS | {total_frames} Total Frames")
    
    output_dir = "data/frames/test_output_frames"
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir)
    
    last_saved_gray = None
    saved_count = 0
    frame_idx = 0
    
    while True:
        ret, frame = cap.read()
        if not ret: break
        
        # Convert to grayscale for fast comparison
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        is_unique = False
        if last_saved_gray is None:
            is_unique = True
        else:
            # Fast absolute difference calculation in OpenCV C++ space
            diff = cv2.absdiff(gray, last_saved_gray)
            mean_diff = cv2.mean(diff)[0]
            
            if mean_diff > threshold:
                is_unique = True
                
        if is_unique:
            cv2.imwrite(os.path.join(output_dir, f"frame_{frame_idx:05d}.jpg"), frame)
            last_saved_gray = gray
            saved_count += 1
            
        frame_idx += 1
        if frame_idx % 100 == 0:
            print(f"Processed {frame_idx}/{total_frames} | Kept: {saved_count}", end="\r")
            
    cap.release()
    print(f"\n\n--- RESULTS ---")
    print(f"Original Frames: {total_frames}")
    print(f"Kept Unique Frames: {saved_count}")
    print(f"Reduction Rate: {100 - (saved_count / total_frames * 100):.1f}%")
    print(f"Output folder: ./{output_dir}/")
    print(f"Check the folder visually to ensure no players were skipped!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Roster Frame Deduplication")
    parser.add_argument("--file", type=str, required=True, help="Path to MP4")
    parser.add_argument("--threshold", type=float, default=5.0, help="Mean pixel difference threshold (higher = fewer frames saved)")
    args = parser.parse_args()
    
    run_dedupe_test(args.file, args.threshold)
