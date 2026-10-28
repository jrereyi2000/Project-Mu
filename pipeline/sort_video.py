# @AUDIT-STATUS: PRUNE_FILE (v0)
# @REPLACED-BY: pipeline/save_reader/pc_save_reader.mjs
import cv2
import os
import argparse
from pipeline.template_classifier import TemplateClassifier, TEMPLATE_DIR
from pipeline.ingest_stream import get_stream_url
import time
import threading

class VideoCaptureThread(threading.Thread):
    def __init__(self, source: str):
        super().__init__()
        self.cap = cv2.VideoCapture(source)
        if not self.cap.isOpened(): raise ValueError(f"Failed to open source {source}")
        self.latest_frame = None
        self.ret = False
        self.running = True
        
    def run(self):
        while self.running:
            self.ret, frame = self.cap.read()
            if not self.ret:
                self.running = False
                break
            self.latest_frame = frame
                
    def stop(self):
        self.running = False
        self.cap.release()


# The known primary states. If a template file is named IN_GAMEPLAY_1.jpg, 
# it will be grouped into the IN_GAMEPLAY folder.
KNOWN_STATES = ["IN_GAMEPLAY", "BOX_SCORE", "LEAGUE_STANDINGS", "HIGHLIGHTS_RECAP"]

def get_base_state(detected_state: str) -> str:
    """Groups variations of a state (e.g., IN_GAMEPLAY_1) into the base state."""
    if detected_state == "UNKNOWN":
        return "UNKNOWN"
        
    for ks in KNOWN_STATES:
        if detected_state.startswith(ks):
            return ks
    return detected_state

def sort_video_frames(source: str, classifier: TemplateClassifier, base_output_dir: str, is_live: bool = False):
    os.makedirs(base_output_dir, exist_ok=True)
    saved_count = 0
    frame_idx = 0
    
    if is_live:
        print("Starting LIVE video sort using background frame reader to prevent latency...")
        video_thread = VideoCaptureThread(source)
        video_thread.start()
        
        try:
            while video_thread.running:
                frame = video_thread.latest_frame
                if frame is None:
                    time.sleep(0.1)
                    continue
                    
                # We want 1 FPS, so sleep for 1 second between processing frames
                start_time = time.time()
                
                raw_state, score = classifier.detect_ui_state(frame)
                base_state = get_base_state(raw_state)
                
                state_dir = os.path.join(base_output_dir, base_state)
                os.makedirs(state_dir, exist_ok=True)
                
                filename = os.path.join(state_dir, f"frame_{saved_count:05d}.jpg")
                cv2.imwrite(filename, frame)
                print(f"Saved {filename} | Detected: {base_state} (Score: {score:.2f})")
                saved_count += 1
                
                elapsed = time.time() - start_time
                if elapsed < 1.0:
                    time.sleep(1.0 - elapsed)
                    
        except KeyboardInterrupt:
            print("Sorting stopped by user.")
        finally:
            video_thread.stop()
            video_thread.join()
            print(f"Finished sorting {saved_count} frames.")
            
    else:
        # Offline File Processing
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            print(f"Failed to open video source: {source}")
            return
            
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0 or not fps: fps = 30.0
        frames_to_skip = int(fps)
        
        print(f"Starting video sort. Detected FPS: {fps}")
        try:
            while True:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, frame = cap.read()
                if not ret: break
                
                raw_state, score = classifier.detect_ui_state(frame)
                base_state = get_base_state(raw_state)
                
                state_dir = os.path.join(base_output_dir, base_state)
                os.makedirs(state_dir, exist_ok=True)
                
                filename = os.path.join(state_dir, f"frame_{saved_count:05d}.jpg")
                cv2.imwrite(filename, frame)
                print(f"Saved {filename} | Detected: {base_state} (Score: {score:.2f})")
                
                saved_count += 1
                frame_idx += frames_to_skip
                
        except KeyboardInterrupt:
            print("Sorting stopped by user.")
        finally:
            cap.release()
            print(f"Finished sorting {saved_count} frames.")

def main():
    parser = argparse.ArgumentParser(description="Sorts a video into subdirectories based on UI state templates.")
    parser.add_argument("--file", type=str, help="Local video file path to sort")
    parser.add_argument("--url", type=str, help="Twitch URL to sort live")
    parser.add_argument("--templates", type=str, default=TEMPLATE_DIR, help="Directory containing template images")
    parser.add_argument("--output-dir", type=str, default="data/frames/sorted_frames", help="Base directory to save sorted frames")
    parser.add_argument("--threshold", type=float, default=0.8, help="Confidence threshold (0.0 to 1.0)")

    args = parser.parse_args()

    # Initialize the classifier
    classifier = TemplateClassifier(template_dir=args.templates, threshold=args.threshold)

    if args.url:
        print(f"Resolving stream URL for {args.url}...")
        stream_url = get_stream_url(args.url)
        if stream_url:
            sort_video_frames(stream_url, classifier, args.output_dir, is_live=True)
    elif args.file:
        sort_video_frames(args.file, classifier, args.output_dir, is_live=False)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
